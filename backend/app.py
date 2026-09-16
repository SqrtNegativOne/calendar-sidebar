"""FastAPI application factory.

The app never constructs a provider itself — the registry is injected by the
composition root (:mod:`server`). Routes depend on :func:`get_registry`, so the
same app can be assembled with Google only, Microsoft only, both, or fakes in
tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from loguru import logger
from pydantic import BaseModel, model_validator

from backend.auth import OAuthStateStore
from backend.provider import (
    CalendarProvider,
    ProviderAuthError,
    ProviderBundle,
    ProviderError,
    ProviderScopeError,
)
from backend.registry import ProviderRegistry

# The API is a local-only, unauthenticated-by-design service for the widget.
# Pinning the Host header stops a DNS-rebinding page from reaching it.
_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

_CONNECTED_PAGE = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Calendar Sidebar</title></head>"
    "<body style=\"margin:0;height:100vh;display:flex;align-items:center;"
    "justify-content:center;background:#111;color:#eee;"
    "font-family:system-ui,sans-serif\">"
    "<div style=\"text-align:center\">"
    "<h2>Calendar Sidebar connected</h2>"
    "<p>You can close this tab and return to the app.</p>"
    "</div></body></html>"
)


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def get_oauth_states(request: Request) -> OAuthStateStore:
    return request.app.state.oauth_states


RegistryDep = Annotated[ProviderRegistry, Depends(get_registry)]
StatesDep = Annotated[OAuthStateStore, Depends(get_oauth_states)]


class EventCreate(BaseModel):
    """Payload for creating a new event."""

    title: str
    start: datetime
    end: datetime
    provider: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "EventCreate":
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


def _bundle_or_404(registry: ProviderRegistry, provider_id: str) -> ProviderBundle:
    bundle = registry.find(provider_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider {provider_id!r}")
    return bundle


def _require_provider(
    registry: ProviderRegistry, provider_id: str | None
) -> ProviderBundle:
    """Resolve the target provider, defaulting to the first authenticated one."""
    if provider_id:
        bundle = _bundle_or_404(registry, provider_id)
        if not bundle.auth.is_authenticated():
            raise HTTPException(
                status_code=401, detail=f"{bundle.auth.display_name} is not connected"
            )
        return bundle
    authenticated = registry.authenticated()
    if not authenticated:
        logger.warning("API request rejected — no provider authenticated")
        raise HTTPException(status_code=401, detail="Not authenticated")
    return authenticated[0]


def _authorization_url(
    registry: ProviderRegistry, states: OAuthStateStore, provider_id: str
) -> str:
    bundle = _bundle_or_404(registry, provider_id)
    if not bundle.auth.configured:
        logger.error(f"Cannot start OAuth flow — {provider_id} is not configured")
        raise HTTPException(
            status_code=503,
            detail=f"{bundle.auth.display_name} is not configured",
        )
    state = states.issue(provider_id)
    try:
        return bundle.auth.authorization_url(state)
    except RuntimeError as exc:
        logger.error(f"Cannot start OAuth flow — {exc}")
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _complete_login(
    registry: ProviderRegistry, provider_id: str, code: str
) -> HTMLResponse:
    bundle = _bundle_or_404(registry, provider_id)
    logger.info(f"{bundle.auth.display_name} callback received — exchanging code")
    try:
        bundle.auth.exchange_code(code)
    except ProviderScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error(f"OAuth callback failed — {exc}")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return HTMLResponse(_CONNECTED_PAGE)


def create_app(registry: ProviderRegistry, *, build_dir: Path | None = None) -> FastAPI:
    states = OAuthStateStore()
    build_root = (build_dir or Path(__file__).resolve().parent.parent / "build").resolve()

    # Docs are disabled: this API carries the user's real credentials and has
    # no business being browsable from the local machine.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.registry = registry
    app.state.oauth_states = states
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)

    # ---------------------------------------------------------------- auth --

    @app.get("/api/auth/status")
    def auth_status(registry: RegistryDep) -> dict[str, Any]:
        providers = registry.status()
        authenticated = any(bool(p["authenticated"]) for p in providers)
        logger.debug(f"Auth status check: authenticated={authenticated}")
        return {"authenticated": authenticated, "providers": providers}

    # NOTE: the literal /api/auth/callback must be registered before the
    # dynamic /api/auth/{provider_id} route, otherwise Starlette matches
    # "callback" as a provider id.
    @app.get("/api/auth/callback")
    def auth_callback(
        code: str,
        registry: RegistryDep,
        states: StatesDep,
        state: str | None = None,
    ) -> HTMLResponse:
        # Legacy single-provider path (registered as Google's redirect URI);
        # the state carries the provider that issued it.
        provider_id = states.consume(state)
        if provider_id is None:
            logger.warning("OAuth callback rejected — missing, unknown, or expired state")
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired OAuth state — restart the login flow",
            )
        return _complete_login(registry, provider_id, code)

    @app.get("/api/auth/{provider_id}")
    def auth_redirect(
        provider_id: str, registry: RegistryDep, states: StatesDep
    ) -> RedirectResponse:
        logger.info(f"OAuth flow initiated for {provider_id} — redirecting")
        url = _authorization_url(registry, states, provider_id)
        return RedirectResponse(url)

    @app.get("/api/auth/{provider_id}/url")
    def auth_url(
        provider_id: str, registry: RegistryDep, states: StatesDep
    ) -> dict[str, str]:
        """Consent URL for the widget to copy to the clipboard.

        Google rejects OAuth inside the embedded WebEngine, so the user pastes
        this into a real browser and the widget polls /api/auth/status.
        """
        url = _authorization_url(registry, states, provider_id)
        logger.info(f"Generated {provider_id} consent URL for manual sign-in")
        return {"url": url}

    @app.get("/api/auth/{provider_id}/callback")
    def provider_callback(
        provider_id: str,
        code: str,
        registry: RegistryDep,
        states: StatesDep,
        state: str | None = None,
    ) -> HTMLResponse:
        issued = states.consume(state)
        if issued is None or issued != provider_id:
            logger.warning(
                "OAuth callback rejected — missing, unknown, expired, or "
                "cross-provider state"
            )
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired OAuth state — restart the login flow",
            )
        return _complete_login(registry, provider_id, code)

    # -------------------------------------------------------------- events --

    @app.get("/api/events")
    def list_events(registry: RegistryDep) -> list[dict[str, Any]]:
        if not registry.has_any_authenticated():
            logger.warning("API request rejected — no provider authenticated")
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Day boundaries in the machine's local timezone, not UTC — otherwise
        # the window drifts by hours for anyone west/east of Greenwich.
        now = datetime.now().astimezone()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        result = registry.list_events(day_start, day_end)
        if result.all_failed:
            if result.auth_failures and result.auth_failures == set(result.failures):
                raise HTTPException(
                    status_code=401, detail="Reconnect required to read calendars"
                )
            raise HTTPException(
                status_code=502,
                detail="; ".join(
                    f"{pid}: {msg}" for pid, msg in result.failures.items()
                ),
            )
        logger.info(
            f"Fetched {len(result.events)} event(s) across "
            f"{len(registry.authenticated())} provider(s) for {day_start.date()}"
        )
        return [event.to_dict() for event in result.events]

    @app.post("/api/events", status_code=201)
    def create_event(body: EventCreate, registry: RegistryDep) -> dict[str, Any]:
        bundle = _require_provider(registry, body.provider)
        provider: CalendarProvider = bundle.calendar
        logger.info(
            f"Creating event on {bundle.auth.id}: "
            f"{body.title!r} ({body.start} -> {body.end})"
        )
        try:
            created = provider.create_event(body.title, body.start, body.end)
        except ProviderAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return created.to_dict()

    @app.patch("/api/events/{event_id}")
    def update_event(
        event_id: str,
        body: EventPatch,
        registry: RegistryDep,
        provider: str | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        # Events are merged from every provider, so the caller tells us which
        # provider owns this event.
        bundle = _require_provider(registry, provider)
        logger.info(f"Updating event {event_id} on {bundle.auth.id}")
        try:
            updated = bundle.calendar.update_event(
                event_id,
                title=body.title,
                start=body.start,
                end=body.end,
                calendar_id=calendar_id,
            )
        except ProviderAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return updated.to_dict()

    @app.delete("/api/events/{event_id}", status_code=204)
    def delete_event(
        event_id: str,
        registry: RegistryDep,
        provider: str | None = None,
        calendar_id: str | None = None,
    ) -> None:
        bundle = _require_provider(registry, provider)
        logger.info(f"Deleting event {event_id} on {bundle.auth.id}")
        try:
            bundle.calendar.delete_event(event_id, calendar_id=calendar_id)
        except ProviderAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # ------------------------------------------- SPA static file serving --
    # Must come last so API routes take priority.

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
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

    return app
