"""Check the public source boundary without printing private file contents.

Generated runtime/build directories are excluded. All other files must belong
to the explicit source locations below. This is a release guard, not a complete
secret scanner or a replacement for human review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".gitignore", ".gitattributes", "README.md", "LICENSE",
    "CONTRIBUTING.md", "SECURITY.md", "PRIVACY.md", "CODE_SIGNING.md",
    "00-START-HIER.html", "00-START-HIER.txt", "INSTALLATION.html",
    "INSTALLATION.txt", "DRITTQUELLEN.md", "ENTWICKLUNG.md", "PRUEFBERICHT.txt",
    "requirements.txt", "requirements-getestet.txt", "package.json",
    "Installieren.cmd", "installieren.py", "Starten.cmd", "starten.pyw",
    "archiv_start.py", "Stoppen.cmd", "Desktop-Verknuepfung.cmd",
    "Desktop-Verknuepfung.ps1", "pruefen.py", "paket_erstellen.py",
    "installer_support.py", "INSTALLER-INFO.txt", "DATEIEN-SHA256.json",
    "test_empty_install.py", "test_launcher.py",
}
FIXED_FILES = {
    "BIP/crawler.py", "BIP/test_crawler.py", "BIP/start_recherche.pyw",
    "BIP/requirements.txt", "BIP/recherche/requirements.txt",
    "Recherche/web/un-emblem-source.json",
    "UN/sources-start.json", "UN/data/sources.json",
    "tools/verify_publication.py", "tests/test_publication.py",
}
FIXED_FILES.update(f"{folder}/{name}" for folder in ("BIP", "ICC-ICJ", "UN")
                   for name in ("LIESMICH.txt", "Starten.cmd"))
CODE_DIRS = {"BIP/recherche", "Recherche/research_portal", "Recherche/scripts"}
WEB_DIRS = {"BIP/recherche/web", "Recherche/web"}
GENERATED_PREFIXES = (
    ".git/", ".venv/", "node_modules/", "runtime/", ".test-tmp/",
    ".pytest_cache/", "tmp/", "dist/",
    "Recherche/.test-tmp/", "Recherche/tmp/",
    "installer/build/", "installer/output/", "installer/downloads/",
    "installer/artifacts/",
    "installer/tools/", "installer/signpath-signed/",
)
TEXT_SUFFIXES = {
    ".py", ".pyw", ".md", ".txt", ".json", ".svg", ".html", ".css", ".js",
    ".cjs", ".cmd", ".ps1", ".iss", ".yml", ".yaml", ".xml", ".example",
    ".template",
}
PRIVATE_PATTERNS = {
    "personal home path": re.compile(
        r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+(?!Public(?:[\\/]|$))[^\\/\s]+"
        r"|/(?:Users|home)/[A-Za-z0-9_.-]+/)"
    ),
    "private key material": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "GitHub access token": re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})"
    ),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def allowed(relative: str) -> bool:
    path = Path(relative)
    parent = path.parent.as_posix()
    if relative in ROOT_FILES | FIXED_FILES:
        return True
    if parent in CODE_DIRS:
        return path.suffix in {".py", ".cjs"}
    if parent in WEB_DIRS:
        return path.suffix in {".html", ".css", ".js", ".svg", ".ico"}
    if parent == "installer":
        return path.suffix in {".py", ".ps1", ".iss", ".txt", ".json", ".md"}
    if relative.startswith("installer/signpath/"):
        return path.suffix in {".md", ".txt", ".xml", ".example", ".template"}
    if parent == ".github/workflows":
        return path.suffix in {".yml", ".yaml"}
    if relative.startswith("docs/"):
        return path.suffix in {".md", ".txt", ".html"}
    return False


def source_paths(root: Path = ROOT) -> list[Path]:
    root = root.resolve()
    paths = []
    problems = []
    # Check directories too: a junction must not import external user data.
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            problems.append(f"link/junction not allowed: {relative}")
            continue
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or relative.startswith(GENERATED_PREFIXES):
            continue
        if not allowed(relative):
            problems.append(f"not an approved source file: {relative}")
            continue
        if not path.resolve().is_relative_to(root):
            problems.append(f"source outside repository: {relative}")
            continue
        paths.append(path)
    if problems:
        raise ValueError("\n".join(problems))
    return paths


def audit(root: Path = ROOT) -> dict:
    root = root.resolve()
    paths = source_paths(root)
    problems = []
    manifest = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        manifest[relative] = {
            "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()
        }
        if path.suffix in TEXT_SUFFIXES or path.name in {
            "LICENSE", ".gitignore", ".gitattributes"
        }:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                problems.append(f"source is not UTF-8: {relative}")
                continue
            for label, pattern in PRIVATE_PATTERNS.items():
                if pattern.search(text):
                    problems.append(f"{label}: {relative}")
    seed = root / "UN/sources-start.json"
    installed_seed = root / "UN/data/sources.json"
    if not seed.is_file() or not installed_seed.is_file():
        problems.append("both public UN seed files must be present")
    elif seed.read_bytes() != installed_seed.read_bytes():
        problems.append("UN/data/sources.json differs from public seed")
    else:
        try:
            json.loads(seed.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            problems.append("public UN seed is not valid JSON")
    if problems:
        raise ValueError("\n".join(problems))
    return {"status": "pass", "source_files": len(paths),
            "source_bytes": sum(entry["bytes"] for entry in manifest.values()),
            "files": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        result = audit(args.root)
    except ValueError as exc:
        parser.exit(1, f"Publication check failed:\n{exc}\n")
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2)
                                 + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items()
                      if key != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
