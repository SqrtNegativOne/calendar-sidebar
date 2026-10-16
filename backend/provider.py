"""Calendar provider interface + error taxonomy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from backend.models import CalendarEvent


class ProviderError(RuntimeError):
    """A calendar provider failed to complete a request."""


class CalendarProvider(ABC):
    """Interface for reading and mutating a user's calendars."""

    id: str
    display_name: str

    @property
    @abstractmethod
    def configured(self) -> bool:
        """True when the provider has everything it needs to serve requests."""

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
        original_start: datetime | None = None,
    ) -> CalendarEvent:
        """Change an event's title and/or time.

        ``original_start`` identifies the occurrence being changed inside a
        recurring event — it is the ``recurrenceId`` the API reported for that
        occurrence. When it is given, a provider that stores a whole series as
        a single object must change only that occurrence; when it is omitted,
        the series itself is edited.
        """

    @abstractmethod
    def delete_event(
        self,
        event_id: str,
        *,
        calendar_id: str | None = None,
        original_start: datetime | None = None,
    ) -> None:
        """Delete an event, or a single occurrence of a recurring one."""
