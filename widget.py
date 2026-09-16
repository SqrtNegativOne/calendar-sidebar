"""Desktop calendar sidebar — always-on-top PyQt6 window.

Usage:
  1. bun run build          (build the Svelte app once)
  2. uv run widget.py       (uv installs deps automatically, then launches)

Press Ctrl+Q to quit, or use the tray icon menu. Escape is left to the
web UI (e.g. cancelling the inline title editor).

Dependencies are declared in pyproject.toml (installed via uv).
"""

import sys
import threading
import ctypes
from ctypes import byref, c_int
from pathlib import Path

from loguru import logger
from PyQt6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QKeySequence,
    QShortcut,
    QRegion,
)
from PyQt6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)


_BUILD_DIR = Path(__file__).parent / "build"
_LOG_FILE = Path(__file__).parent / "log" / "widget.log"
_ICON_FILE = Path(__file__).parent / "icon.png"

_INJECT_JS = r"""
(function () {
  const s = document.createElement('style');
  s.textContent = `
    html, body { background: transparent !important; }
    :root     { --bg: transparent !important; }
    .demo     { display: none !important; }
  `;
  document.head.appendChild(s);
})();
"""

_OPEN_W  = 306   # tab(22) + panel(280) + 4px buffer
_CLOSED_W = 26   # tab(22) + 4px buffer

# Height of the toggle tab button — must match the CSS .tab { height: 64px }
_TAB_H = 64

# Must exceed the Svelte close-transition duration (250 ms)
_CLOSE_DELAY_MS = 300

# UI load retry: exponential backoff from 1 s, capped at 30 s
_MAX_LOAD_ATTEMPTS = 5
_RETRY_BASE_MS = 1000
_RETRY_MAX_MS = 30_000

# Windows per-session named mutex — released automatically on process exit
_ERROR_ALREADY_EXISTS = 183
_MUTEX_NAME = "Local\\CalendarSidebarSingleInstance"

# Windows DWM: disable rounded corners
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1


def _start_server(server) -> None:
    from server import run

    logger.debug("Server thread starting")
    run(server)


def _acquire_single_instance() -> bool:
    """True if no other instance already owns the per-session named mutex."""
    if sys.platform != "win32":
        return True
    ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    return ctypes.windll.kernel32.GetLastError() != _ERROR_ALREADY_EXISTS


class CalendarWindow(QMainWindow):
    def __init__(self, api_port: int, server) -> None:
        super().__init__()
        self._port = api_port
        self._server = server

        screen = QApplication.primaryScreen().availableGeometry()
        self._screen = screen
        # Matches Svelte's initial open = $state(true)
        self._is_open: bool = True
        self._page_loaded: bool = False
        self._ready_check_inflight: bool = False
        self._load_inflight: bool = False
        self._load_attempts: int = 0
        self._menu: QMenu | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Calendar Sidebar")
        if _ICON_FILE.exists():
            self.setWindowIcon(QIcon(str(_ICON_FILE)))

        # Initial geometry — open state
        self.setGeometry(
            screen.right() - _OPEN_W, screen.top(),
            _OPEN_W, screen.height(),
        )
        self.setMask(QRegion(0, 0, _OPEN_W, screen.height()))

        self._view = QWebEngineView(self)
        self._view.setGeometry(0, 0, _OPEN_W, screen.height())
        self._view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self._view.page().featurePermissionRequested.connect(self._on_feature_permission)
        self._view.loadFinished.connect(self._on_loaded)

        # Poll until uvicorn has bound its port, then load the SPA — no fixed
        # sleep, so startup is fast when the server is quick and reliable
        # even when it is slow
        self._base_url = f"http://127.0.0.1:{api_port}"
        self._net = QNetworkAccessManager(self)
        self._ready_timer = QTimer(self)
        self._ready_timer.setInterval(150)
        self._ready_timer.timeout.connect(self._check_server_ready)
        self._ready_timer.start()
        logger.debug(f"Waiting for API server at {self._base_url} before loading UI")

        # Poll the Svelte widget class list to track open/closed state
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(150)
        self._poll_timer.timeout.connect(self._poll_open)

        # Escape is deliberately not bound to quit — the web layer uses it to
        # cancel the inline title editor and dismiss UI, and a global shortcut
        # would swallow those.
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(QApplication.quit)

        self._build_tray()

    def _on_feature_permission(
        self, origin: QUrl, feature: QWebEnginePage.Feature
    ) -> None:
        """Grant clipboard access to the local UI only; deny everything else.

        The Connect button copies a provider sign-in URL to the clipboard, and
        WebEngine gates clipboard writes behind a feature permission.
        """
        page = self._view.page()
        allowed = (
            origin.host() in {"127.0.0.1", "localhost"}
            and feature == QWebEnginePage.Feature.ClipboardReadWrite
        )
        page.setFeaturePermission(
            origin,
            feature,
            QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            if allowed
            else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser,
        )

    def _build_tray(self) -> None:
        """System tray icon with show/hide + quit actions."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available — tray icon skipped")
            return
        icon = QIcon(str(_ICON_FILE)) if _ICON_FILE.exists() else QIcon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Calendar Sidebar")

        # Keep a Python reference: QSystemTrayIcon.setContextMenu() does not
        # take ownership, so a local QMenu would be garbage-collected and the
        # context menu would silently stop working.
        menu = QMenu()
        self._menu = menu
        act_toggle = QAction("Show / Hide sidebar", menu)
        act_toggle.triggered.connect(self._toggle_visible)
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_toggle)
        menu.addSeparator()
        menu.addAction(act_quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        logger.debug("Tray icon created")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Left-click on the tray icon also toggles the window
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visible()

    def _toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
            logger.debug("Window hidden via tray")
        else:
            self.show()
            logger.debug("Window shown via tray")

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._disable_dwm_corners()
        self._sync_poll_timer()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        # There is nothing to poll while the sidebar is hidden
        self._poll_timer.stop()

    def _disable_dwm_corners(self) -> None:
        """Tell the Windows DWM compositor not to round this window's corners."""
        try:
            hwnd = int(self.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                _DWMWA_WINDOW_CORNER_PREFERENCE,
                byref(c_int(_DWMWCP_DONOTROUND)),
                4,
            )
            logger.debug("DWM rounded corners disabled")
        except Exception as exc:
            logger.warning(f"Could not disable DWM rounded corners: {exc}")

    def _set_width(self, w: int) -> None:
        s = self._screen
        self.setGeometry(s.right() - w, s.top(), w, s.height())
        self._view.setGeometry(0, 0, w, s.height())
        self._apply_mask()

    def _apply_mask(self) -> None:
        """Shape the OS window to exactly the visible content — nothing more."""
        w, h = self.width(), self.height()
        if self._is_open:
            self.setMask(QRegion(0, 0, w, h))
        else:
            tab_y = (h - _TAB_H) // 2
            self.setMask(QRegion(0, tab_y, w, _TAB_H))

    def _check_server_ready(self) -> None:
        if self._page_loaded or self._ready_check_inflight:
            return
        self._ready_check_inflight = True
        url = QUrl(f"{self._base_url}/api/auth/status")
        reply = self._net.get(QNetworkRequest(url))
        reply.finished.connect(lambda: self._on_ready_check(reply))

    def _on_ready_check(self, reply: QNetworkReply) -> None:
        ok = reply.error() == QNetworkReply.NetworkError.NoError
        reply.deleteLater()
        self._ready_check_inflight = False
        if self._page_loaded or self._load_inflight:
            return
        if ok:
            logger.info("API server is up — loading UI")
            self._ready_timer.stop()
            self._load_inflight = True
            self._view.load(QUrl(f"{self._base_url}/"))
        else:
            logger.debug("API server not ready yet — retrying")

    def _on_loaded(self, ok: bool) -> None:
        self._load_inflight = False
        if ok:
            self._page_loaded = True
            self._load_attempts = 0
            logger.info("WebEngine page loaded successfully")
            QTimer.singleShot(80, lambda: self._view.page().runJavaScript(_INJECT_JS))
            QTimer.singleShot(120, self._sync_poll_timer)
            return

        self._load_attempts += 1
        logger.error(
            f"WebEngine failed to load {self._base_url}/ "
            f"(attempt {self._load_attempts}/{_MAX_LOAD_ATTEMPTS}) — server may have died"
        )
        if self._load_attempts >= _MAX_LOAD_ATTEMPTS:
            logger.critical("Giving up on UI load — restart the app to retry")
            return
        delay = min(_RETRY_BASE_MS * 2 ** (self._load_attempts - 1), _RETRY_MAX_MS)
        logger.info(f"Retrying UI load in {delay} ms")
        self._ready_timer.stop()
        QTimer.singleShot(delay, self._ready_timer.start)

    def _sync_poll_timer(self) -> None:
        """Only poll the page while it is loaded and actually on screen."""
        if self._page_loaded and self.isVisible():
            self._poll_timer.start()
        else:
            self._poll_timer.stop()

    def _poll_open(self) -> None:
        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010
            )
        except Exception:
            pass
        self._view.page().runJavaScript(
            "!!document.querySelector('.widget.open')",
            self._on_open_state,
        )

    def _on_open_state(self, is_open: bool) -> None:
        if is_open == self._is_open:
            return
        self._is_open = is_open
        logger.debug(f"Sidebar state changed: {'open' if is_open else 'closed'}")
        if is_open:
            self._set_width(_OPEN_W)
        else:
            QTimer.singleShot(_CLOSE_DELAY_MS, lambda: self._set_width(_CLOSED_W))


    def shutdown(self) -> None:
        """Stop timers and ask uvicorn for a graceful shutdown."""
        self._ready_timer.stop()
        self._poll_timer.stop()
        if self._server is not None:
            logger.info("Requesting API server shutdown")
            self._server.should_exit = True


def main() -> None:
    if sys.platform == "win32":
        # Set AppUserModelID so Windows groups this process correctly in the taskbar
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.calendar.sidebar.v1")

    logger.add(_LOG_FILE, rotation="10 MB", retention="1 week", level="DEBUG", encoding="utf-8")
    logger.info("Calendar sidebar starting up")

    if not _acquire_single_instance():
        logger.warning("Another Calendar Sidebar instance is already running — exiting")
        ctypes.windll.user32.MessageBoxW(
            0, "Calendar Sidebar is already running.", "Calendar Sidebar", 0x40
        )
        sys.exit(0)

    if not _BUILD_DIR.exists():
        logger.critical(f"Build directory not found: {_BUILD_DIR} — run  bun run build  first")
        sys.exit(1)

    from server import create_server, find_free_port

    port = find_free_port()
    server = create_server(port)
    logger.info(f"Starting API server thread on port {port}")
    threading.Thread(target=_start_server, args=(server,), daemon=True).start()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    logger.info("Creating main window")
    win = CalendarWindow(port, server)
    app.aboutToQuit.connect(win.shutdown)
    win.show()

    logger.info("Entering Qt event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception(f"Unhandled exception in main: {exc}")