"""Conservative setup hooks; never replace archive data or terminate a foreign service."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import sys

import archiv_start


ROOT = Path(__file__).resolve().parent
OPERATION_LOCKS = (
    ("BIP/recherche/data/build.lock", "BIP-Aktualisierung"),
    ("ICC-ICJ/data/update.lock", "ICC/ICJ-Aktualisierung"),
    ("ICC-ICJ/data/index.lock", "ICC/ICJ-Indexierung"),
    ("UN/data/update.lock", "UN-Aktualisierung"),
    ("UN/data/index.lock", "UN-Indexierung"),
)


def check_operation_locks(root: Path) -> None:
    """Probe existing OS locks without creating or altering any archive files."""
    with ExitStack() as stack:
        for relative, label in OPERATION_LOCKS:
            path = root / relative
            if not path.exists():
                continue
            try:
                handle = stack.enter_context(path.open("r+b"))
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    stack.callback(msvcrt.locking, handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    stack.callback(fcntl.flock, handle, fcntl.LOCK_UN)
            except OSError as exc:
                raise RuntimeError(
                    f"{label} ist noch aktiv oder ihre Sperrdatei ist nicht zugänglich. "
                    "Bitte den Abschluss abwarten und die Installation beziehungsweise "
                    "Deinstallation danach erneut starten. Es wurde kein Vorgang abgebrochen."
                ) from exc


def prepare(root: Path = ROOT) -> None:
    """Reject active work and stop only the service belonging to this installation."""
    root = Path(root).resolve()
    check_operation_locks(root)
    port = archiv_start.configured_port(root)
    status = archiv_start.health_status(port)
    if archiv_start.is_our_archive(status, root):
        archiv_start.stop_server(port, root)
        # A second check also detects independently launched command-line updaters.
        check_operation_locks(root)
        print("Der Suchdienst dieser Installation wurde sicher beendet.")
    else:
        print("Kein Suchdienst dieser Installation zu beenden. Andere Archive bleiben unberührt.")


def port_available(port: int) -> bool:
    """Check binding, including ports occupied by services without our health endpoint."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def initialize(root: Path = ROOT) -> None:
    """Create first-install settings only; leave every existing setting byte-for-byte."""
    root = Path(root).resolve()
    if not (root / "archiv_start.py").is_file():
        raise RuntimeError("Der Installationsordner enthält noch kein vollständiges Programm.")
    settings = root / "archiv-einstellungen.json"
    if settings.exists():
        print("Vorhandene Einstellungen und Quellenangaben bleiben vollständig erhalten.")
        return
    port = next((candidate for candidate in range(8771, 8791) if port_available(candidate)), None)
    if port is None:
        raise RuntimeError(
            "Keiner der lokalen Ports 8771 bis 8790 ist frei. "
            "Bitte ein anderes Recherchearchiv schließen und die Einrichtung erneut starten."
        )
    try:
        # Exclusive creation also preserves settings created by another process meanwhile.
        with settings.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump({"port": port}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError:
        print("Vorhandene Einstellungen und Quellenangaben bleiben vollständig erhalten.")
        return
    print(f"Ersteinrichtung abgeschlossen; lokaler Port: {port}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sichere Vorbereitung und Ersteinrichtung des Recherchearchivs.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--initialize", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT, help="Installationsordner; normalerweise automatisch erkannt.")
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            prepare(args.root)
        else:
            initialize(args.root)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        message = f"Einrichtung angehalten: {exc}"
        try:
            (args.root.resolve() / "installer-status.txt").write_text(
                message + "\n", encoding="utf-8"
            )
        except OSError:
            # Keep the original failure even when the destination itself is inaccessible.
            pass
        if sys.stderr:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
