"""Provider registry — the injectable dependency routes resolve calendars from."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from backend.models import CalendarEvent
from backend.provider import CalendarProvider, ProviderError


@dataclass
class AggregationResult:
    """Outcome of merging events across every configured provider."""

    events: list[CalendarEvent] = field(default_factory=list)
    # provider id -> human-readable failure reason
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def all_failed(self) -> bool:
        return not self.events and bool(self.failures)


class ProviderRegistry:
    """Look-up + aggregation seam over the configured providers."""

    def __init__(self, providers: Iterable[CalendarProvider]) -> None:
        self._providers: dict[str, CalendarProvider] = {p.id: p for p in providers}

    def all(self) -> list[CalendarProvider]:
        return list(self._providers.values())

    def find(self, provider_id: str) -> CalendarProvider | None:
        return self._providers.get(provider_id)

    def require(self, provider_id: str) -> CalendarProvider:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        return provider

    def configured(self) -> list[CalendarProvider]:
        return [p for p in self._providers.values() if p.configured]

    def has_any_configured(self) -> bool:
        return any(p.configured for p in self._providers.values())

    def list_events(self, day_start: datetime, day_end: datetime) -> AggregationResult:
        """Fetch from every configured provider, isolating per-provider failures.

        One broken provider must not blank the sidebar, so partial results are
        returned alongside the failures.
        """
        result = AggregationResult()
        for provider in self.configured():
            try:
                result.events.extend(provider.list_events(day_start, day_end))
            except ProviderError as exc:
                result.failures[provider.id] = str(exc)
        result.events.sort(key=lambda event: event.sort_key())
        return result

    def status(self) -> list[dict[str, object]]:
        return [
            {
                "id": provider.id,
                "displayName": provider.display_name,
                "configured": provider.configured,
            }
            for provider in self._providers.values()
        ]
