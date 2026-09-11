"""Google Calendar OAuth2 + event sync backend.

Run standalone for testing:
  uv run server.py

Normally started by widget.py automatically.

Dependencies are declared in pyproject.toml (installed via uv).
"""

import json
import os
import secrets
import socket
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build as gclient_build
from loguru import logger
from pydantic import BaseModel, model_validator

load_dotenv()

_LOG_FILE = Path(__file__).parent / "log" / "server.log"
_BUILD_DIR = Path(__file__).parent / "build"
_TOKEN_FILE = Path(__file__).parent / ".tokens.json"
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# The API is a local-only, unauthenticated-by-design service for the widget.
# Pinning the Host header stops a DNS-rebinding page from reaching it.
_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Short-lived, single-use OAuth state store (login-CSRF guard).
_OAUTH_STATE_TTL = timedelta(minutes=10)
_oauth_states: dict[str, datetime] = {}
_oauth_lock = threading.Lock()

# FastAPI runs these sync routes in a threadpool, so a background poll racing
# a user action could otherwise lose an already-rotated refresh token.
_token_lock = threading.RLock()

# Docs are disabled: this API carries the user's real Google credentials and
# has no business being browsable from the local machine.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)


def _client_id() -> str:
    v = os.getenv("GOOGLE_CLIENT_ID", "")
    if not v:
        raise RuntimeError("GOOGLE_CLIENT_ID not set — copy .env.example to .env and fill it in")
    return v


def _client_secret() -> str:
    v = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not v:
        raise RuntimeError("GOOGLE_CLIENT_SECRET not set")
    return v


def _redirect_uri() -> str:
    return os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8764/api/auth/callback")


def _calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID", "primary")


def _client_config() -> dict[str, Any]:
    return {
        "web": {
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }


def _chmod_private(path: Path) -> None:
    """Best-effort owner-only permissions on a file holding OAuth tokens."""
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.debug(f"Could not restrict permissions on {path.name}: {exc}")


def _load_tokens() -> dict[str, Any] | None:
    with _token_lock:
        if not _TOKEN_FILE.exists():
            logger.debug(f"Token file not found at {_TOKEN_FILE} — not yet authenticated")
            return None
        try:
            data = json.loads(_TOKEN_FILE.read_text())
            if data:
                logger.debug("Token file loaded")
            else:
                logger.debug("Token file is empty")
            return data if data else None
        except Exception as exc:
            logger.warning(f"Failed to read token file: {exc}")
            return None


def _save_tokens(token: str | None, refresh_token: str | None, expiry: str | None) -> None:
    with _token_lock:
        existing = _load_tokens() or {}
        updated: dict[str, Any] = {**existing}
        if token is not None:
            updated["token"] = token
        if refresh_token is not None:
            updated["refresh_token"] = refresh_token
        if expiry is not None:
            updated["expiry"] = expiry
        # Atomic write so a crash mid-save can't corrupt the token file
        tmp = _TOKEN_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(updated, indent=2))
        _chmod_private(tmp)
        tmp.replace(_TOKEN_FILE)
        _chmod_private(_TOKEN_FILE)


def _get_credentials() -> Credentials | None:
    tokens = _load_tokens()
    if not tokens or (not tokens.get("token") and not tokens.get("refresh_token")):
        logger.debug("No usable tokens — credentials unavailable")
        return None

    try:
        client_id = _client_id()
        client_secret = _client_secret()
    except RuntimeError as exc:
        logger.error(f"Cannot build credentials — {exc}")
        return None

    expiry: datetime | None = None
    if tokens.get("expiry"):
        try:
            expiry = datetime.fromisoformat(tokens["expiry"])
        except ValueError:
            pass

    creds = Credentials(
        token=tokens.get("token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=_SCOPES,
        expiry=expiry,
    )

    if not creds.valid and creds.refresh_token:
        logger.info("Access token expired — refreshing via refresh token")
        try:
            creds.refresh(Request())
            _save_tokens(
                token=creds.token,
                refresh_token=creds.refresh_token,
                expiry=creds.expiry.isoformat() if creds.expiry else None,
            )
            logger.info("Credentials refreshed and saved")
        except Exception as exc:
            logger.error(f"Credential refresh failed: {exc}")
            return None

    if not creds.valid:
        # No refresh token, or the stored token is unusable. Returning None
        # makes callers answer 401 so the UI shows the reconnect prompt,
        # instead of handing invalid creds to Google and 500ing.
        logger.warning("Stored credentials are invalid and cannot be refreshed — re-authenticate")
        return None

    return creds


def _require_creds() -> Credentials:
    creds = _get_credentials()
    if creds is None:
        logger.warning("API request rejected — user not authenticated (visit /api/auth/google)")
        raise HTTPException(status_code=401, detail="Not authenticated")
    return creds


def _iso_from_block(block: dict[str, Any]) -> str | None:
    """RFC3339-ish string from a Google `start`/`end` block, or None if empty."""
    date_time = block.get("dateTime")
    if date_time:
        return date_time
    date = block.get("date")
    return f"{date}T00:00:00" if date else None


def _map_event(ev: dict[str, Any]) -> dict[str, Any]:
    start = ev.get("start") or {}
    end = ev.get("end") or {}
    start_iso = _iso_from_block(start)
    end_iso = _iso_from_block(end)
    if start_iso is None or end_iso is None:
        raise ValueError(f"event {ev.get('id')!r} has no usable start/end")
    return {
        "id": ev["id"],
        "title": ev.get("summary") or "(no title)",
        "start": start_iso,
        "end": end_iso,
        "allDay": "date" in start,
    }


def _new_oauth_state() -> str:
    """Issue a single-use OAuth `state` and record it server-side."""
    state = secrets.token_urlsafe(32)
    now = datetime.now()
    with _oauth_lock:
        for stale, issued in list(_oauth_states.items()):
            if now - issued > _OAUTH_STATE_TTL:
                _oauth_states.pop(stale, None)
        _oauth_states[state] = now
    return state


def _consume_oauth_state(state: str | None) -> bool:
    """Validate and invalidate a state issued by /api/auth/google."""
    if not state:
        return False
    with _oauth_lock:
        issued = _oauth_states.pop(state, None)
    if issued is None:
        return False
    return datetime.now() - issued <= _OAUTH_STATE_TTL


# Auth routes

@app.get("/api/auth/google")
def auth_google() -> RedirectResponse:
    try:
        config = _client_config()
    except RuntimeError as exc:
        logger.error(f"Cannot start OAuth flow — {exc}")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    state = _new_oauth_state()
    logger.info("OAuth flow initiated — redirecting to Google")
    flow = Flow.from_client_config(config, scopes=_SCOPES, redirect_uri=_redirect_uri())
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)
    return RedirectResponse(url)


@app.get("/api/auth/callback")
def auth_callback(code: str, state: str | None = None) -> RedirectResponse:
    if not _consume_oauth_state(state):
        logger.warning("OAuth callback rejected — missing, unknown, or expired state")
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth state — restart the login flow",
        )

    try:
        config = _client_config()
    except RuntimeError as exc:
        logger.error(f"OAuth callback failed — {exc}")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    logger.info("OAuth callback received — exchanging code for tokens")
    flow = Flow.from_client_config(config, scopes=_SCOPES, redirect_uri=_redirect_uri())
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_tokens(
        token=creds.token,
        refresh_token=creds.refresh_token,
        expiry=creds.expiry.isoformat() if creds.expiry else None,
    )
    logger.info("Authentication successful — tokens saved")
    return RedirectResponse("/")


@app.get("/api/auth/status")
def auth_status() -> dict[str, bool]:
    tokens = _load_tokens()
    authenticated = bool(tokens and (tokens.get("token") or tokens.get("refresh_token")))
    logger.debug(f"Auth status check: authenticated={authenticated}")
    return {"authenticated": authenticated}


# Event routes

@app.get("/api/events")
def list_events() -> list[dict[str, Any]]:
    logger.info("Fetching today's calendar events")
    creds = _require_creds()
    svc = gclient_build("calendar", "v3", credentials=creds)

    # Day boundaries in the machine's local timezone, not UTC — otherwise
    # the window drifts by hours for anyone west/east of Greenwich
    now = datetime.now().astimezone()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    result = (
        svc.events()
        .list(
            calendarId=_calendar_id(),
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events: list[dict[str, Any]] = []
    for ev in result.get("items", []):
        if not ev.get("id") or not ev.get("start"):
            continue
        try:
            events.append(_map_event(ev))
        except ValueError as exc:
            logger.warning(f"Skipping malformed event: {exc}")
    logger.info(f"Fetched {len(events)} event(s) for {day_start.date()}")
    return events


class EventBody(BaseModel):
    """Payload for creating a new event."""

    title: str
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _check(self) -> "EventBody":
        if not self.title.strip():
            raise ValueError("title must not be empty")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class EventPatch(BaseModel):
    """Payload for updating an existing event. At least one field is required."""

    title: str | None = None
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> "EventPatch":
        if self.title is None and self.start is None and self.end is None:
            raise ValueError("patch must include at least one of title, start, end")
        if self.title is not None and not self.title.strip():
            raise ValueError("title must not be empty")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("end must be after start")
        return self


@app.post("/api/events", status_code=201)
def create_event(body: EventBody) -> dict[str, Any]:
    logger.info(f"Creating event: {body.title!r} ({body.start} -> {body.end})")
    creds = _require_creds()
    svc = gclient_build("calendar", "v3", credentials=creds)
    result = (
        svc.events()
        .insert(
            calendarId=_calendar_id(),
            body={
                "summary": body.title,
                "start": {"dateTime": body.start.isoformat()},
                "end": {"dateTime": body.end.isoformat()},
            },
        )
        .execute()
    )
    logger.info(f"Event created: id={result.get('id')}")
    return _map_event(result)


@app.patch("/api/events/{event_id}")
def update_event(event_id: str, body: EventPatch) -> dict[str, Any]:
    logger.info(f"Updating event {event_id}")
    creds = _require_creds()
    svc = gclient_build("calendar", "v3", credentials=creds)

    patch: dict[str, Any] = {}
    if body.title is not None:
        patch["summary"] = body.title
    if body.start is not None:
        patch["start"] = {"dateTime": body.start.isoformat()}
    if body.end is not None:
        patch["end"] = {"dateTime": body.end.isoformat()}

    result = (
        svc.events()
        .patch(calendarId=_calendar_id(), eventId=event_id, body=patch)
        .execute()
    )
    logger.debug(f"Event {event_id} updated")
    return _map_event(result)


@app.delete("/api/events/{event_id}", status_code=204)
def delete_event(event_id: str) -> None:
    logger.info(f"Deleting event {event_id}")
    creds = _require_creds()
    svc = gclient_build("calendar", "v3", credentials=creds)
    svc.events().delete(calendarId=_calendar_id(), eventId=event_id).execute()
    logger.debug(f"Event {event_id} deleted")


# SPA static file serving — must come last so API routes take priority

@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str) -> FileResponse:
    build_root = _BUILD_DIR.resolve()
    index = build_root / "index.html"
    if not full_path:
        return FileResponse(str(index))
    # Path traversal guard: `full_path` is attacker-controlled, so anything
    # that escapes build/ (e.g. /%2e%2e%2f.env) falls back to the SPA shell
    # rather than serving arbitrary files off disk.
    candidate = (build_root / full_path).resolve()
    if candidate.is_relative_to(build_root) and candidate.is_file():
        return FileResponse(str(candidate))
    return FileResponse(str(index))


def find_free_port(preferred: int = 8764) -> int:
    """Return `preferred` if free, otherwise scan upward for the first free port."""
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found near {preferred}")


def create_server(port: int):
    """Build (but don't start) a uvicorn server. Returns the handle so callers
    can request a graceful shutdown via `server.should_exit = True`."""
    import uvicorn

    redirect_port = urlparse(_redirect_uri()).port
    if redirect_port is not None and redirect_port != port:
        logger.warning(
            f"Server will run on port {port} but GOOGLE_REDIRECT_URI points at "
            f"port {redirect_port} — the OAuth login flow may fail until tokens "
            "exist or GOOGLE_REDIRECT_URI is updated"
        )
    return uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )


def run(server) -> None:
    """Start the given uvicorn server. Blocks until shutdown. Called by widget.py."""
    logger.info(f"API server starting on http://127.0.0.1:{server.config.port}")

    try:
        _client_id()
        _client_secret()
        logger.info("Google OAuth credentials found in environment")
    except RuntimeError as exc:
        logger.error(f"Missing Google OAuth config: {exc}")

    server.run()


if __name__ == "__main__":
    logger.add(_LOG_FILE, rotation="10 MB", retention="1 week", level="DEBUG", encoding="utf-8")
    run(create_server(find_free_port()))
