"""FastAPI application factory.

The app never constructs a provider itself — the registry is injected by the
composition root (:mod:`server`). Routes depend on :func:`get_registry`, so the
same app can be assembled with the real Radicale provider or with fakes in
tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel, model_validator

from backend.provider import CalendarProvider, ProviderError
from backend.registry import ProviderRegistry

# The API is a local-only, unauthenticated-by-design service for the widget.
# Pinning the Host header stops a DNS-rebinding page from reaching it.
_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


RegistryDep = Annotated[ProviderRegistry, Depends(get_registry)]


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
    # Which occurrence of a recurring event is being changed; omitted means the
    # whole series.
    original_start: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> "EventPatch":
        if self.title is None and self.start is None and self.end is None:
            raise ValueError("patch must include at least one of title, start, end")
        if self.title is not None and not self.title.strip():
            raise ValueError("title must not be empty")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("end must be after start")
        return self


def _require_provider(
    registry: ProviderRegistry, provider_id: str | None
) -> CalendarProvider:
    """Resolve the target provider, defaulting to the first configured one."""
    if provider_id:
        provider = registry.find(provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"Unknown provider {provider_id!r}")
        return provider
    configured = registry.configured()
    if not configured:
        logger.warning("API request rejected — no calendar provider is configured")
        raise HTTPException(status_code=401, detail="No calendar configured")
    return configured[0]


def create_app(registry: ProviderRegistry, *, build_dir: Path | None = None) -> FastAPI:
    build_root = (build_dir or Path(__file__).resolve().parent.parent / "build").resolve()

    # Docs are disabled: this API carries the user's real credentials and has
    # no business being browsable from the local machine.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.registry = registry
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)

    # -------------------------------------------------------------- status --

    @app.get("/api/status")
    def status(registry: RegistryDep) -> dict[str, Any]:
        """Whether the sidebar has a usable calendar, and which providers exist."""
        providers = registry.status()
        connected = registry.has_any_configured()
        logger.debug(f"Status check: configured={connected}")
        return {"configured": connected, "providers": providers}

    # -------------------------------------------------------------- events --

    @app.get("/api/events")
    def list_events(registry: RegistryDep) -> list[dict[str, Any]]:
        if not registry.has_any_configured():
            logger.warning("API request rejected — no calendar provider is configured")
            raise HTTPException(status_code=401, detail="No calendar configured")

        # Day boundaries in the machine's local timezone, not UTC — otherwise
        # the window drifts by hours for anyone west/east of Greenwich.
        now = datetime.now().astimezone()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        result = registry.list_events(day_start, day_end)
        if result.all_failed:
            raise HTTPException(
                status_code=502,
                detail="; ".join(
                    f"{pid}: {msg}" for pid, msg in result.failures.items()
                ),
            )
        logger.info(
            f"Fetched {len(result.events)} event(s) across "
            f"{len(registry.configured())} provider(s) for {day_start.date()}"
        )
        return [event.to_dict() for event in result.events]

    @app.post("/api/events", status_code=201)
    def create_event(body: EventCreate, registry: RegistryDep) -> dict[str, Any]:
        provider = _require_provider(registry, body.provider)
        logger.info(
            f"Creating event on {provider.id}: "
            f"{body.title!r} ({body.start} -> {body.end})"
        )
        try:
            created = provider.create_event(body.title, body.start, body.end)
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
        original_start: datetime | None = None,
    ) -> dict[str, Any]:
        # Events are merged from every provider and calendar, so the caller
        # tells us which one owns this event. `original_start` is accepted in
        # the query as well as the body so PATCH and DELETE are addressed the
        # same way.
        target = _require_provider(registry, provider)
        logger.info(f"Updating event {event_id} on {target.id}")
        try:
            updated = target.update_event(
                event_id,
                title=body.title,
                start=body.start,
                end=body.end,
                calendar_id=calendar_id,
                original_start=body.original_start or original_start,
            )
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return updated.to_dict()

    @app.delete("/api/events/{event_id}", status_code=204)
    def delete_event(
        event_id: str,
        registry: RegistryDep,
        provider: str | None = None,
        calendar_id: str | None = None,
        original_start: datetime | None = None,
    ) -> None:
        target = _require_provider(registry, provider)
        logger.info(f"Deleting event {event_id} on {target.id}")
        try:
            target.delete_event(
                event_id, calendar_id=calendar_id, original_start=original_start
            )
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
