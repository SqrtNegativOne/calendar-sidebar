# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "PyQt6>=6.7",
#   "PyQt6-WebEngine>=6.7",
#   "fastapi>=0.115",
#   "uvicorn>=0.34",
#   "google-auth>=2.38",
#   "google-auth-oauthlib>=1.2",
#   "google-api-python-client>=2.166",
#   "python-dotenv>=1.0",
#   "loguru>=0.7",
# ]
# ///
"""Desktop calendar sidebar — always-on-top PyQt6 window.

Usage:
  1. npm run build          (build the Svelte app once)
  2. uv run widget.py       (uv installs deps automatically, then launches)

Press Ctrl+Q or Escape to quit.
"""

import sys
import threading
import ctypes
from ctypes import byref, c_int
from pathlib import Path

from loguru import logger
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QColor, QKeySequence, QShortcut, QRegion, QIcon


_BUILD_DIR = Path(__file__).parent / "build"
_LOG_FILE = Path(__file__).parent / "widget.log"
_ICON_FILE = Path(__file__).parent / "icon.png"
_API_PORT = 8765

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

_OPEN_W  = 312   # tab(28) + panel(280) + 4px buffer
_CLOSED_W = 32   # tab(28) + 4px buffer

# Height of the toggle tab button — must match the CSS .tab { height: 80px }
_TAB_H = 80

# Must exceed the Svelte close-transition duration (250 ms)
_CLOSE_DELAY_MS = 300

# Windows DWM: disable rounded corners
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1


def _start_server() -> None:
    logger.debug("Server thread starting")
    from server import run
    run(_API_PORT)


class CalendarWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        screen = QApplication.primaryScreen().availableGeometry()
        self._screen = screen
        # Matches Svelte's initial open = $state(true)
        self._is_open: bool = True

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
        self._view.loadFinished.connect(self._on_loaded)

        # Give uvicorn a moment to bind before the WebEngine makes its first request
        QTimer.singleShot(900, lambda: self._view.load(QUrl(f"http://127.0.0.1:{_API_PORT}/")))
        logger.debug(f"UI will load from http://127.0.0.1:{_API_PORT}/ after 900 ms")

        # Poll the Svelte widget class list to track open/closed state
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(150)
        self._poll_timer.timeout.connect(self._poll_open)

        for seq in ("Ctrl+Q", "Escape"):
            QShortcut(QKeySequence(seq), self).activated.connect(QApplication.quit)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._disable_dwm_corners()

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

    def _on_loaded(self, ok: bool) -> None:
        if ok:
            logger.info("WebEngine page loaded successfully")
            QTimer.singleShot(80, lambda: self._view.page().runJavaScript(_INJECT_JS))
            QTimer.singleShot(120, self._poll_timer.start)
        else:
            logger.error(
                f"WebEngine failed to load http://127.0.0.1:{_API_PORT}/ — "
                "server may not have started in time"
            )

    def _poll_open(self) -> None:
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


def main() -> None:
    if sys.platform == "win32":
        # Set AppUserModelID so Windows groups this process correctly in the taskbar
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.calendar.sidebar.v1")

    logger.add(_LOG_FILE, rotation="1 MB", retention=3, level="DEBUG", encoding="utf-8")
    logger.info("Calendar sidebar starting up")

    if not _BUILD_DIR.exists():
        logger.critical(f"Build directory not found: {_BUILD_DIR} — run  npm run build  first")
        sys.exit(1)

    logger.info("Starting API server thread")
    threading.Thread(target=_start_server, daemon=True).start()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    logger.info("Creating main window")
    win = CalendarWindow()
    win.show()

    logger.info("Entering Qt event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception(f"Unhandled exception in main: {exc}")