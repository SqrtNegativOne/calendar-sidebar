"""Composition root for the Calendar Sidebar backend.

Reads configuration from the environment, wires concrete provider
implementations into :class:`~backend.registry.ProviderRegistry`, and builds the
FastAPI app / uvicorn server. This is the only module that knows which concrete
providers exist — everything else depends on the interfaces.

Run standalone for testing:
  uv run server.py

Normally started by widget.py automatically.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from loguru import logger

from backend import TokenStore
from backend.app import create_app as build_app
from backend.google import GoogleAuth, GoogleCalendarProvider
from backend.microsoft import MicrosoftAuth, MicrosoftCalendarProvider
from backend.registry import ProviderBundle, ProviderRegistry

load_dotenv()

# oauthlib raises a bare `Warning` exception when the authorization server
# returns a scope set that differs from the request (Google normalises/
# reorders scopes), which otherwise turns a successful grant into a 500.
# Relax it and validate the granted scopes ourselves instead.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

_LOG_FILE = Path(__file__).parent / "log" / "server.log"
_BUILD_DIR = Path(__file__).parent / "build"
_TOKEN_FILE = Path(__file__).parent / ".tokens.json"

_DEFAULT_PORT = 8764
_DEFAULT_REDIRECT = f"http://localhost:{_DEFAULT_PORT}/api/auth/callback"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def build_registry(store: TokenStore | None = None) -> ProviderRegistry:
    """Construct the provider registry from environment configuration.

    Missing credentials do not abort startup — the provider is simply reported
    as unconfigured and its Connect button is hidden in the UI.
    """
    store = store or TokenStore(_TOKEN_FILE)

    google_auth = GoogleAuth(
        store,
        client_id=_env("GOOGLE_CLIENT_ID"),
        client_secret=_env("GOOGLE_CLIENT_SECRET"),
        redirect_uri=_env("GOOGLE_REDIRECT_URI") or _DEFAULT_REDIRECT,
    )
    microsoft_auth = MicrosoftAuth(
        store,
        client_id=_env("MICROSOFT_CLIENT_ID"),
        client_secret=_env("MICROSOFT_CLIENT_SECRET"),
        redirect_uri=_env("MICROSOFT_REDIRECT_URI")
        or f"http://localhost:{_DEFAULT_PORT}/api/auth/microsoft/callback",
        tenant_id=_env("MICROSOFT_TENANT_ID", "common"),
    )

    return ProviderRegistry(
        [
            ProviderBundle(
                auth=google_auth,
                calendar=GoogleCalendarProvider(
                    google_auth, calendar_id=_env("GOOGLE_CALENDAR_ID") or None
                ),
            ),
            ProviderBundle(
                auth=microsoft_auth,
                calendar=MicrosoftCalendarProvider(
                    microsoft_auth, calendar_id=_env("MICROSOFT_CALENDAR_ID") or None
                ),
            ),
        ]
    )


def create_app(
    registry: ProviderRegistry | None = None, build_dir: Path | None = None
):
    """Build the FastAPI app, defaulting to the env-configured registry."""
    return build_app(
        registry or build_registry(),
        build_dir=build_dir or _BUILD_DIR,
    )


def _warn_redirect_ports(registry: ProviderRegistry, port: int) -> None:
    for bundle in registry.all():
        uri = bundle.auth.redirect_uri()
        if not uri:
            continue
        redirect_port = urlparse(uri).port
        if redirect_port is not None and redirect_port != port:
            logger.warning(
                f"Server will run on port {port} but {bundle.auth.display_name}'s "
                f"redirect URI points at port {redirect_port} — first-time login "
                "may fail until tokens exist or the redirect URI is updated"
            )


def find_free_port(preferred: int = _DEFAULT_PORT) -> int:
    """Return `preferred` if free, otherwise scan upward for the first free port."""
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found near {preferred}")


def create_server(port: int, app=None):
    """Build (but don't start) a uvicorn server. Returns the handle so callers
    can request a graceful shutdown via `server.should_exit = True`."""
    import uvicorn

    app = app if app is not None else create_app()
    _warn_redirect_ports(app.state.registry, port)
    return uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )


def run(server) -> None:
    """Start the given uvicorn server. Blocks until shutdown. Called by widget.py."""
    logger.info(f"API server starting on http://127.0.0.1:{server.config.port}")

    registry: ProviderRegistry = server.config.app.state.registry
    if not registry.any_configured():
        logger.error(
            "No calendar provider configured — copy .env.example to .env and "
            "fill in Google and/or Microsoft OAuth credentials"
        )
    else:
        for bundle in registry.all():
            state = "configured" if bundle.auth.configured else "not configured"
            log = logger.info if bundle.auth.configured else logger.warning
            log(f"{bundle.auth.display_name}: {state}")

    server.run()


if __name__ == "__main__":
    logger.add(_LOG_FILE, rotation="10 MB", retention="1 week", level="DEBUG", encoding="utf-8")
    _app = create_app()
    run(create_server(find_free_port(), _app))
