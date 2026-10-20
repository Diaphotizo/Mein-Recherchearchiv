"""Build a source-only ZIP from an explicit allowlist, even after using the app."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
ROOT_FILES = {
    ".gitignore", "README.md", "00-START-HIER.html", "00-START-HIER.txt",
    "INSTALLATION.html", "INSTALLATION.txt",
    "DRITTQUELLEN.md", "ENTWICKLUNG.md", "LICENSE", "requirements.txt",
    "requirements-getestet.txt", "PRUEFBERICHT.txt", "package.json",
    "Installieren.cmd", "installieren.py", "Starten.cmd", "starten.pyw",
    "archiv_start.py", "Stoppen.cmd", "Desktop-Verknuepfung.cmd",
    "Desktop-Verknuepfung.ps1", "pruefen.py", "paket_erstellen.py",
}


def source_files(root=ROOT):
    files = {root / name for name in ROOT_FILES if (root / name).is_file()}
    # Keep the three visible entry folders even before they hold any documents.
    # Only these named public files are included, never the whole data folders.
    files.update(root / folder / name for folder in ("BIP", "ICC-ICJ", "UN")
                 for name in ("LIESMICH.txt", "Starten.cmd"))
    files.update(root / name for name in ("00-START-HIER.html", "00-START-HIER.txt",
                                         "INSTALLATION.html", "INSTALLATION.txt"))
    files.update(root.glob("test_*.py"))
    for relative in ("BIP/crawler.py", "BIP/test_crawler.py",
                     "BIP/start_recherche.pyw", "BIP/requirements.txt"):
        files.add(root / relative)
    for folder in ("BIP/recherche", "Recherche/research_portal", "Recherche/scripts"):
        for suffix in ("*.py", "*.cjs"):
            files.update((root / folder).glob(suffix))
    files.add(root / "BIP/recherche/requirements.txt")
    for folder in ("BIP/recherche/web", "Recherche/web"):
        files.update(p for p in (root / folder).iterdir()
                     if p.is_file() and p.suffix in (".html", ".css", ".js", ".svg", ".ico"))
    files.add(root / "Recherche/web/un-emblem-source.json")
    return sorted(files)


def build(target, root=ROOT):
    target = Path(target).resolve()
    root = Path(root).resolve()
    if target.suffix.lower() != ".zip":
        raise ValueError("Das Ziel muss eine ZIP-Datei sein.")
    if target.is_relative_to(root):
        raise ValueError("Bitte das ZIP außerhalb des Programmordners speichern.")
    files = source_files(root)
    missing = [str(p.relative_to(root)) for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    # The shipped public seed is separate from the live user-editable manifest.
    seed = root / "UN/sources-start.json"
    if not seed.is_file():
        raise FileNotFoundError("UN/sources-start.json fehlt.")
    public_manifest = seed.read_bytes()
    manifest = {}
    prefix = "Mein-Recherchearchiv"
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        def put(relative, content):
            archive.writestr(prefix + "/" + relative, content)
            manifest[relative] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for path in files:
            if not path.resolve().is_relative_to(root):
                raise ValueError("Eine Quelldatei liegt außerhalb des Programmordners.")
            put(path.relative_to(root).as_posix(), path.read_bytes())
        put("UN/sources-start.json", public_manifest)
        put("UN/data/sources.json", public_manifest)
        put("archiv-einstellungen.json", b'{"port": 8771}\n')
        archive.writestr(prefix + "/DATEIEN-SHA256.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Die ZIP-Prüfung ist fehlgeschlagen.")
    return {"files": len(manifest) + 1, "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


if __name__ == "__main__":
    raise SystemExit("Für die Windows-Installer-Ausgabe bitte das zugehörige Entwicklerpaket verwenden: build_setup.py --source-zip. Die Quelldateiliste dieses Moduls wird vom Installer-Build genutzt.")
