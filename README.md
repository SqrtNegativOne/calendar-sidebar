# Calendar Sidebar

Always-on-top Windows desktop sidebar showing today's Google Calendar events.
PyQt6 hosts a WebEngine rendering a Svelte/SvelteKit frontend; a FastAPI/uvicorn
backend runs on a background thread and handles Google OAuth2 + event CRUD.

## Quick start

```
bun install          # install frontend deps (once)
bun run build        # compile the Svelte frontend (required once)
uv run widget.py     # start the app
```

Or double-click `run.vbs` for a silent, no-console launch.

Full architecture, OAuth setup, debugging table, and conventions live in
[AGENTS.md](./AGENTS.md).
