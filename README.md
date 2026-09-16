# Calendar Sidebar

Always-on-top Windows desktop sidebar showing today's **Google** and
**Microsoft** Calendar events. PyQt6 hosts a WebEngine rendering a
Svelte/SvelteKit frontend; a FastAPI/uvicorn backend runs on a background thread
and handles OAuth2 + event CRUD per provider.

## Quick start

```
bun install          # install frontend deps (once)
bun run build        # compile the Svelte frontend (required once)
uv run widget.py     # start the app
```

Or double-click `run.vbs` for a silent, no-console launch.

Configure either or both providers in `.env` (see `.env.example`) and click
**Connect** in the sidebar. For each provider, the widget copies a consent URL
to your clipboard — paste it into any browser to sign in; the sidebar detects
the callback automatically.

Full architecture, OAuth setup, debugging table, and conventions live in
[AGENTS.md](./AGENTS.md).

## Provider setup

### Google

1. Create an OAuth 2.0 credential (type: **Web application**) in
   [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2. Add `http://localhost:8764/api/auth/callback` as an **Authorized redirect URI**.
3. Set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env`.

### Microsoft

1. Register an app in Azure / Entra ID (**App registrations → New registration**).
2. Add a **Web** redirect URI `http://localhost:8764/api/auth/microsoft/callback`
   under **Authentication**.
3. Add Microsoft Graph **delegated** permission `Calendars.ReadWrite` under
   **API permissions**.
4. Create a client secret under **Certificates & secrets**.
5. Set `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` in `.env`
   (`MICROSOFT_TENANT_ID` defaults to `common`).

## Troubleshooting

### `Error 400: redirect_uri_mismatch` (Google)

`GOOGLE_REDIRECT_URI` must be byte-for-byte identical to an **Authorized
redirect URI** on the OAuth client (Google treats `localhost` and `127.0.0.1`
as different values). For the widget, register:

```
http://localhost:8764/api/auth/callback
```

If you also develop against the Vite dev server, register its URI too:

```
http://localhost:5173/api/auth/callback
```

Google Cloud Console → **APIs & Services** → **Credentials** → the OAuth 2.0
client ID → **Authorized redirect URIs** → add → **Save**. Changes can take a
few minutes to propagate. The redirect URI used is the `GOOGLE_REDIRECT_URI`
value in `.env`, and the API port must match it.

### `AADSTS50011: redirect URI mismatch` (Microsoft)

`MICROSOFT_REDIRECT_URI` must match a **Web** redirect URI on the app
registration exactly. Register
`http://localhost:8764/api/auth/microsoft/callback`, or set the variable to
whatever you registered.

### Port fallback breaks first-time login

The API scans upward for a free port if `8764` is taken. OAuth redirect URIs
are fixed, so either free the port or point the provider's redirect variable at
the port the server actually reported in `log/widget.log`. Once at least one
provider has a stored token, the port no longer matters for it.
