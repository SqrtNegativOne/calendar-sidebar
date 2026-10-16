"""Provider-agnostic calendar backend.

The backend is built around one small interface —
:class:`~backend.provider.CalendarProvider` for event CRUD — so the Radicale
provider is swappable and the routes never name a concrete implementation.
Nothing in this package reaches for ``os.environ`` directly: configuration is
injected by the composition root (:mod:`server`).
"""

from backend.app import create_app
from backend.models import CalendarEvent
from backend.provider import CalendarProvider, ProviderError
from backend.registry import ProviderRegistry

__all__ = [
    "CalendarEvent",
    "CalendarProvider",
    "ProviderError",
    "ProviderRegistry",
    "create_app",
]
