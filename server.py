# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "fastapi>=0.115",
#   "uvicorn>=0.34",
#   "google-auth>=2.38",
#   "google-auth-oauthlib>=1.2",
#   "google-api-python-client>=2.166",
#   "python-dotenv>=1.0",
# ]
# ///
"""Google Calendar OAuth2 + event sync backend.

Run standalone for testing:
  uv run server.py

Normally started by widget.py automatically.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build as gclient_build
from pydantic import BaseModel

load_dotenv()

_BUILD_DIR = Path(__file__).parent / "build"
_TOKEN_FILE = Path(__file__).parent / ".tokens.json"
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

app = FastAPI()


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
    return os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8765/api/auth/callback")


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


def _load_tokens() -> dict[str, Any] | None:
    if not _TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(_TOKEN_FILE.read_text())
        return data if data else None
    except Exception:
        return None


def _save_tokens(token: str | None, refresh_token: str | None, expiry: str | None) -> None:
    existing = _load_tokens() or {}
    updated: dict[str, Any] = {**existing}
    if token is not None:
        updated["token"] = token
    if refresh_token is not None:
        updated["refresh_token"] = refresh_token
    if expiry is not None:
        updated["expiry"] = expiry
    _TOKEN_FILE.write_text(json.dumps(updated, indent=2))


def _get_credentials() -> Credentials | None:
    tokens = _load_tokens()
    if not tokens or (not tokens.get("token") and not tokens.get("refresh_token")):
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
        client_id=_client_id(),
        client_secret=_client_secret(),
        scopes=_SCOPES,
        expiry=expiry,
    )

    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        _save_tokens(
            token=creds.token,
            refresh_token=creds.refresh_token,
            expiry=creds.expiry.isoformat() if creds.expiry else None,
        )

    return creds


def _require_creds() -> Credentials:
    creds = _get_credentials()
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return creds


def _map_event(ev: dict[str, Any]) -> dict[str, str]:
    start = ev.get("start", {})
    end = ev.get("end", {})
    return {
        "id": ev["id"],
        "title": ev.get("summary", "(no title)"),
        "start": start.get("dateTime") or f"{start.get('date')}T00:00:00",
        "end": end.get("dateTime") or f"{end.get('date')}T00:00:00",
    }


# Auth routes

@app.get("/api/auth/google")
def auth_google() -> RedirectResponse:
    flow = Flow.from_client_config(_client_config(), scopes=_SCOPES, redirect_uri=_redirect_uri())
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return RedirectResponse(url)


@app.get("/api/auth/callback")
def auth_callback(code: str) -> RedirectResponse:
    flow = Flow.from_client_config(_client_config(), scopes=_SCOPES, redirect_uri=_redirect_uri())
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_tokens(
        token=creds.token,
        refresh_token=creds.refresh_token,
        expiry=creds.expiry.isoformat() if creds.expiry else None,
    )
    return RedirectResponse("/")


@app.get("/api/auth/status")
def auth_status() -> dict[str, bool]:
    tokens = _load_tokens()
    return {"authenticated": bool(tokens and (tokens.get("token") or tokens.get("refresh_token")))}


# Event routes

@app.get("/api/events")
def list_events() -> list[dict[str, str]]:
    creds = _require_creds()
    svc = gclient_build("calendar", "v3", credentials=creds)

    now = datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
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

    return [
        _map_event(ev)
        for ev in result.get("items", [])
        if ev.get("id") and ev.get("start")
    ]


class EventBody(BaseModel):
    """Payload for creating a new event."""

    title: str
    start: str
    end: str


@app.post("/api/events", status_code=201)
def create_event(body: EventBody) -> dict[str, str]:
    creds = _require_creds()
    svc = gclient_build("calendar", "v3", credentials=creds)
    result = (
        svc.events()
        .insert(
            calendarId=_calendar_id(),
            body={
                "summary": body.title,
                "start": {"dateTime": body.start},
                "end": {"dateTime": body.end},
            },
        )
        .execute()
    )
    return _map_event(result)


class EventPatch(BaseModel):
    """Payload for updating an existing event."""

    title: str | None = None
    start: str | None = None
    end: str | None = None


@app.patch("/api/events/{event_id}")
def update_event(event_id: str, body: EventPatch) -> dict[str, str]:
    creds = _require_creds()
    svc = gclient_build("calendar", "v3", credentials=creds)

    patch: dict[str, Any] = {}
    if body.title is not None:
        patch["summary"] = body.title
    if body.start is not None:
        patch["start"] = {"dateTime": body.start}
    if body.end is not None:
        patch["end"] = {"dateTime": body.end}

    result = (
        svc.events()
        .patch(calendarId=_calendar_id(), eventId=event_id, body=patch)
        .execute()
    )
    return _map_event(result)


@app.delete("/api/events/{event_id}", status_code=204)
def delete_event(event_id: str) -> None:
    creds = _require_creds()
    svc = gclient_build("calendar", "v3", credentials=creds)
    svc.events().delete(calendarId=_calendar_id(), eventId=event_id).execute()


# SPA static file serving — must come last so API routes take priority

@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str) -> FileResponse:
    candidate = _BUILD_DIR / full_path if full_path else _BUILD_DIR / "index.html"
    if full_path and candidate.is_file():
        return FileResponse(str(candidate))
    return FileResponse(str(_BUILD_DIR / "index.html"))


def run(port: int = 8765) -> None:
    """Start the uvicorn server. Called by widget.py."""
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    run()
