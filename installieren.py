"""Install dependencies locally, without downloading documents or requiring admin rights."""
from __future__ import annotations
import argparse
from pathlib import Path
import sqlite3
import subprocess
import sys
try:
    import venv
except ModuleNotFoundError:
    venv = None  # The bundled Windows runtime is already fully configured.

ROOT = Path(__file__).resolve().parent


def check_python() -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12 oder neuer wird benötigt. Bitte eine aktuelle 64-Bit-Version "
                           "von python.org für das eigene Benutzerkonto installieren.")
    try:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
            connection.execute("INSERT INTO probe VALUES ('Rechercheprüfung')")
            if not connection.execute("SELECT COUNT(*) FROM probe WHERE probe MATCH 'Rechercheprüfung'").fetchone()[0]:
                raise RuntimeError("Der Volltextsuchtest lieferte kein Ergebnis.")
    except sqlite3.Error as exc:
        raise RuntimeError("Diese Python-Version enthält keine funktionsfähige SQLite-FTS5-Suche. "
                           "Bitte die reguläre Python-Installation von python.org verwenden.") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mein Recherchearchiv im entpackten Ordner einrichten.")
    parser.add_argument("--check", action="store_true", help="Nur Python und SQLite-FTS5 prüfen; nichts installieren.")
    args = parser.parse_args(argv)
    try:
        check_python()
        print("Python und SQLite-Volltextsuche sind geeignet.")
        if args.check:
            return 0
        if (ROOT / "runtime/python.exe").is_file():
            from installer_support import initialize
            initialize(ROOT)
            print("Die Windows-Installation enthält bereits alle Bibliotheken.")
            return 0
        if venv is None:
            raise RuntimeError("Die Laufzeit ist unvollständig. Bitte das Windows-Setup erneut ausführen.")
        requirements = ROOT / "requirements.txt"
        if not requirements.is_file():
            raise RuntimeError("requirements.txt fehlt. Bitte das vollständige Paket entpacken.")
        environment = ROOT / ".venv"
        if not environment.exists():
            print("Die lokale Python-Umgebung .venv wird eingerichtet …")
            venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if not python.is_file():
            raise RuntimeError("Der Ordner .venv ist unvollständig. Bitte ihn umbenennen und die Installation erneut starten.")
        subprocess.run([str(python), str(Path(__file__).resolve()), "--check"], check=True, cwd=ROOT)
        print("Programmbibliotheken werden installiert. Dafür wird Internetzugang benötigt …")
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)],
                       check=True, cwd=ROOT)
        print("\nFertig. Das Archiv lässt sich mit Starten.cmd öffnen.")
        print("Es wurden keine Dokumente heruntergeladen. Die Sammlung wird erst durch eine ausdrücklich gestartete Aktualisierung aufgebaut.")
        print("Optional: Desktop-Verknuepfung.cmd erstellt eine Verknüpfung für diese Kopie.")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"\nEinrichtung nicht abgeschlossen: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
