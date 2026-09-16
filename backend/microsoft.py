"""Microsoft Calendar (Microsoft Graph) + OAuth2 implementation.

Uses the raw OAuth2 endpoints and the Graph REST API via ``requests`` rather
than pulling in a heavier SDK — the surface we need is small and this keeps the
provider symmetric with the Google one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from loguru import logger

from backend.auth import AuthProvider, TokenStore
from backend.models import (
    DEFAULT_EVENT_COLOR,
    DEFAULT_EVENT_TEXT_COLOR,
    CalendarEvent,
)
from backend.provider import (
    CalendarProvider,
    ProviderAuthError,
    ProviderError,
    ProviderScopeError,
)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# Microsoft returns `offline_access` inconsistently, so it is requested but not
# enumerated as a required scope; Calendars.ReadWrite is the one we must have.
_REQUIRED_SCOPES = ("Calendars.ReadWrite",)
_EXTRA_SCOPES = ("offline_access",)


def _utc_iso(value: datetime) -> str:
    """ISO-8601 UTC string without an offset (Graph's preferred form)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_graph_datetime(block: dict[str, Any]) -> str | None:
    raw = block.get("dateTime")
    if not raw:
        return None
    # Graph emits e.g. "2024-01-01T10:00:00.0000000" (UTC, no suffix) once
    # `Prefer: outlook.timezone="UTC"` is honoured.
    if raw.endswith("Z") or "+" in raw[10:]:
        return raw
    return f"{raw}Z"


def _text_color_for(background: str) -> str:
    """Pick black or white text for a hex background based on luminance."""
    value = background.lstrip("#")
    if len(value) != 6:
        return DEFAULT_EVENT_TEXT_COLOR
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return DEFAULT_EVENT_TEXT_COLOR
    # Relative luminance (sRGB approximation) — 0..1
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#1b1b1b" if luminance > 0.6 else "#f5f5f5"


class MicrosoftAuth(AuthProvider):
    id = "microsoft"
    display_name = "Microsoft Calendar"
    scopes = _REQUIRED_SCOPES

    def __init__(
        self,
        store: TokenStore,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        tenant_id: str = "common",
    ) -> None:
        super().__init__(store)
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._tenant_id = tenant_id or "common"
        self._session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def redirect_uri(self) -> str:
        return self._redirect_uri

    @property
    def _token_uri(self) -> str:
        return (
            f"https://login.microsoftonline.com/{self._tenant_id}"
            "/oauth2/v2.0/token"
        )

    @property
    def _authorize_uri(self) -> str:
        return (
            f"https://login.microsoftonline.com/{self._tenant_id}"
            "/oauth2/v2.0/authorize"
        )

    def authorization_url(self, state: str) -> str:
        if not self.configured:
            raise RuntimeError(
                "MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET not set"
            )
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "response_mode": "query",
            "scope": " ".join((*self.scopes, *_EXTRA_SCOPES)),
            "state": state,
        }
        return f"{self._authorize_uri}?{urlencode(params)}"

    def exchange_code(self, code: str) -> None:
        if not self.configured:
            raise RuntimeError(
                "MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET not set"
            )
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "scope": " ".join((*self.scopes, *_EXTRA_SCOPES)),
        }
        payload = self._post_token(data, context="Microsoft token exchange failed")
        granted = str(payload.get("scope", "")).split() or list(self.scopes)
        missing = self.missing_scopes(granted)
        if missing:
            logger.warning(
                f"Microsoft did not grant required scope(s): {', '.join(missing)}"
            )
            raise ProviderScopeError(
                "Microsoft did not grant the required scope(s): "
                f"{', '.join(missing)}. Check the app registration's API "
                "permissions."
            )
        self._persist(payload, scopes=granted)
        logger.info("Microsoft authentication successful — tokens saved")

    def _post_token(self, data: dict[str, str], *, context: str) -> dict[str, Any]:
        try:
            response = self._session.post(self._token_uri, data=data, timeout=30)
        except requests.RequestException as exc:
            logger.exception(f"{context}: {exc}")
            raise ProviderError(f"{context}: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:500]
            logger.error(f"{context}: HTTP {response.status_code} {detail}")
            raise ProviderError(f"{context}: HTTP {response.status_code} {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"{context}: malformed token response") from exc

    def _persist(self, payload: dict[str, Any], *, scopes: list[str] | None = None) -> None:
        access_token = payload.get("access_token")
        if not access_token:
            raise ProviderError("Microsoft token response contained no access_token")
        expires_in = payload.get("expires_in")
        expiry = None
        if isinstance(expires_in, (int, float)):
            expiry = (
                datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
            ).isoformat()
        self._store.save(
            self.id,
            access_token=str(access_token),
            refresh_token=payload.get("refresh_token"),
            expiry=expiry,
            scopes=scopes,
        )

    def _refresh(self, refresh_token: str) -> str | None:
        if not self.configured:
            return None
        logger.info("Microsoft access token expired — refreshing via refresh token")
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join((*self.scopes, *_EXTRA_SCOPES)),
        }
        try:
            payload = self._post_token(data, context="Microsoft token refresh failed")
        except ProviderError:
            return None
        self._persist(payload)
        logger.info("Microsoft credentials refreshed and saved")
        return str(payload["access_token"])

    def _valid_cached_token(self, tokens: dict[str, object]) -> str | None:
        token = tokens.get("access_token") or tokens.get("token")
        if not token:
            return None
        expiry_raw = tokens.get("expiry")
        if not expiry_raw:
            return str(token)
        try:
            expiry = datetime.fromisoformat(str(expiry_raw))
        except ValueError:
            return str(token)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        # Refresh a minute early to avoid racing an expiry.
        if datetime.now(timezone.utc) < expiry - timedelta(minutes=1):
            return str(token)
        return None

    def access_token(self) -> str | None:
        tokens = self._store.load(self.id)
        if not tokens:
            logger.debug("No usable Microsoft tokens — credentials unavailable")
            return None
        cached = self._valid_cached_token(tokens)
        if cached:
            return cached
        refresh_token = tokens.get("refresh_token")
        if refresh_token:
            return self._refresh(str(refresh_token))
        logger.warning(
            "Stored Microsoft credentials are invalid and cannot be refreshed — "
            "re-authenticate"
        )
        return None

    def is_authenticated(self) -> bool:
        tokens = self._store.load(self.id)
        if not tokens:
            return False
        if not (tokens.get("access_token") or tokens.get("token") or tokens.get("refresh_token")):
            return False
        return not self.missing_scopes(tokens.get("scopes"))


class MicrosoftCalendarProvider(CalendarProvider):
    id = "microsoft"

    def __init__(self, auth: MicrosoftAuth, *, calendar_id: str | None = None) -> None:
        super().__init__(auth)
        self.auth: MicrosoftAuth = auth
        self._calendar_override = calendar_id
        self._session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = self.auth.access_token()
        if token is None:
            raise ProviderAuthError(
                "Microsoft credentials unavailable — reconnect required"
            )
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        url = path if path.startswith("http") else f"{_GRAPH_BASE}{path}"
        try:
            response = self._session.request(
                method, url, headers=headers, timeout=30, **kwargs
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Microsoft Graph request failed: {exc}") from exc
        if response.status_code == 401:
            raise ProviderAuthError("Microsoft rejected the token — reconnect required")
        if response.status_code == 204:
            return {}
        if response.status_code >= 400:
            detail = response.text[:500]
            raise ProviderError(
                f"Microsoft Graph request failed: HTTP {response.status_code} {detail}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("Microsoft Graph returned a malformed response") from exc

    def _calendars(self) -> list[tuple[str, str]]:
        """(calendarId, background colour) for every calendar."""
        if self._calendar_override:
            return [(self._calendar_override, DEFAULT_EVENT_COLOR)]
        calendars: list[tuple[str, str]] = []
        path: str | None = "/me/calendars?$select=id,name,hexColor&$top=100"
        while path:
            result = self._request("GET", path)
            for item in result.get("value", []):
                cal_id = item.get("id")
                if not cal_id:
                    continue
                calendars.append((cal_id, item.get("hexColor") or DEFAULT_EVENT_COLOR))
            path = result.get("@odata.nextLink")
        return calendars

    def list_events(self, day_start: datetime, day_end: datetime) -> list[CalendarEvent]:
        calendars = self._calendars()
        params = {
            "startDateTime": _utc_iso(day_start),
            "endDateTime": _utc_iso(day_end),
            # Graph expands recurrences for calendarView; cap the page size and
            # follow nextLinks so a busy calendar can't truncate the day.
            "$top": "100",
        }
        headers = {"Prefer": 'outlook.timezone="UTC"'}

        events: list[CalendarEvent] = []
        failures = 0
        for cal_id, color in calendars:
            try:
                path: str | None = (
                    f"/me/calendars/{cal_id}/calendarView?"
                    + urlencode(params)
                )
                while path:
                    result = self._request("GET", path, headers=headers)
                    for raw in result.get("value", []):
                        mapped = self._map_event(raw, cal_id, color)
                        if mapped is not None:
                            events.append(mapped)
                    path = result.get("@odata.nextLink")
            except ProviderError as exc:
                failures += 1
                logger.exception(f"Microsoft Calendar fetch failed for {cal_id}: {exc}")
                continue

        if calendars and failures == len(calendars):
            raise ProviderError("Microsoft Calendar request failed for every calendar")
        return events

    def _event_body(
        self,
        *,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if title is not None:
            body["subject"] = title
        if start is not None:
            body["start"] = {"dateTime": _utc_iso(start).rstrip("Z"), "timeZone": "UTC"}
        if end is not None:
            body["end"] = {"dateTime": _utc_iso(end).rstrip("Z"), "timeZone": "UTC"}
        return body

    def create_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent:
        if self._calendar_override:
            path = f"/me/calendars/{self._calendar_override}/events"
        else:
            path = "/me/events"
        result = self._request(
            "POST",
            path,
            json=self._event_body(title=title, start=start, end=end),
        )
        logger.info(f"Microsoft event created: id={result.get('id')}")
        return self._map_event(
            result, self._calendar_override or "primary", DEFAULT_EVENT_COLOR
        ) or CalendarEvent(
            id=str(result.get("id", "")),
            title=title,
            start=_utc_iso(start),
            end=_utc_iso(end),
            all_day=False,
            calendar_id=self._calendar_override or "primary",
            provider_id=self.id,
        )

    def update_event(
        self,
        event_id: str,
        *,
        title: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        calendar_id: str | None = None,
    ) -> CalendarEvent:
        if calendar_id:
            path = f"/me/calendars/{calendar_id}/events/{event_id}"
        else:
            path = f"/me/events/{event_id}"
        result = self._request(
            "PATCH",
            path,
            json=self._event_body(title=title, start=start, end=end),
        )
        logger.debug(f"Microsoft event {event_id} updated")
        mapped = self._map_event(
            result, calendar_id or "primary", DEFAULT_EVENT_COLOR
        )
        if mapped is not None:
            return mapped
        raise ProviderError("Microsoft returned a malformed event after update")

    def delete_event(self, event_id: str, *, calendar_id: str | None = None) -> None:
        if calendar_id:
            path = f"/me/calendars/{calendar_id}/events/{event_id}"
        else:
            path = f"/me/events/{event_id}"
        self._request("DELETE", path)
        logger.debug(f"Microsoft event {event_id} deleted")

    def _map_event(
        self, raw: dict[str, Any], calendar_id: str, color: str
    ) -> CalendarEvent | None:
        event_id = raw.get("id")
        start_iso = _parse_graph_datetime(raw.get("start") or {})
        end_iso = _parse_graph_datetime(raw.get("end") or {})
        if not event_id or start_iso is None or end_iso is None:
            logger.warning(f"Skipping malformed Microsoft event: {event_id!r}")
            return None
        return CalendarEvent(
            id=str(event_id),
            title=raw.get("subject") or "(no title)",
            start=start_iso,
            end=end_iso,
            all_day=bool(raw.get("isAllDay")),
            calendar_id=calendar_id,
            provider_id=self.id,
            color=color,
            text_color=_text_color_for(color),
        )
