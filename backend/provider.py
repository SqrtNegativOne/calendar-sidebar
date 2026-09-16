"""Calendar provider interface + error taxonomy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from backend.auth import AuthProvider
from backend.models import CalendarEvent


class ProviderError(RuntimeError):
    """A calendar provider failed to complete a request."""


class ProviderAuthError(ProviderError):
    """The provider's credentials are unusable and the user must reconnect."""


class ProviderScopeError(ProviderError):
    """Consent was granted, but without a scope the provider requires."""


class CalendarProvider(ABC):
    """Interface for reading and mutating a user's calendars."""

    id: str

    def __init__(self, auth: AuthProvider) -> None:
        self.auth = auth

    @abstractmethod
    def list_events(self, day_start: datetime, day_end: datetime) -> list[CalendarEvent]:
        """Return every event in the half-open window, across all calendars."""

    @abstractmethod
    def create_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent:
        ...

    @abstractmethod
    def update_event(
        self,
        event_id: str,
        *,
        title: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        calendar_id: str | None = None,
    ) -> CalendarEvent:
        ...

    @abstractmethod
    def delete_event(self, event_id: str, *, calendar_id: str | None = None) -> None:
        ...


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    """An auth strategy paired with the calendar client that consumes it."""

    auth: AuthProvider
    calendar: CalendarProvider
