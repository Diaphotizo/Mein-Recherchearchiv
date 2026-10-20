"""Isolated installer-hook checks; no network requests or production paths."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BUILD_ROOT = Path(__file__).resolve().parent
TEST_ROOT = BUILD_ROOT / "hook-test-fixtures"
TEST_ROOT.mkdir(exist_ok=True)
sys.path.insert(0, str(BUILD_ROOT.parent))
import installer_support as support


class InstallerSupportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(TEST_ROOT.resolve())
        self.addCleanup(self.temporary.cleanup)
        (self.root / "archiv_start.py").write_text("# installed program", encoding="utf-8")

    def test_custom_settings_and_sources_are_unchanged(self):
        settings = self.root / "archiv-einstellungen.json"
        sources = self.root / "UN/data/sources.json"
        sources.parent.mkdir(parents=True)
        own_settings = b'{"port": 9898, "my_setting": true}\r\n'
        own_sources = b'{"documents": [{"symbol":"my manual source"}]}'
        settings.write_bytes(own_settings)
        sources.write_bytes(own_sources)
        with patch.object(support, "port_available") as available:
            support.initialize(self.root)
        available.assert_not_called()
        self.assertEqual(settings.read_bytes(), own_settings)
        self.assertEqual(sources.read_bytes(), own_sources)

    def test_first_install_skips_occupied_port(self):
        with patch.object(support, "port_available", side_effect=lambda port: port == 8772):
            support.initialize(self.root)
        settings = json.loads((self.root / "archiv-einstellungen.json").read_text())
        self.assertEqual(settings, {"port": 8772})

    def test_exhausted_ports_leave_no_partial_configuration(self):
        with patch.object(support, "port_available", return_value=False):
            with self.assertRaises(RuntimeError):
                support.initialize(self.root)
        self.assertFalse((self.root / "archiv-einstellungen.json").exists())

    def test_concurrently_created_settings_are_preserved(self):
        settings = self.root / "archiv-einstellungen.json"
        def available(port):
            settings.write_text('{"port": 9988}', encoding="utf-8")
            return True
        with patch.object(support, "port_available", side_effect=available):
            support.initialize(self.root)
        self.assertEqual(settings.read_text(), '{"port": 9988}')

    def test_prepare_stops_only_matching_archive(self):
        status = {
            "app": support.archiv_start.APP_ID,
            "portal": True,
            "archive_root_fingerprint": support.archiv_start.root_fingerprint(self.root),
        }
        with patch.object(support.archiv_start, "health_status", return_value=status), \
                patch.object(support.archiv_start, "stop_server") as stop:
            support.prepare(self.root)
        stop.assert_called_once_with(8771, self.root)

    def test_prepare_never_stops_unrelated_archive(self):
        status = {"app": support.archiv_start.APP_ID, "portal": True, "archive_root_fingerprint": "other"}
        with patch.object(support.archiv_start, "health_status", return_value=status), \
                patch.object(support.archiv_start, "stop_server") as stop:
            support.prepare(self.root)
        stop.assert_not_called()
        self.assertFalse((self.root / "ICC-ICJ").exists())

    def test_prepare_stops_at_bad_settings(self):
        (self.root / "archiv-einstellungen.json").write_text("not JSON", encoding="utf-8")
        with patch.object(support.archiv_start, "health_status") as health:
            with self.assertRaises(RuntimeError):
                support.prepare(self.root)
        health.assert_not_called()

    def test_active_operation_locks_block_prepare_without_changing_files(self):
        child_script = (
            "import msvcrt,sys;"
            "f=open(sys.argv[1],'r+b');"
            "msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);"
            "print('READY',flush=True);sys.stdin.read(1)"
        )
        for relative, label in support.OPERATION_LOCKS:
            with self.subTest(operation=label):
                lock_path = self.root / relative
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_bytes(b"original lock bytes")
                child = subprocess.Popen(
                    [sys.executable, "-c", child_script, str(lock_path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    self.assertEqual(child.stdout.readline().strip(), "READY")
                    with patch.object(support.archiv_start, "health_status") as health:
                        with self.assertRaisesRegex(RuntimeError, "noch aktiv"):
                            support.prepare(self.root)
                    health.assert_not_called()
                finally:
                    child.communicate("x", timeout=10)
                self.assertEqual(child.returncode, 0)
                self.assertEqual(lock_path.read_bytes(), b"original lock bytes")
                support.check_operation_locks(self.root)

    def test_main_returns_failure_when_update_refuses_shutdown(self):
        with patch.object(support, "prepare", side_effect=RuntimeError("Aktualisierung läuft")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(support.main(["--prepare", "--root", str(self.root)]), 1)
        self.assertIn("Aktualisierung läuft", (self.root / "installer-status.txt").read_text(encoding="utf-8"))

    def test_success_does_not_create_error_report(self):
        with patch.object(support, "prepare"):
            self.assertEqual(support.main(["--prepare", "--root", str(self.root)]), 0)
        self.assertFalse((self.root / "installer-status.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
