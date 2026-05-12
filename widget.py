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
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QColor, QKeySequence, QShortcut


_BUILD_DIR = Path(__file__).parent / "build"

# Hide the demo prose, make the page chrome transparent so only the
# widget panel/tab paints — the OS desktop shows through the rest.
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

# tab(28px) + panel(280px) + 4px rounding buffer
_WIDGET_W = 312


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

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool          # no taskbar entry, no alt-tab
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(
            screen.right() - _WIDGET_W,
            screen.top(),
            _WIDGET_W,
            screen.height(),
        )

        self._view = QWebEngineView(self)
        self._view.setGeometry(0, 0, _WIDGET_W, screen.height())
        self._view.page().setBackgroundColor(QColor(0, 0, 0, 0))

        # Wait briefly after load so Svelte finishes hydration before CSS injection.
        self._view.loadFinished.connect(self._on_loaded)
        self._view.load(QUrl(f"http://127.0.0.1:{port}/"))

        for seq in ("Ctrl+Q", "Escape"):
            QShortcut(QKeySequence(seq), self).activated.connect(QApplication.quit)

    def _on_loaded(self, ok: bool) -> None:
        if ok:
            QTimer.singleShot(80, lambda: self._view.page().runJavaScript(_INJECT_JS))


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
