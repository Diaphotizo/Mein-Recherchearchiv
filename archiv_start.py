"""Portable, loopback-only launcher for this copy of Mein Recherchearchiv."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent
APP_ID = "bip-recherche-local-v1"
COLLECTION_PATHS = {"archive": "/", "bip": "/", "courts": "/icc-icj/", "un": "/un/"}


def root_fingerprint(root: Path) -> str:
    """Identify a local archive without disclosing its absolute path over HTTP."""
    normalized = os.path.normcase(str(root.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_port(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Der Port muss eine ganze Zahl zwischen 1024 und 65535 sein.")
    try:
        port = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Der Port muss eine ganze Zahl zwischen 1024 und 65535 sein.") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("Der Port muss zwischen 1024 und 65535 liegen.")
    return port


def configured_port(root: Path = ROOT) -> int:
    settings_file = root / "archiv-einstellungen.json"
    if not settings_file.exists():
        return 8771
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8-sig"))
        if not isinstance(settings, dict):
            raise ValueError("Es wird ein JSON-Objekt erwartet.")
        return validate_port(settings.get("port", 8771))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"archiv-einstellungen.json ist ungültig: {exc}") from exc


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise URLError("Die lokale Statusprüfung erlaubt keine Weiterleitungen.")


def local_json(port: int, path: str, payload: dict | None = None, token: str = "") -> dict:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    base = f"http://127.0.0.1:{port}"
    headers = {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Origin": base, "X-BIP-Token": token}
    request = Request(base + path, data=body, headers=headers)
    with opener.open(request, timeout=2) as response:
        data = response.read(65537)
    if len(data) > 65536:
        raise ValueError("Die lokale Statusantwort ist zu groß.")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Ungültige lokale Statusantwort.")
    return result


def health_status(port: int) -> dict | None:
    try:
        return local_json(port, "/api/health")
    except (OSError, URLError, ValueError):
        return None


def is_our_archive(status: dict | None, root: Path = ROOT) -> bool:
    return bool(status and status.get("app") == APP_ID and status.get("portal") is True
                and status.get("archive_root_fingerprint") == root_fingerprint(root))


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.5):
            return True
    except OSError:
        return False


def venv_python(root: Path = ROOT, windowless: bool = False) -> Path:
    bundled = root / "runtime" / ("pythonw.exe" if windowless else "python.exe")
    if bundled.is_file():
        return bundled
    folder = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    name = "pythonw.exe" if os.name == "nt" and windowless else "python.exe" if os.name == "nt" else "python"
    executable = folder / name
    if not executable.is_file():
        raise RuntimeError("Die lokale Python-Umgebung fehlt. Bitte zuerst Installieren.cmd ausführen.")
    return executable


def occupied_message(port: int) -> str:
    return (f"Port {port} wird bereits von einem anderen Archiv oder Programm verwendet.\n"
            "Diese Kopie wurde deshalb nicht geöffnet. Es wurden keine anderen Dienste beendet.\n"
            "Bitte in archiv-einstellungen.json einen anderen Port wählen, zum Beispiel 8772, "
            "oder Starten.cmd --port 8772 verwenden.")


def ensure_server(port: int, root: Path = ROOT) -> None:
    current = health_status(port)
    if is_our_archive(current, root):
        return
    if current is not None or port_in_use(port):
        raise RuntimeError(occupied_message(port))
    python = venv_python(root, windowless=True)
    logs = root / "Recherche" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "server.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(python), "-B", "-m", "research_portal.server", "--port", str(port)],
            cwd=root / "Recherche", stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(100):
        status = health_status(port)
        if is_our_archive(status, root):
            return
        if status is not None:
            if process.poll() is None:
                process.terminate()
            raise RuntimeError(occupied_message(port))
        if process.poll() is not None:
            raise RuntimeError("Der lokale Suchdienst konnte nicht starten.\n"
                               "Einzelheiten stehen in Recherche/logs/server.log. "
                               "Bitte zunächst Installieren.cmd ausführen, falls die Einrichtung noch fehlt.")
        time.sleep(.2)
    if process.poll() is None:
        process.terminate()
    raise RuntimeError("Der lokale Suchdienst hat nicht rechtzeitig geantwortet. "
                       "Einzelheiten stehen in Recherche/logs/server.log.")


def stop_server(port: int, root: Path = ROOT) -> None:
    current = health_status(port)
    if current is None and not port_in_use(port):
        if sys.stdout:
            print("Diese Sammlung läuft derzeit nicht.")
        return
    if not is_our_archive(current, root):
        raise RuntimeError(occupied_message(port))
    state = local_json(port, "/api/state")
    try:
        result = local_json(port, "/api/shutdown",
                            {"archive_root_fingerprint": root_fingerprint(root)}, str(state.get("csrf", "")))
    except URLError as exc:
        if getattr(exc, "code", None) == 409:
            raise RuntimeError("Eine Aktualisierung oder Indexierung läuft noch. Bitte deren Abschluss abwarten und Stoppen.cmd erneut ausführen.") from exc
        raise
    if not result.get("stopping"):
        raise RuntimeError("Der lokale Suchdienst konnte nicht beendet werden.")
    for _ in range(50):
        if not is_our_archive(health_status(port), root):
            if sys.stdout:
                print("Der lokale Suchdienst dieser Sammlung wurde beendet.")
            return
        time.sleep(.1)
    raise RuntimeError("Der Suchdienst beendet sich noch. Bitte kurz warten und Stoppen.cmd erneut ausführen.")


def open_archive(url: str) -> None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Microsoft/Edge/Application/msedge.exe"]
    edge = next((path for path in candidates if path.is_file()), None) if os.name == "nt" else None
    if edge:
        subprocess.Popen([str(edge), "--app=" + url, "--window-size=1380,960"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif not webbrowser.open(url):
        raise RuntimeError(f"Der Browser konnte nicht geöffnet werden. Die Sammlung ist unter {url} erreichbar.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mein Recherchearchiv lokal öffnen.")
    parser.add_argument("--collection", choices=COLLECTION_PATHS, default="archive")
    parser.add_argument("--port", type=validate_port, help="Lokaler Port; überschreibt archiv-einstellungen.json.")
    parser.add_argument("--no-open", action="store_true", help="Nur den lokalen Suchdienst starten.")
    parser.add_argument("--stop", action="store_true", help="Nur den Suchdienst dieser Sammlung beenden.")
    parser.add_argument("--console", action="store_true", help="Fehler im Konsolenfenster anzeigen.")
    args = parser.parse_args(argv)
    try:
        port = args.port if args.port is not None else configured_port()
        if args.stop:
            stop_server(port)
            return 0
        ensure_server(port)
        url = f"http://127.0.0.1:{port}" + COLLECTION_PATHS[args.collection]
        if not args.no_open:
            open_archive(url)
        if sys.stdout:
            print(f"Mein Recherchearchiv: {url}")
        return 0
    except Exception as exc:
        message = str(exc)
        if sys.stderr:
            print(message, file=sys.stderr)
        if not args.console:
            try:
                if os.name == "nt":
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(None, message, "Mein Recherchearchiv", 0x10)
                else:
                    from tkinter import Tk, messagebox
                    window = Tk()
                    window.withdraw()
                    messagebox.showerror("Mein Recherchearchiv", message, parent=window)
                    window.destroy()
            except Exception:
                (ROOT / "Startfehler.txt").write_text(message, encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
