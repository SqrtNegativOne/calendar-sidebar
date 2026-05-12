# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "PyQt6>=6.7",
#   "PyQt6-WebEngine>=6.7",
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
import http.server
import socket
import ctypes
from ctypes import byref, c_int
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QColor, QKeySequence, QShortcut, QRegion


_BUILD_DIR = Path(__file__).parent / "build"

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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(directory: Path, port: int) -> None:
    class _H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *_):
            pass

    http.server.HTTPServer(("127.0.0.1", port), _H).serve_forever()


class CalendarWindow(QMainWindow):
    def __init__(self, port: int) -> None:
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

        # Initial geometry — open state
        self.setGeometry(
            screen.right() - _OPEN_W, screen.top(),
            _OPEN_W, screen.height(),
        )
        # Mask to exact window rect — OS-level shape, no corners, no strip
        self.setMask(QRegion(0, 0, _OPEN_W, screen.height()))

        self._view = QWebEngineView(self)
        self._view.setGeometry(0, 0, _OPEN_W, screen.height())
        self._view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self._view.loadFinished.connect(self._on_loaded)
        self._view.load(QUrl(f"http://127.0.0.1:{port}/"))

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
        except Exception:
            pass

    def _set_width(self, w: int) -> None:
        s = self._screen
        self.setGeometry(s.right() - w, s.top(), w, s.height())
        self._view.setGeometry(0, 0, w, s.height())
        self._apply_mask()

    def _apply_mask(self) -> None:
        """Shape the OS window to exactly the visible content — nothing more."""
        w, h = self.width(), self.height()
        if self._is_open:
            # Full rectangle: panel + tab
            self.setMask(QRegion(0, 0, w, h))
        else:
            # Just the tab button, vertically centered
            tab_y = (h - _TAB_H) // 2
            self.setMask(QRegion(0, tab_y, w, _TAB_H))

    def _on_loaded(self, ok: bool) -> None:
        if ok:
            QTimer.singleShot(80, lambda: self._view.page().runJavaScript(_INJECT_JS))
            QTimer.singleShot(120, self._poll_timer.start)

    def _poll_open(self) -> None:
        self._view.page().runJavaScript(
            "!!document.querySelector('.widget.open')",
            self._on_open_state,
        )

    def _on_open_state(self, is_open: bool) -> None:
        if is_open == self._is_open:
            return
        self._is_open = is_open
        if is_open:
            # Expand immediately — panel needs room before it animates out
            self._set_width(_OPEN_W)
        else:
            # Let the 250ms CSS transition finish, then shrink + remask
            QTimer.singleShot(_CLOSE_DELAY_MS, lambda: self._set_width(_CLOSED_W))


def main() -> None:
    if not _BUILD_DIR.exists():
        print("ERROR: build/ not found — run  npm run build  first.", file=sys.stderr)
        sys.exit(1)

    port = _free_port()
    threading.Thread(target=_serve, args=(_BUILD_DIR, port), daemon=True).start()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    win = CalendarWindow(port)
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
