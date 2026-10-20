"""Build an offline Windows 11 x64 installer from explicit code and wheel inputs."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT.parent
PYTHON_URL = "https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip"
PYTHON_HASH = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
INSTALLER_SOURCES = (
    "build_setup.py", "setup.iss", "hinweise.txt", "wheels.lock.txt",
    "ERSTELLEN.txt", "README.md", "test_installer_support.py", "test_runtime.py",
    "test_setup_integration.py", "test_build_setup.py", "SIGNIERUNG.txt",
    "install_inno.ps1", "prepare_ci_artifact.py",
    "signpath/README.md", "signpath/workflow-plan.txt",
)
PUBLICATION_SOURCES = (
    ".gitattributes", "CONTRIBUTING.md", "SECURITY.md", "PRIVACY.md",
    "CODE_SIGNING.md", ".github/workflows/windows-unsigned.yml",
    "tools/verify_publication.py", "tests/test_publication.py",
)
PUBLICATION_DOCUMENTS = (
    "CONTRIBUTING.md", "SECURITY.md", "PRIVACY.md", "CODE_SIGNING.md",
    "installer/README.md", "installer/ERSTELLEN.txt", "installer/SIGNIERUNG.txt",
    "installer/signpath/README.md", "installer/signpath/workflow-plan.txt",
)


def verify_publication():
    """Stop before downloads or compilation if the public source boundary fails."""
    subprocess.run([sys.executable, "-B", str(PAYLOAD / "tools/verify_publication.py"),
                    "--root", str(PAYLOAD)], check=True, cwd=PAYLOAD)


def prepare_runtime():
    """Run with full Python and pip; the installed app never uses pip."""
    downloads = ROOT / "downloads"
    downloads.mkdir(exist_ok=True)
    archive = downloads / PYTHON_URL.rsplit("/", 1)[-1]
    if not archive.exists():
        partial = archive.with_suffix(".partial")
        with urllib.request.urlopen(PYTHON_URL, timeout=60) as response:
            partial.write_bytes(response.read())
        if hashlib.sha256(partial.read_bytes()).hexdigest() != PYTHON_HASH:
            raise RuntimeError("Python download checksum mismatch.")
        partial.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_HASH:
        raise RuntimeError("Python archive checksum mismatch.")
    runtime = PAYLOAD / "runtime"
    if runtime.exists():
        raise RuntimeError("Runtime already exists; use a fresh build checkout for --prepare-runtime.")
    runtime.mkdir()
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not (runtime / name).resolve().is_relative_to(runtime):
                raise RuntimeError("Unsafe runtime archive member")
        z.extractall(runtime)
    (runtime / "python313._pth").write_text(
        "python313.zip\n.\nLib/site-packages\n..\n../BIP\n../Recherche\nimport site\n",
        encoding="ascii")
    wheels = downloads / "wheels"
    subprocess.run([sys.executable, "-m", "pip", "download", "--require-hashes",
        "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "313",
        "--implementation", "cp", "--abi", "cp313", "--dest", str(wheels),
        "-r", str(ROOT / "wheels.lock.txt")], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--require-hashes",
        "--no-index", "--find-links", str(wheels), "--only-binary=:all:",
        "--platform", "win_amd64", "--python-version", "313", "--implementation", "cp",
        "--abi", "cp313", "--no-compile", "--target", str(runtime / "Lib/site-packages"),
        "-r", str(ROOT / "wheels.lock.txt")], check=True)


def app_files():
    if (PAYLOAD / "UN/data/sources.json").read_bytes() != (PAYLOAD / "UN/sources-start.json").read_bytes():
        raise RuntimeError("Build refused: UN sources were customized. Use only the public seed in release builds.")
    spec = importlib.util.spec_from_file_location("source_builder", PAYLOAD / "paket_erstellen.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = set(module.source_files(PAYLOAD))
    paths.update(PAYLOAD / p for p in (
        "installer_support.py", "UN/sources-start.json", "UN/data/sources.json",
        "INSTALLER-INFO.txt"))
    paths.update(PAYLOAD / relative for relative in PUBLICATION_DOCUMENTS)
    paths.update((PAYLOAD / "docs").glob("*.md"))
    return sorted(paths)


def public_source_files():
    """Only named source paths; never glob the application data or build outputs."""
    paths = set(app_files())
    paths.update(ROOT / relative for relative in INSTALLER_SOURCES)
    paths.update(PAYLOAD / relative for relative in PUBLICATION_SOURCES)
    # Repository documentation belongs to the source distribution, not installed app data.
    paths.update((PAYLOAD / "docs").glob("*.md"))
    missing = [str(p.relative_to(PAYLOAD)) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError("Missing public source files: " + ", ".join(sorted(missing)))
    for path in paths:
        if not path.resolve().is_relative_to(PAYLOAD.resolve()) or path.is_symlink():
            raise RuntimeError(f"Source path escapes repository: {path.name}")
    return sorted(paths)


def file_list():
    paths = app_files()
    runtime = PAYLOAD / "runtime"
    if not (runtime / "python.exe").is_file():
        raise RuntimeError("Bundled runtime missing. Run --prepare-runtime first.")
    paths += [p for p in runtime.rglob("*") if p.is_file()
        and "__pycache__" not in p.parts
        and not p.is_relative_to(runtime / "Lib/site-packages/bin")]
    return sorted(set(paths))


def write_manifest():
    entries, manifest = [], {}
    for path in file_list():
        relative = path.relative_to(PAYLOAD)
        if not path.resolve().is_relative_to(PAYLOAD.resolve()) or not path.is_file():
            raise RuntimeError(f"Invalid payload file: {relative}")
        dest = "{app}" + ("\\" + str(relative.parent) if str(relative.parent) != "." else "")
        flags = "onlyifdoesntexist uninsneveruninstall" if relative.as_posix() == "UN/data/sources.json" else "ignoreversion"
        entries.append(f'Source: "{{#PayloadDir}}\\{relative}"; DestDir: "{dest}"; Flags: {flags}')
        content = path.read_bytes()
        manifest[relative.as_posix()] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    (build / "files.iss").write_text("\n".join(entries) + "\n", encoding="utf-8-sig")
    (build / "payload-sha256.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def source_zip():
    output = ROOT / "output/Mein-Recherchearchiv-Windows-Installer-Quellcode.zip"
    paths = public_source_files()
    output.parent.mkdir(exist_ok=True)
    manifest = {}
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in paths:
            relative = path.relative_to(PAYLOAD).as_posix()
            content = path.read_bytes()
            z.writestr("Mein-Recherchearchiv/" + relative, content)
            manifest[relative] = {
                "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()
            }
        z.writestr("Mein-Recherchearchiv/DATEIEN-SHA256.json",
                   json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(output) as z:
        if z.testzip() is not None:
            raise RuntimeError("Source ZIP CRC check failed.")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-runtime", action="store_true")
    parser.add_argument("--compiler", type=Path)
    parser.add_argument("--source-zip", action="store_true")
    parser.add_argument("--isolated-test", action="store_true",
                        help="Test build without Windows uninstall registration; never distribute as release.")
    args = parser.parse_args()
    if args.prepare_runtime or args.compiler or args.source_zip:
        verify_publication()
    if args.prepare_runtime:
        prepare_runtime()
    if args.compiler:
        manifest = write_manifest()
        print(f"Allowlisted installer payload: {len(manifest)} files")
        flags = (["/DIsolatedTest=1", "/DSetupAppId=MeinRecherchearchiv.Test",
                  "/DSetupAppName=Mein Recherchearchiv Test", "/DOutputName=Installer-Pruefung"]
                 if args.isolated_test else [])
        basename = ("Installer-Pruefung.exe" if args.isolated_test else
                    "Mein-Recherchearchiv-Setup-1.0.0-Windows11-x64.exe")
        if (ROOT / "output" / basename).exists():
            raise RuntimeError("Output already exists; use a fresh build checkout.")
        subprocess.run([str(args.compiler.resolve()), "/Qp", f"/DPayloadDir={PAYLOAD}",
                        *flags, str(ROOT / "setup.iss")], check=True, cwd=ROOT)
    if args.source_zip:
        print(source_zip())


if __name__ == "__main__":
    main()
