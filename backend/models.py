"""Provider-agnostic domain models shared by every calendar implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Fallback colours for events whose calendar (or a freshly created event)
# doesn't supply its own background/foreground pair.
DEFAULT_EVENT_COLOR = "#1e3a5f"
DEFAULT_EVENT_TEXT_COLOR = "#a8cef0"


def _parse_iso(value: str) -> datetime:
    """Best-effort parse of an RFC3339 string from either provider.

    Google returns offsets (``...+02:00``) while Microsoft returns UTC without
    an offset. ``fromisoformat`` handles both, but never trust provider input —
    an unparseable value sorts to the epoch rather than raising mid-aggregation.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    """Normalised event, independent of the provider it came from."""

    id: str
    title: str
    start: str
    end: str
    all_day: bool
    calendar_id: str
    provider_id: str
    color: str = DEFAULT_EVENT_COLOR
    text_color: str = DEFAULT_EVENT_TEXT_COLOR

    def sort_key(self) -> datetime:
        return _parse_iso(self.start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "allDay": self.all_day,
            "calendarId": self.calendar_id,
            "provider": self.provider_id,
            "color": self.color,
            "textColor": self.text_color,
        }
