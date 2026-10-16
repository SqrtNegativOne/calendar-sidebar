"""Radicale (CalDAV) calendar provider.

Plain CalDAV, so it works against any server that speaks the protocol; the
project's own deployment is Radicale on ``127.0.0.1:5232``.

Reading merges every calendar the account can see (each event keeps its own
calendar's colour). Writing targets the calendar an event already lives in, or
the first calendar that accepts events for new ones.

Two CalDAV behaviours shape the write path:

* A series is one resource holding a master ``VEVENT`` plus one override
  ``VEVENT`` per changed occurrence, so editing or deleting a single occurrence
  means appending a ``RECURRENCE-ID``-bearing override or an ``EXDATE`` — not
  touching the master.
* Every ``TZID`` must resolve on the server, so written times are always
  converted to a real IANA zone (``tzlocal``) rather than a bare offset or, on
  Windows, a Windows zone name that the server cannot look up.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

import caldav
import icalendar
from caldav.elements import dav
from caldav.elements import ical as ical_elem
from loguru import logger
from tzlocal import get_localzone

from backend.models import (
    DEFAULT_EVENT_COLOR,
    DEFAULT_EVENT_TEXT_COLOR,
    CalendarEvent,
)
from backend.provider import CalendarProvider, ProviderError

_VEVENT = "VEVENT"
_DISPLAYNAME = dav.DisplayName().tag
_CALENDAR_COLOR = ical_elem.CalendarColor().tag


@dataclass(frozen=True, slots=True)
class RadicaleSettings:
    """Everything needed to talk to one CalDAV server."""

    url: str
    username: str
    password: str
    # Optional allow-list of calendars (id, display name, or URL). Empty means
    # every calendar that accepts events; the first entry is also where new
    # events are created.
    calendar_ids: tuple[str, ...] = ()
    timeout: int = 15


@dataclass(frozen=True, slots=True)
class _CalendarRef:
    """A usable calendar plus the metadata the sidebar renders it with."""

    calendar: caldav.Calendar
    id: str
    name: str
    color: str
    text_color: str


def _localize(value: datetime | date) -> datetime | date:
    """Anchor a time to the machine's IANA zone, leaving dates alone.

    An aware time is converted, so a UTC instant from the browser lands in the
    local zone; a naive one is assumed to already be local wall-clock time,
    which is how every other client reads a floating CalDAV time. Either way
    the written ``TZID`` is a real IANA name — Radicale rejects anything it
    cannot look up, and a bare ``+05:30`` offset is not a name at all.
    """
    if isinstance(value, datetime):
        return value.astimezone(get_localzone())
    return value


def _iso(value: datetime | date) -> str:
    """Serialize a CalDAV date/date-time the way the frontend expects."""
    if isinstance(value, datetime):
        return value.isoformat()
    return f"{value.isoformat()}T00:00:00"


def _css_color(value: str | None) -> str:
    """Normalize a calendar colour for inline CSS.

    ``calendar-color`` is a CSS colour, so names pass through untouched;
    the trailing alpha of the common ``#rrggbbaa`` form is dropped because the
    sidebar composites events over its own dark surface.
    """
    if not value:
        return DEFAULT_EVENT_COLOR
    color = value.strip()
    if len(color) == 9 and color.startswith("#"):
        return color[:7]
    return color


def _text_color(background: str) -> str:
    """Pick a legible foreground for a ``#rrggbb`` background."""
    if len(background) != 7 or not background.startswith("#"):
        return DEFAULT_EVENT_TEXT_COLOR
    try:
        red, green, blue = (int(background[i : i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return DEFAULT_EVENT_TEXT_COLOR
    # Rec. 601 luma — good enough to choose black or white text.
    luma = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
    return "#16161a" if luma > 0.6 else "#f2f2f0"


def _components(instance: icalendar.Calendar) -> list[icalendar.Component]:
    """The ``VEVENT``s of a resource, ignoring any ``VTIMEZONE``."""
    return [sub for sub in instance.subcomponents if sub.name == _VEVENT]


def _master_of(instance: icalendar.Calendar) -> icalendar.Component:
    """The master ``VEVENT`` of a resource (the one without a ``RECURRENCE-ID``)."""
    events = _components(instance)
    if not events:
        raise ProviderError("Calendar object contains no VEVENT")
    return next((e for e in events if e.get("RECURRENCE-ID") is None), events[0])


def _is_recurring(component: icalendar.Component) -> bool:
    return component.get("RRULE") is not None or component.get("RDATE") is not None


def _excluded(component: icalendar.Component) -> list[datetime | date]:
    """Every EXDATE value on a component.

    icalendar returns a single property, or a list of them once the resource
    carries more than one EXDATE line — which is legal, and what repeated
    ``add("exdate", ...)`` produces.
    """
    stored = component.get("EXDATE")
    if stored is None:
        return []
    properties = stored if isinstance(stored, list) else [stored]
    return [item.dt for prop in properties for item in prop.dts]


def _duration_of(component: icalendar.Component) -> timedelta:
    """How long one occurrence lasts, for time-shifting an occurrence."""
    start = component.get("DTSTART")
    end = component.get("DTEND")
    if start is not None and end is not None:
        if isinstance(start.dt, datetime) and isinstance(end.dt, datetime):
            return end.dt - start.dt
    duration = component.get("DURATION")
    if duration is not None:
        return duration.dt
    return timedelta(hours=1)


def _end_of(component: icalendar.Component, start: datetime | date) -> datetime | date:
    """The event's end, tolerating events that carry neither DTEND nor DURATION."""
    end = component.get("DTEND")
    if end is not None:
        return end.dt
    duration = component.get("DURATION")
    if duration is not None:
        return start + duration.dt
    # An all-day event without DTEND occupies the whole day; a timed one with
    # no end is zero-length (the frontend renders it as a minimal block).
    if isinstance(start, datetime):
        return start
    return start + timedelta(days=1)


class RadicaleProvider(CalendarProvider):
    id = "radicale"
    display_name = "Radicale"

    def __init__(self, settings: RadicaleSettings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return bool(self._settings.url and self._settings.username)

    # ------------------------------------------------------------ plumbing --

    @contextmanager
    def _client(self) -> Iterator[caldav.DAVClient]:
        """A short-lived DAV client.

        python-caldav's client carries a connection pool that is not documented
        as thread-safe and FastAPI runs these sync routes in a threadpool, so
        each operation gets its own client rather than sharing one.
        """
        client = caldav.DAVClient(
            url=self._settings.url,
            username=self._settings.username,
            password=self._settings.password or None,
            timeout=self._settings.timeout,
        )
        try:
            yield client
        finally:
            client.close()

    @contextmanager
    def _errors(self, action: str) -> Iterator[None]:
        """Translate caldav/network failures into the provider taxonomy."""
        try:
            yield
        except ProviderError:
            raise
        except caldav.error.AuthorizationError as exc:
            raise ProviderError(
                "Radicale rejected the configured credentials — check "
                "RADICALE_USERNAME and RADICALE_PASSWORD"
            ) from exc
        except OSError as exc:
            # niquests/urllib3 chain several long nested exceptions; the sidebar
            # has room for one short line, so the detail goes to the log.
            logger.debug(f"Connection to Radicale failed: {exc!r}")
            raise ProviderError(
                f"Cannot reach Radicale at {self._settings.url} — is it running?"
            ) from exc
        except Exception as exc:
            raise ProviderError(f"Radicale {action} failed: {exc}") from exc

    @staticmethod
    def _supports_events(calendar: caldav.Calendar) -> bool:
        try:
            components = calendar.get_supported_components()
        except Exception as exc:
            # Unknown capability is not a reason to hide the calendar; the
            # query itself will come back empty if it holds nothing we can use.
            logger.debug(f"Could not read supported components of {calendar.url}: {exc}")
            return True
        return not components or any(str(c) == _VEVENT for c in components)

    @staticmethod
    def _describe(calendar: caldav.Calendar) -> _CalendarRef:
        props: dict[str, Any] = {}
        try:
            props = calendar.get_properties([dav.DisplayName(), ical_elem.CalendarColor()])
        except Exception as exc:
            logger.warning(f"Could not read properties of {calendar.url}: {exc}")
        color = _css_color(props.get(_CALENDAR_COLOR))
        return _CalendarRef(
            calendar=calendar,
            id=str(calendar.id or calendar.url),
            name=str(props.get(_DISPLAYNAME) or calendar.id or calendar.url),
            color=color,
            text_color=_text_color(color),
        )

    @staticmethod
    def _matches(ref: _CalendarRef, wanted: str) -> bool:
        return wanted in (ref.id, ref.name) or wanted.rstrip("/") == str(
            ref.calendar.url
        ).rstrip("/")

    def _visible_calendars(self, client: caldav.DAVClient) -> list[_CalendarRef]:
        """Every usable calendar, honouring a configured allow-list and order."""
        wanted = self._settings.calendar_ids
        refs: list[_CalendarRef] = []
        for calendar in client.principal().calendars():
            if not self._supports_events(calendar):
                continue
            ref = self._describe(calendar)
            if wanted and not any(self._matches(ref, w) for w in wanted):
                continue
            refs.append(ref)
        if wanted:
            # Configured order decides, so the first pinned calendar is the one
            # new events are created in.
            refs.sort(
                key=lambda ref: next(
                    (i for i, w in enumerate(wanted) if self._matches(ref, w)),
                    len(wanted),
                )
            )
        return refs
    def _locate(
        self, client: caldav.DAVClient, event_id: str, calendar_id: str | None
    ) -> tuple[_CalendarRef, caldav.Event]:
        """Find the one calendar resource that holds ``event_id`` (a UID)."""
        refs = self._visible_calendars(client)
        if calendar_id:
            refs = [ref for ref in refs if ref.id == calendar_id]
            if not refs:
                raise ProviderError(f"Unknown calendar {calendar_id!r} on Radicale")
        for ref in refs:
            # No time range is available here, so client-side filtering has to
            # stay off: python-caldav's date-less filter path clamps to
            # datetime.min, which has no local-time representation on Windows.
            found = ref.calendar.search(
                uid=event_id, post_filter=False, comp_class=caldav.Event
            )
            for resource in found:
                if resource.id == event_id:
                    return ref, resource
        raise ProviderError(f"Event {event_id!r} was not found on Radicale")

    # -------------------------------------------------------------- reading --

    @staticmethod
    def _map_component(
        component: icalendar.Component, *, event_id: str, ref: _CalendarRef
    ) -> CalendarEvent:
        dtstart = component.get("DTSTART")
        if dtstart is None:
            raise ValueError("event has no DTSTART")
        start = dtstart.dt
        end = _end_of(component, start)
        recurrence = component.get("RECURRENCE-ID")
        return CalendarEvent(
            id=event_id,
            title=str(component.get("SUMMARY") or "(no title)"),
            start=_iso(_localize(start)),
            end=_iso(_localize(end)),
            all_day=not isinstance(start, datetime),
            calendar_id=ref.id,
            provider_id=RadicaleProvider.id,
            color=ref.color,
            text_color=ref.text_color,
            recurrence_id=_iso(_localize(recurrence.dt)) if recurrence is not None else None,
        )

    def _map_resource(self, resource: caldav.Event, ref: _CalendarRef) -> CalendarEvent:
        return self._map_component(
            _master_of(resource.icalendar_instance), event_id=resource.id, ref=ref
        )

    def list_events(self, day_start: datetime, day_end: datetime) -> list[CalendarEvent]:
        with self._errors("fetching events"), self._client() as client:
            refs = self._visible_calendars(client)
            events: list[CalendarEvent] = []
            failures = 0
            for ref in refs:
                try:
                    # expand=True splits a recurring series into the occurrences
                    # that actually fall in the window, each carrying the
                    # RECURRENCE-ID that identifies it inside the series.
                    found = ref.calendar.search(
                        start=day_start, end=day_end, event=True, expand=True
                    )
                except Exception as exc:
                    failures += 1
                    logger.exception(
                        f"Radicale fetch failed for calendar {ref.name!r} on "
                        f"{day_start.date()}: {exc}"
                    )
                    continue
                for resource in found:
                    try:
                        events.append(self._map_resource(resource, ref))
                    except (ValueError, ProviderError) as exc:
                        logger.warning(
                            f"Skipping malformed event in {ref.name!r}: {exc}"
                        )
            if refs and failures == len(refs):
                raise ProviderError("Radicale request failed for every calendar")
            return events

    # -------------------------------------------------------------- writing --

    def create_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent:
        with self._errors("creating an event"), self._client() as client:
            refs = self._visible_calendars(client)
            if not refs:
                raise ProviderError("Radicale has no calendar that accepts events")
            target = refs[0]
            component = icalendar.Event()
            component.add("uid", str(uuid.uuid4()))
            component.add("summary", title)
            component.add("dtstamp", datetime.now(timezone.utc))
            component.add("dtstart", _localize(start))
            component.add("dtend", _localize(end))
            resource = target.calendar.save_event(ical=component)
            event_id = str(component["UID"])
            logger.info(f"Radicale event created in {target.name!r}: id={event_id}")
            return self._map_component(component, event_id=event_id, ref=target)

    @staticmethod
    def _occurrence_id(
        component: icalendar.Component, original_start: datetime
    ) -> datetime | date:
        """The RECURRENCE-ID/EXDATE value identifying this series' occurrence.

        It matches the master's date type (a DATE for all-day series) and is
        always expressed in the local IANA zone, so the written value resolves
        on the server and compares equal to the one read back later.
        """
        dtstart = component.get("DTSTART")
        if dtstart is not None and not isinstance(dtstart.dt, datetime):
            return original_start.date()
        return _localize(original_start)

    def _override_occurrence(
        self,
        instance: icalendar.Calendar,
        master: icalendar.Component,
        original_start: datetime,
        *,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
    ) -> icalendar.Component:
        """Replace one occurrence by writing an override into the resource.

        CalDAV has no per-instance edit call: an occurrence is changed by
        storing a second VEVENT that repeats the series UID and points at the
        occurrence's original start with RECURRENCE-ID.
        """
        recurrence_id = self._occurrence_id(master, original_start)
        existing = next(
            (
                event
                for event in _components(instance)
                if event.get("RECURRENCE-ID") is not None
                and event["RECURRENCE-ID"].dt == recurrence_id
            ),
            None,
        )

        duration = _duration_of(master)
        if start is None and end is None:
            start, end = recurrence_id, recurrence_id + duration
        elif start is None:
            start = end - duration  # type: ignore[operator]
        elif end is None:
            end = start + duration

        override = icalendar.Event()
        override.add("uid", str(master.get("UID")))
        override.add("dtstamp", datetime.now(timezone.utc))
        override.add("recurrence-id", recurrence_id)
        override.add(
            "summary",
            title or str((existing or master).get("SUMMARY") or "(no title)"),
        )
        override.add("dtstart", _localize(start))
        override.add("dtend", _localize(end))

        if existing is not None:
            instance.subcomponents.remove(existing)  # replace, never stack
        instance.add_component(override)
        return override

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
        with self._errors("updating an event"), self._client() as client:
            ref, resource = self._locate(client, event_id, calendar_id)
            with resource.edit_icalendar_instance() as instance:
                master = _master_of(instance)
                if original_start is not None and _is_recurring(master):
                    component = self._override_occurrence(
                        instance,
                        master,
                        original_start,
                        title=title,
                        start=start,
                        end=end,
                    )
                else:
                    component = master
                    self._apply(component, title=title, start=start, end=end)
            resource.save()
            logger.info(f"Radicale event {event_id} updated")
            return self._map_component(component, event_id=event_id, ref=ref)

    @staticmethod
    def _apply(
        component: icalendar.Component,
        *,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
    ) -> None:
        if title is not None:
            component["SUMMARY"] = title
        # Re-adding rather than assigning keeps the written value in the local
        # IANA zone, which is both DST-correct and always resolvable server-side.
        if start is not None:
            component.pop("DTSTART", None)
            component.add("DTSTART", _localize(start))
        if end is not None:
            component.pop("DTEND", None)
            component.add("DTEND", _localize(end))
        component.pop("DTSTAMP", None)
        component.add("DTSTAMP", datetime.now(timezone.utc))

    def delete_event(
        self,
        event_id: str,
        *,
        calendar_id: str | None = None,
        original_start: datetime | None = None,
    ) -> None:
        with self._errors("deleting an event"), self._client() as client:
            _, resource = self._locate(client, event_id, calendar_id)
            master = _master_of(resource.icalendar_instance)
            if original_start is not None and _is_recurring(master):
                # Dropping one occurrence must not destroy the series, so the
                # master gains an EXDATE instead of the resource being deleted.
                with resource.edit_icalendar_instance() as instance:
                    component = _master_of(instance)
                    excluded = self._occurrence_id(component, original_start)
                    if excluded not in _excluded(component):
                        component.add("exdate", excluded)
                    # An excluded occurrence can no longer happen, so an override
                    # that used to move it would only be dead weight.
                    for event in _components(instance):
                        if (
                            event.get("RECURRENCE-ID") is not None
                            and event["RECURRENCE-ID"].dt == excluded
                        ):
                            instance.subcomponents.remove(event)
                resource.save()
                logger.info(f"Radicale event {event_id} occurrence {excluded} removed")
                return
            resource.delete()
            logger.info(f"Radicale event {event_id} deleted")
