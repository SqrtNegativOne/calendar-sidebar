"""Provider-agnostic calendar backend.

The backend is built around two small interfaces — :class:`~backend.auth.AuthProvider`
for OAuth token lifecycle and :class:`~backend.provider.CalendarProvider` for
event CRUD — so Google and Microsoft are swappable implementations wired up by
:mod:`server`. Nothing in this package reaches for ``os.environ`` directly:
configuration is injected by the composition root.
"""

from backend.app import create_app
from backend.auth import AuthProvider, OAuthStateStore, TokenStore
from backend.models import CalendarEvent
from backend.provider import (
    CalendarProvider,
    ProviderAuthError,
    ProviderBundle,
    ProviderError,
    ProviderScopeError,
)
from backend.registry import ProviderRegistry

__all__ = [
    "AuthProvider",
    "CalendarEvent",
    "CalendarProvider",
    "OAuthStateStore",
    "ProviderAuthError",
    "ProviderBundle",
    "ProviderError",
    "ProviderRegistry",
    "ProviderScopeError",
    "TokenStore",
    "create_app",
]
