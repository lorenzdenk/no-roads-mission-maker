"""Startpunkt der Desktop-App: lokaler Server + Fenster (pywebview) oder Standardbrowser."""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

from .server import App, serve

TITLE = "No Roads Mission Maker"


def _log_setup():
    """Ohne Konsole (PyInstaller --windowed) gibt es kein stdout/stderr: in Datei umleiten."""
    if sys.stdout is None or sys.stderr is None:
        d = os.path.join(os.path.expanduser("~"), ".no-roads-mission-maker")
        os.makedirs(d, exist_ok=True)
        f = open(os.path.join(d, "log.txt"), "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def main(argv=None) -> int:
    _log_setup()
    argv = list(sys.argv[1:] if argv is None else argv)
    use_browser = "--browser" in argv
    no_open = "--no-open" in argv
    app, srv = serve(App())
    url = f"http://127.0.0.1:{app.port}/"

    if not use_browser:
        try:
            import webview  # type: ignore

            win = webview.create_window(TITLE, url, width=1480, height=920, min_size=(1100, 720), background_color="#7fd0ff")

            def picker(kind):
                if kind == "folder":
                    r = win.create_file_dialog(webview.FOLDER_DIALOG)
                elif kind == "gpx":
                    r = win.create_file_dialog(webview.OPEN_DIALOG, file_types=("GPX (*.gpx)", "Alle Dateien (*.*)"))
                else:
                    r = win.create_file_dialog(webview.OPEN_DIALOG, file_types=("OSM PBF (*.pbf)", "Alle Dateien (*.*)"))
                return r[0] if r else None

            app.picker = picker
            webview.start()
            return 0
        except Exception as e:  # noqa: BLE001  -> kein pywebview/WebView2: im Browser oeffnen
            print("Fenster nicht verfuegbar, oeffne Browser:", e, file=sys.stderr)

    print(f"{TITLE}: {url}")
    if not no_open:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
