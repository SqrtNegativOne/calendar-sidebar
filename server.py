"""Composition root for the Calendar Sidebar backend.

Reads configuration from the environment, wires the concrete provider into a
:class:`~backend.registry.ProviderRegistry`, and builds the FastAPI app /
uvicorn server. This is the only module that knows which concrete provider
exists — everything else depends on the interface.

Run standalone for testing:
  uv run server.py

Normally started by widget.py automatically.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from backend.app import create_app as build_app
from backend.radicale import RadicaleProvider, RadicaleSettings
from backend.registry import ProviderRegistry

load_dotenv()

_LOG_FILE = Path(__file__).parent / "log" / "server.log"
_BUILD_DIR = Path(__file__).parent / "build"

_DEFAULT_PORT = 8764
_DEFAULT_RADICALE_URL = "http://127.0.0.1:5232"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def build_registry() -> ProviderRegistry:
    """Construct the provider registry from environment configuration.

    A missing URL or username does not abort startup — the provider is simply
    reported as unconfigured and the sidebar explains what to set.
    """
    calendar_ids = tuple(
        item.strip() for item in _env("RADICALE_CALENDAR_IDS").split(",") if item.strip()
    )
    return ProviderRegistry(
        [
            RadicaleProvider(
                RadicaleSettings(
                    url=_env("RADICALE_URL") or _DEFAULT_RADICALE_URL,
                    username=_env("RADICALE_USERNAME"),
                    password=_env("RADICALE_PASSWORD"),
                    calendar_ids=calendar_ids,
                )
            )
        ]
    )


def create_app(registry: ProviderRegistry | None = None, build_dir: Path | None = None):
    """Build the FastAPI app, defaulting to the env-configured registry."""
    return build_app(
        registry or build_registry(),
        build_dir=build_dir or _BUILD_DIR,
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
    return uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )


def run(server) -> None:
    """Start the given uvicorn server. Blocks until shutdown. Called by widget.py."""
    logger.info(f"API server starting on http://127.0.0.1:{server.config.port}")

    registry: ProviderRegistry = server.config.app.state.registry
    if not registry.has_any_configured():
        logger.error(
            "No calendar provider configured — copy .env.example to .env and set "
            "RADICALE_URL, RADICALE_USERNAME and RADICALE_PASSWORD"
        )
    else:
        for provider in registry.all():
            state = "configured" if provider.configured else "not configured"
            log = logger.info if provider.configured else logger.warning
            log(f"{provider.display_name}: {state}")

    server.run()


if __name__ == "__main__":
    logger.add(_LOG_FILE, rotation="10 MB", retention="1 week", level="DEBUG", encoding="utf-8")
    _app = create_app()
    run(create_server(find_free_port(), _app))
