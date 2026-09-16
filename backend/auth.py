"""Polymorphic OAuth token handling.

Every provider implements :class:`AuthProvider`; the concrete mechanics of
refresh, consent URLs and scope validation differ per provider, but the rest of
the backend only ever talks to this interface. Tokens live in a single JSON
file keyed by provider id, guarded by a lock so a background poll racing a
token refresh can't lose a rotated refresh token.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger


def chmod_private(path: Path) -> None:
    """Best-effort owner-only permissions on a file holding OAuth tokens."""
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.debug(f"Could not restrict permissions on {path.name}: {exc}")


class TokenStore:
    """JSON-backed, per-provider token storage.

    Layout::

        {"google": {"token": ..., "refresh_token": ..., ...},
         "microsoft": {...}}

    A legacy flat Google token file (``{"token": ...}``) is migrated in place
    the first time it is written.
    """

    _LEGACY_KEYS = ("token", "refresh_token", "expiry", "scopes")

    def __init__(self, path: Path) -> None:
        self._path = path
        # Re-entrant so save() can call load() while a caller already holds the
        # lock (FastAPI runs sync routes in a threadpool).
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def _read_all(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
        except Exception as exc:
            logger.warning(f"Failed to read token file: {exc}")
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _is_provider_blob(value: object) -> bool:
        return isinstance(value, dict) and any(
            key in value for key in ("token", "refresh_token", "access_token")
        )

    def load(self, provider_id: str) -> dict[str, object] | None:
        """Return the stored token blob for ``provider_id`` or ``None``."""
        with self._lock:
            data = self._read_all()
            blob = data.get(provider_id)
            if self._is_provider_blob(blob):
                logger.debug(f"Token file loaded for {provider_id}")
                return dict(blob)  # type: ignore[arg-type]
            # Backwards compatibility: the original single-provider file stored
            # Google's token fields at the top level.
            if provider_id == "google" and any(key in data for key in self._LEGACY_KEYS):
                logger.debug("Migrating legacy flat Google token file")
                return {key: data[key] for key in self._LEGACY_KEYS if key in data}
            logger.debug(f"Token file not found for {provider_id} — not yet authenticated")
            return None

    def save(
        self,
        provider_id: str,
        *,
        token: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expiry: str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        """Merge token fields and persist atomically.

        ``None`` fields are ignored so a refresh (which omits ``scopes``) keeps
        the previously recorded grant.
        """
        with self._lock:
            data = self._read_all()
            legacy_keys = [key for key in self._LEGACY_KEYS if key in data]
            if provider_id == "google" and "google" not in data and legacy_keys:
                data = {"google": {key: data[key] for key in legacy_keys}}

            blob = data.get(provider_id)
            updated: dict[str, object] = dict(blob) if isinstance(blob, dict) else {}
            for key, value in (
                ("token", token),
                ("access_token", access_token),
                ("refresh_token", refresh_token),
                ("expiry", expiry),
                ("scopes", scopes),
            ):
                if value is not None:
                    updated[key] = value
            data[provider_id] = updated

            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2))
            chmod_private(tmp)
            tmp.replace(self._path)
            chmod_private(self._path)

    def clear(self, provider_id: str) -> None:
        with self._lock:
            data = self._read_all()
            if provider_id not in data:
                return
            data.pop(provider_id, None)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2))
            chmod_private(tmp)
            tmp.replace(self._path)
            chmod_private(self._path)


class OAuthStateStore:
    """Short-lived, single-use OAuth ``state`` store (login-CSRF guard).

    Each state is bound to the provider that issued it, so a callback can be
    dispatched without trusting a query parameter.
    """

    def __init__(self, ttl: timedelta = timedelta(minutes=10)) -> None:
        self._ttl = ttl
        self._states: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def issue(self, provider_id: str) -> str:
        state = secrets.token_urlsafe(32)
        now = datetime.now()
        with self._lock:
            for stale, (_, issued) in list(self._states.items()):
                if now - issued > self._ttl:
                    self._states.pop(stale, None)
            self._states[state] = (provider_id, now)
        return state

    def consume(self, state: str | None) -> str | None:
        """Validate + invalidate ``state``; return the issuing provider id."""
        if not state:
            return None
        with self._lock:
            entry = self._states.pop(state, None)
        if entry is None:
            return None
        provider_id, issued = entry
        if datetime.now() - issued > self._ttl:
            return None
        return provider_id


class AuthProvider(ABC):
    """Interface every OAuth provider implements."""

    id: str
    display_name: str
    scopes: tuple[str, ...] = ()

    def __init__(self, store: TokenStore) -> None:
        self._store = store

    @property
    @abstractmethod
    def configured(self) -> bool:
        """True when the provider has enough credentials to attempt a login."""

    @abstractmethod
    def authorization_url(self, state: str) -> str:
        """Build the consent URL the user opens in a browser."""

    @abstractmethod
    def exchange_code(self, code: str) -> None:
        """Exchange an authorization code and persist the resulting tokens."""

    @abstractmethod
    def access_token(self) -> str | None:
        """Return a currently-valid bearer token, refreshing if necessary."""

    @abstractmethod
    def is_authenticated(self) -> bool:
        """True when a usable, correctly-scoped token is on disk."""

    def logout(self) -> None:
        self._store.clear(self.id)

    def redirect_uri(self) -> str | None:
        """The redirect URI this provider is configured to use, if any."""
        return None

    def missing_scopes(self, granted: object) -> list[str]:
        """Required scopes absent from a recorded grant."""
        granted_set = set(granted) if isinstance(granted, list) else set()
        return [scope for scope in self.scopes if scope not in granted_set]
