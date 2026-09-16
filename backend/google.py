"""Google Calendar + OAuth2 implementation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build as gclient_build
from googleapiclient.errors import HttpError
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

_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"


class GoogleAuth(AuthProvider):
    id = "google"
    display_name = "Google Calendar"
    scopes = (
        # Read/write events on every calendar the user can access.
        "https://www.googleapis.com/auth/calendar.events",
        # Read the user's calendar list so we know which calendars to query.
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    )

    def __init__(
        self,
        store: TokenStore,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        super().__init__(store)
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def redirect_uri(self) -> str:
        return self._redirect_uri

    def _client_config(self) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError(
                "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set — copy "
                ".env.example to .env and fill them in"
            )
        return {
            "web": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "auth_uri": _AUTH_URI,
                "token_uri": _TOKEN_URI,
                "redirect_uris": [self._redirect_uri],
            }
        }

    def authorization_url(self, state: str) -> str:
        flow = Flow.from_client_config(
            self._client_config(), scopes=list(self.scopes), redirect_uri=self._redirect_uri
        )
        url, _ = flow.authorization_url(
            access_type="offline", prompt="consent", state=state
        )
        return url

    def exchange_code(self, code: str) -> None:
        flow = Flow.from_client_config(
            self._client_config(), scopes=list(self.scopes), redirect_uri=self._redirect_uri
        )
        try:
            flow.fetch_token(code=code)
        except Exception as exc:
            logger.exception(f"Google token exchange failed: {exc}")
            raise ProviderError(f"Google token exchange failed: {exc}") from exc

        creds = flow.credentials
        # `creds.scopes` echoes what we *requested*; `granted_scopes` is what
        # Google actually returned. Validate and store the real grant.
        granted = (creds.granted_scopes or "").split() or list(self.scopes)
        missing = self.missing_scopes(granted)
        if missing:
            logger.warning(f"Google did not grant required scope(s): {', '.join(missing)}")
            raise ProviderScopeError(
                "Google did not grant the required scope(s): "
                f"{', '.join(missing)}. Check the OAuth consent screen's scopes."
            )
        self._store.save(
            self.id,
            token=creds.token,
            refresh_token=creds.refresh_token,
            expiry=creds.expiry.isoformat() if creds.expiry else None,
            scopes=granted,
        )
        logger.info("Google authentication successful — tokens saved")

    def credentials(self) -> Credentials | None:
        """Return usable Google credentials, refreshing in place if needed."""
        tokens = self._store.load(self.id)
        if not tokens or not (tokens.get("token") or tokens.get("refresh_token")):
            logger.debug("No usable Google tokens — credentials unavailable")
            return None

        # A refresh cannot broaden an existing grant, so a token minted before
        # a scope was added must go back through consent.
        missing = self.missing_scopes(tokens.get("scopes"))
        if missing:
            if not tokens.get("scopes"):
                logger.warning(
                    "Stored Google credentials predate scope tracking — "
                    "re-authenticate to grant calendar-list access"
                )
            else:
                logger.warning(
                    "Stored Google credentials are missing required scope(s): "
                    f"{', '.join(missing)} — re-authenticate"
                )
            return None

        if not self.configured:
            logger.error("Cannot build Google credentials — OAuth client not configured")
            return None

        expiry: datetime | None = None
        if tokens.get("expiry"):
            try:
                expiry = datetime.fromisoformat(str(tokens["expiry"]))
            except ValueError:
                pass

        creds = Credentials(
            token=str(tokens["token"]) if tokens.get("token") else None,
            refresh_token=(
                str(tokens["refresh_token"]) if tokens.get("refresh_token") else None
            ),
            token_uri=_TOKEN_URI,
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=list(self.scopes),
            expiry=expiry,
        )

        if not creds.valid and creds.refresh_token:
            logger.info("Google access token expired — refreshing via refresh token")
            try:
                creds.refresh(Request())
            except Exception as exc:
                logger.error(f"Google credential refresh failed: {exc}")
                return None
            self._store.save(
                self.id,
                token=creds.token,
                refresh_token=creds.refresh_token,
                expiry=creds.expiry.isoformat() if creds.expiry else None,
            )
            logger.info("Google credentials refreshed and saved")

        if not creds.valid:
            logger.warning(
                "Stored Google credentials are invalid and cannot be refreshed — "
                "re-authenticate"
            )
            return None
        return creds

    def access_token(self) -> str | None:
        creds = self.credentials()
        return creds.token if creds and creds.token else None

    def is_authenticated(self) -> bool:
        tokens = self._store.load(self.id)
        if not tokens or not (tokens.get("token") or tokens.get("refresh_token")):
            return False
        return not self.missing_scopes(tokens.get("scopes"))


class GoogleCalendarProvider(CalendarProvider):
    id = "google"

    def __init__(self, auth: GoogleAuth, *, calendar_id: str | None = None) -> None:
        super().__init__(auth)
        # Narrow the declared base type for callers that need Google-specific
        # credential handling.
        self.auth: GoogleAuth = auth
        self._calendar_override = calendar_id

    def _service(self):
        creds = self.auth.credentials()
        if creds is None:
            raise ProviderAuthError("Google credentials unavailable — reconnect required")
        try:
            return gclient_build("calendar", "v3", credentials=creds)
        except Exception as exc:
            raise ProviderError(f"Could not build Google Calendar client: {exc}") from exc

    def _visible_calendars(self, svc: Any) -> list[tuple[str, str, str]]:
        """(calendarId, backgroundColor, foregroundColor) for every calendar."""
        if self._calendar_override:
            return [
                (
                    self._calendar_override,
                    DEFAULT_EVENT_COLOR,
                    DEFAULT_EVENT_TEXT_COLOR,
                )
            ]

        calendars: list[tuple[str, str, str]] = []
        page_token: str | None = None
        while True:
            try:
                result = svc.calendarList().list(pageToken=page_token).execute()
            except HttpError as exc:
                if exc.resp.status == 403 and "insufficient" in str(exc).lower():
                    logger.warning(
                        "Google calendar list rejected — token lacks the "
                        "calendar-list scope; re-authentication required"
                    )
                    raise ProviderAuthError(
                        "Reconnect required to read all calendars"
                    ) from exc
                raise ProviderError(f"Google calendar list failed: {exc}") from exc
            for item in result.get("items", []):
                if item.get("deleted") or not item.get("id"):
                    continue
                calendars.append(
                    (
                        item["id"],
                        item.get("backgroundColor") or DEFAULT_EVENT_COLOR,
                        item.get("foregroundColor") or DEFAULT_EVENT_TEXT_COLOR,
                    )
                )
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return calendars

    def list_events(self, day_start: datetime, day_end: datetime) -> list[CalendarEvent]:
        svc = self._service()
        calendars = self._visible_calendars(svc)

        events: list[CalendarEvent] = []
        failures = 0
        for cal_id, color, text_color in calendars:
            try:
                result = (
                    svc.events()
                    .list(
                        calendarId=cal_id,
                        timeMin=day_start.isoformat(),
                        timeMax=day_end.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
            except Exception as exc:
                failures += 1
                logger.exception(
                    f"Google Calendar fetch failed for {cal_id} on "
                    f"{day_start.date()}: {exc}"
                )
                continue
            for raw in result.get("items", []):
                try:
                    events.append(self._map_event(raw, cal_id, color, text_color))
                except ValueError as exc:
                    logger.warning(f"Skipping malformed Google event: {exc}")

        if calendars and failures == len(calendars):
            raise ProviderError("Google Calendar request failed for every calendar")
        return events

    def create_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent:
        svc = self._service()
        calendar_id = self._calendar_override or "primary"
        result = (
            svc.events()
            .insert(
                calendarId=calendar_id,
                body={
                    "summary": title,
                    "start": {"dateTime": start.isoformat()},
                    "end": {"dateTime": end.isoformat()},
                },
            )
            .execute()
        )
        logger.info(f"Google event created: id={result.get('id')}")
        return self._map_event(result, calendar_id)

    def update_event(
        self,
        event_id: str,
        *,
        title: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        calendar_id: str | None = None,
    ) -> CalendarEvent:
        target = calendar_id or self._calendar_override or "primary"
        svc = self._service()
        patch: dict[str, Any] = {}
        if title is not None:
            patch["summary"] = title
        if start is not None:
            patch["start"] = {"dateTime": start.isoformat()}
        if end is not None:
            patch["end"] = {"dateTime": end.isoformat()}
        result = (
            svc.events()
            .patch(calendarId=target, eventId=event_id, body=patch)
            .execute()
        )
        logger.debug(f"Google event {event_id} updated")
        return self._map_event(result, target)

    def delete_event(self, event_id: str, *, calendar_id: str | None = None) -> None:
        target = calendar_id or self._calendar_override or "primary"
        self._service().events().delete(calendarId=target, eventId=event_id).execute()
        logger.debug(f"Google event {event_id} deleted")

    @staticmethod
    def _iso_from_block(block: dict[str, Any]) -> str | None:
        """RFC3339-ish string from a Google ``start``/``end`` block."""
        if block.get("dateTime"):
            return block["dateTime"]
        if block.get("date"):
            return f"{block['date']}T00:00:00"
        return None

    def _map_event(
        self,
        raw: dict[str, Any],
        calendar_id: str,
        color: str = DEFAULT_EVENT_COLOR,
        text_color: str = DEFAULT_EVENT_TEXT_COLOR,
    ) -> CalendarEvent:
        start = raw.get("start") or {}
        end = raw.get("end") or {}
        start_iso = self._iso_from_block(start)
        end_iso = self._iso_from_block(end)
        if start_iso is None or end_iso is None:
            raise ValueError(f"event {raw.get('id')!r} has no usable start/end")
        return CalendarEvent(
            id=raw["id"],
            title=raw.get("summary") or "(no title)",
            start=start_iso,
            end=end_iso,
            all_day="date" in start,
            calendar_id=calendar_id,
            provider_id=self.id,
            color=color,
            text_color=text_color,
        )
