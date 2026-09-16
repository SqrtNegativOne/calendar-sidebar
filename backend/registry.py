"""Provider registry — the injectable dependency that ties auth to calendars."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from backend.models import CalendarEvent
from backend.provider import (
    CalendarProvider,
    ProviderAuthError,
    ProviderError,
    ProviderBundle,
)


@dataclass
class AggregationResult:
    """Outcome of merging events across every authenticated provider."""

    events: list[CalendarEvent] = field(default_factory=list)
    # provider id -> human-readable failure reason
    failures: dict[str, str] = field(default_factory=dict)
    # provider ids whose credentials were rejected mid-request
    auth_failures: set[str] = field(default_factory=set)

    @property
    def all_failed(self) -> bool:
        return not self.events and bool(self.failures)


class ProviderRegistry:
    """Look-up + aggregation seam over the configured provider bundles."""

    def __init__(self, bundles: Iterable[ProviderBundle]) -> None:
        self._bundles: dict[str, ProviderBundle] = {b.auth.id: b for b in bundles}

    def all(self) -> list[ProviderBundle]:
        return list(self._bundles.values())

    def find(self, provider_id: str) -> ProviderBundle | None:
        return self._bundles.get(provider_id)

    def require(self, provider_id: str) -> ProviderBundle:
        bundle = self._bundles.get(provider_id)
        if bundle is None:
            raise KeyError(provider_id)
        return bundle

    def authenticated(self) -> list[ProviderBundle]:
        return [b for b in self._bundles.values() if b.auth.is_authenticated()]

    def has_any_authenticated(self) -> bool:
        return any(b.auth.is_authenticated() for b in self._bundles.values())

    def any_configured(self) -> bool:
        return any(b.auth.configured for b in self._bundles.values())

    def list_events(
        self, day_start: datetime, day_end: datetime
    ) -> AggregationResult:
        """Fetch from every authenticated provider, isolating per-provider failures.

        One broken provider must not blank the sidebar, so partial results are
        returned alongside the failures.
        """
        result = AggregationResult()
        for bundle in self.authenticated():
            provider: CalendarProvider = bundle.calendar
            try:
                result.events.extend(provider.list_events(day_start, day_end))
            except ProviderAuthError as exc:
                result.failures[bundle.auth.id] = str(exc)
                result.auth_failures.add(bundle.auth.id)
            except ProviderError as exc:
                result.failures[bundle.auth.id] = str(exc)
        result.events.sort(key=lambda event: event.sort_key())
        return result

    def status(self) -> list[dict[str, object]]:
        return [
            {
                "id": bundle.auth.id,
                "displayName": bundle.auth.display_name,
                "configured": bundle.auth.configured,
                "authenticated": bundle.auth.is_authenticated(),
            }
            for bundle in self._bundles.values()
        ]
