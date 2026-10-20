"""Prepare an explicitly bounded unsigned CI artifact; never upload anything."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent
FILES = (
    "Mein-Recherchearchiv-Setup-1.0.0-Windows11-x64.exe",
    "Mein-Recherchearchiv-Windows-Installer-Quellcode.zip",
)
MAX_BYTES = 100 * 1024 * 1024


def main():
    sources = [ROOT / "output" / name for name in FILES]
    if any(not p.is_file() or p.is_symlink() for p in sources):
        raise RuntimeError("Both build outputs must exist as ordinary files.")
    if sum(p.stat().st_size for p in sources) >= MAX_BYTES - 65536:
        raise RuntimeError("Artifact exceeds the 100 MiB upload guard.")
    destination = ROOT / "artifacts"
    destination.mkdir(exist_ok=False)
    for source in sources:
        shutil.copyfile(source, destination / source.name)
    commit = os.environ.get("GITHUB_SHA", "")
    if commit and not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise RuntimeError("Invalid GitHub commit identifier.")
    (destination / "BUILD-INFO.json").write_text(json.dumps({
        "commit": commit or None,
        "signature": "unsigned",
        "target": "Windows 11 x64",
        "build_test_host": "Windows Server 2022",
        "installer_compiler": "Inno Setup 6.7.3",
        "embedded_python": "3.13.16",
        "publication": "Temporary workflow artifact only; no release created.",
    }, indent=2) + "\n", encoding="utf-8")
    (destination / "UNSIGNIERT.txt").write_text(
        "Diese Ausgabe ist NICHT digital signiert.\n"
        "Es wurde noch keine SignPath-Zulassung oder Cloud-Signierung bestätigt.\n"
        "Windows kann deshalb vor einem unbekannten Herausgeber warnen.\n"
        "Enthalten sind Programmcode und Laufzeit, keine privaten Archive.\n",
        encoding="utf-8")
    manifest = []
    for path in sorted(destination.iterdir()):
        manifest.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (destination / "SHA256.txt").write_text("\n".join(manifest) + "\n", encoding="ascii")
    total = sum(p.stat().st_size for p in destination.iterdir())
    if total >= MAX_BYTES:
        raise RuntimeError("Artifact exceeds the 100 MiB upload guard.")
    print(f"Prepared unsigned artifact: {len(manifest) + 1} files, {total} bytes.")


if __name__ == "__main__":
    main()
