"""Start Blendora.

Default: desktop window (pywebview) with the local server inside the same process;
falls back to the default web browser when no webview is available.
"""
import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATA = Path(os.environ.get("BLENDORA_HOME", Path.home() / "Blendora"))
DATA.mkdir(parents=True, exist_ok=True)
# librosa uses numba's on-disk cache; inside Program Files that folder is read-only.
os.environ.setdefault("NUMBA_CACHE_DIR", str(DATA / "cache" / "numba"))

# A windowed .exe has no console: send output to a log file instead of crashing on print().
if sys.stdout is None or sys.stderr is None:
    _log = open(DATA / "blendora.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("Brak wolnego portu")


def start_server(port: int):
    import uvicorn

    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(300):
        if server.started:
            return server, th
        time.sleep(0.05)
    raise RuntimeError("Serwer nie wystartował")


class JsApi:
    """Functions callable from the page as window.pywebview.api.*"""

    def pick_folder(self):
        import webview

        res = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else None


def open_window(url: str) -> bool:
    try:
        import webview
    except Exception:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window("Blendora", url, width=1440, height=900, min_size=(1000, 680), background_color="#0b0d12", js_api=JsApi())
        webview.start()  # blocks until the window is closed
        return True
    except Exception as exc:
        print(f"Okno aplikacji niedostępne ({exc}), otwieram przeglądarkę.")
        return False


def main():
    ap = argparse.ArgumentParser(description="Blendora - harmonic mixing & mashup studio")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--browser", action="store_true", help="otwórz w przeglądarce zamiast w oknie aplikacji")
    ap.add_argument("--no-browser", "--server", dest="server_only", action="store_true", help="tylko serwer")
    ap.add_argument("--selftest", action="store_true", help="sprawdź analizę audio i zakończ")
    args = ap.parse_args()

    if args.selftest:
        from app.selftest import run

        sys.exit(0 if run() else 1)

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    server, th = start_server(port)
    print(f"Blendora działa pod adresem {url}  (Ctrl+C aby zakończyć)")
    if not args.server_only and not args.browser and open_window(url):
        server.should_exit = True
        return
    if not args.server_only:
        webbrowser.open(url)
    try:
        while th.is_alive():
            th.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
