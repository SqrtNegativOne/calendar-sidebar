# Calendar Sidebar

Always-on-top Windows desktop sidebar showing today's events from a
**Radicale / CalDAV** server. PyQt6 hosts a WebEngine rendering a
Svelte/SvelteKit frontend; a FastAPI/uvicorn backend runs on a background thread
and talks CalDAV to the calendar server.

## Quick start

```
# 1. have Radicale running (see below)
bun install          # install frontend deps (once)
bun run build        # compile the Svelte frontend (required once)
uv run widget.py     # start the app
```

Or double-click `run.vbs` for a silent, no-console launch.

Point the sidebar at your server in `.env` (see `.env.example`):

```
RADICALE_URL=http://127.0.0.1:5232
RADICALE_USERNAME=me
RADICALE_PASSWORD=...
# optional: comma-separated calendars to read (first one receives new events)
# RADICALE_CALENDAR_IDS=SqrtCal,Work
```

There is no sign-in step: credentials are read from `.env` at startup. Leave
`RADICALE_USERNAME` unset and the sidebar says so instead of failing.

Full architecture, CalDAV notes, debugging table, and conventions live in
[AGENTS.md](./AGENTS.md).

## Using it

- **Click or drag on empty grid space** to create a task at that time.
- **Drag / resize** an event to move it or change its length.
- **Double-click a title** to rename it.
- **×** on an event removes it (for one occurrence of a recurring event, just
  that occurrence is excluded — the series stays).
- Events are re-fetched every 30 s while the panel is open.

Multiple calendars are merged, each event keeping its calendar's colour. All-day
events count towards "today is not empty" but are not drawn in the time grid.

## Troubleshooting

### The sidebar says "No calendar configured"

`RADICALE_USERNAME` (or `RADICALE_URL`) is missing from `.env`. Set them and
restart the app — the configuration is read once at startup.

### `Cannot reach Radicale at ...` in the sidebar

The calendar server isn't running, or the URL/port is wrong. Start Radicale
(e.g. `python C:\radicale\run_radicale.py`) and hit **Retry**.

### `Radicale rejected the configured credentials`

The htpasswd entry doesn't match. Check `RADICALE_USERNAME` / `RADICALE_PASSWORD`
against `C:\radicale\users`, and that `htpasswd_encryption` in
`C:\radicale\config` matches how the file was written (`plain` in this setup).

### A PUT is rejected with `VTIMEZONE components must contain a valid TZID`

The event carries a `TZID` the server can't resolve — typically a Windows zone
name (`India Standard Time`) or a bare offset (`+05:30`) written by a client that
used `datetime.now().astimezone()` on Windows. The sidebar always writes IANA
zone names; to repair existing data, recreate the event or rewrite its
`DTSTART`/`DTEND` with a proper `TZID` (or `Z`).

### Ports

The API server binds `8764` and scans upward if it's taken. Nothing external
points at that port, so a fallback is harmless.
