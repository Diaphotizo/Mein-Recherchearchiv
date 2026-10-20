"""Install, launch, upgrade and uninstall inside a named disposable test directory."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
TARGET = (BUILD / "Installationstest2").resolve()
SETUP = ROOT / "output/Installer-Pruefung.exe"
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HTTP = build_opener(ProxyHandler({}))


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], creationflags=HIDDEN,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


def setup(logname):
    assert TARGET.parent == BUILD.resolve()
    return run([SETUP, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                "/NOICONS", "/TASKS=", f"/DIR={TARGET}", f"/LOG={BUILD / logname}"], timeout=120)


def uninstall(logname):
    executable = (TARGET / "unins000.exe").resolve()
    assert executable.parent == TARGET and TARGET.parent == BUILD.resolve()
    return run([executable, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                f"/LOG={BUILD / logname}"], timeout=120)


def hold_lock():
    path = TARGET / "UN/data/update.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    code = ("import msvcrt,sys; f=open(sys.argv[1],'a+b'); f.seek(0); "
            "f.write(b'0'); f.flush(); f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1); "
            "print('locked',flush=True); sys.stdin.readline()")
    child = subprocess.Popen([str(TARGET / "runtime/python.exe"), "-B", "-c", code, str(path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, creationflags=HIDDEN)
    assert child.stdout.readline().strip() == "locked"
    return child


def hashes(paths):
    return {str(p.relative_to(TARGET)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    assert TARGET.parent == BUILD.resolve() and not TARGET.exists(), "Use a new named test directory."
    result = setup("isolated-install.log")
    assert result.returncode == 0, (result.returncode, result.stdout)
    print("Installation successful.", flush=True)
    python = TARGET / "runtime/python.exe"
    config = TARGET / "archiv-einstellungen.json"
    settings = json.loads(config.read_text(encoding="utf-8"))
    port = settings["port"]
    result = run([python, "-B", TARGET / "archiv_start.py", "--no-open", "--console"], timeout=30)
    assert result.returncode == 0, result.stdout
    for route in ("/", "/icc-icj/", "/un/"):
        with HTTP.open(f"http://127.0.0.1:{port}{route}", timeout=5) as response:
            assert response.status == 200 and b"html" in response.read(200).lower()
    for route in ("/api/state", "/api/courts/state", "/api/un/state"):
        with HTTP.open(f"http://127.0.0.1:{port}{route}", timeout=5) as response:
            state = json.load(response)
            assert state["total"] == 0, (route, state.get("total"))
    print("All three empty libraries and their APIs reachable.", flush=True)
    result = run([python, "-B", ROOT / "test_runtime.py"], timeout=30)
    assert result.returncode == 0, result.stdout
    print("Installed native runtime and spawn workers successful.", flush=True)
    sentinels = []
    for relative in ("BIP/PDF/test.pdf", "BIP/versionen/test/quelle.json",
                     "BIP/recherche/data/eigene_angaben.json",
                     "ICC-ICJ/PDF/test.pdf", "ICC-ICJ/data/annotations.json",
                     "UN/PDF/test.pdf", "UN/TXT/test.txt", "UN/data/annotations.json",
                     "Recherche/data/eigene-testangaben.json"):
        p = TARGET / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"test": "Eigene Angaben und frühere Fassungen bleiben erhalten."}\n', encoding="utf-8")
        sentinels.append(p)
    settings["eigene_einstellung"] = "Behalten"
    config.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    sources = TARGET / "UN/data/sources.json"
    doc = json.loads(sources.read_text(encoding="utf-8"))
    doc["eigene_quelle_test"] = "Behalten"
    sources.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    sentinels += [config, sources]
    before = hashes(sentinels)
    lock = hold_lock()
    try:
        result = setup("blocked-upgrade.log")
        assert result.returncode != 0, "Upgrade must refuse active updates."
        assert hashes(sentinels) == before
    finally:
        lock.communicate("\n", timeout=5)
    print("Active-update guard prevents upgrade.", flush=True)
    result = setup("isolated-upgrade.log")
    assert result.returncode == 0, (result.returncode, result.stdout)
    assert hashes(sentinels) == before
    print("Upgrade preserves all 11 document/settings sentinels.", flush=True)
    lock = hold_lock()
    try:
        result = uninstall("blocked-uninstall.log")
        assert result.returncode != 0, "Uninstall must refuse active updates."
        assert python.exists() and hashes(sentinels) == before
    finally:
        lock.communicate("\n", timeout=5)
    print("Active-update guard prevents uninstall.", flush=True)
    result = uninstall("isolated-uninstall.log")
    assert result.returncode == 0, (result.returncode, result.stdout)
    for _ in range(30):
        if not python.exists():
            break
        time.sleep(.2)
    assert not python.exists() and not (TARGET / "archiv_start.py").exists()
    assert hashes(sentinels) == before
    print("Uninstall removed program and preserved all documents, versions and own data.", flush=True)
    (BUILD / "installer-integration-result.json").write_text(json.dumps({
        "result": "passed", "windows_version": sys.getwindowsversion().build,
        "scope": "Isolated Inno build with identical payload, without registry and startmenu writes",
        "empty_collection_pages": 3, "empty_collection_apis": 3,
        "preserved_files": len(sentinels), "upgrade_guard": "passed",
        "uninstall_guard": "passed", "native_pdf_spawn": "passed"}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
