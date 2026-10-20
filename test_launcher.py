"""Launcher safety checks; no documents, desktop shortcuts, or real server are changed."""
import json
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import archiv_start as launch
import installieren


class LauncherTests(unittest.TestCase):
    def setUp(self):
        workspace = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(prefix=".test-launcher-", dir=workspace)
        self.root = Path(self.temp.name)
        def cleanup():
            if self.root.resolve().parent != workspace:
                raise RuntimeError("Test cleanup outside test workspace refused.")
            self.temp.cleanup()
        self.addCleanup(cleanup)
        self.own = {"app": launch.APP_ID, "portal": True,
                    "archive_root_fingerprint": launch.root_fingerprint(self.root)}

    def test_same_folder_reuses_its_server(self):
        with patch.object(launch, "health_status", return_value=self.own), patch.object(launch.subprocess, "Popen") as spawn:
            launch.ensure_server(8771, self.root)
            spawn.assert_not_called()

    def test_other_archive_is_not_reused_or_stopped(self):
        other = dict(self.own, archive_root_fingerprint="0" * 64)
        with patch.object(launch, "health_status", return_value=other), patch.object(launch.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "anderen Archiv"):
                launch.ensure_server(8771, self.root)
            spawn.assert_not_called()
        with patch.object(launch, "health_status", return_value=other), patch.object(launch, "local_json") as http:
            with self.assertRaisesRegex(RuntimeError, "anderen Archiv"):
                launch.stop_server(8771, self.root)
            http.assert_not_called()

    def test_old_server_without_fingerprint_is_foreign(self):
        self.assertFalse(launch.is_our_archive({"app": launch.APP_ID, "portal": True}, self.root))
        self.assertTrue(launch.is_our_archive(self.own, self.root))
        self.assertFalse(launch.is_our_archive(self.own, self.root / "other"))

    def test_unresponsive_occupied_port_is_not_replaced(self):
        with patch.object(launch, "health_status", return_value=None), patch.object(launch, "port_in_use", return_value=True), patch.object(launch.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "Port 8771"):
                launch.ensure_server(8771, self.root)
            spawn.assert_not_called()

    def test_new_server_starts_own_package_on_loopback_port(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(launch, "health_status", side_effect=[None, self.own]), patch.object(launch, "port_in_use", return_value=False), patch.object(launch, "venv_python", return_value=self.root / ".venv" / "python"), patch.object(launch.subprocess, "Popen", return_value=process) as spawn:
            launch.ensure_server(8772, self.root)
        command = spawn.call_args.args[0]
        self.assertEqual(command[-4:], ["-m", "research_portal.server", "--port", "8772"])
        self.assertEqual(spawn.call_args.kwargs["cwd"], self.root / "Recherche")
        process.terminate.assert_not_called()

    def test_stop_sends_own_identity_and_csrf_token(self):
        with patch.object(launch, "health_status", side_effect=[self.own, None]), patch.object(launch, "local_json", side_effect=[{"csrf": "test-token"}, {"stopping": True}]) as http:
            launch.stop_server(8771, self.root)
        self.assertEqual(http.call_args_list[1].args,
                         (8771, "/api/shutdown", {"archive_root_fingerprint": launch.root_fingerprint(self.root)}, "test-token"))

    def test_stop_refuses_busy_archive_with_helpful_message(self):
        busy = HTTPError("http://127.0.0.1:8771/api/shutdown", 409, "Busy", {}, None)
        with patch.object(launch, "health_status", return_value=self.own), patch.object(launch, "local_json", side_effect=[{"csrf": "token"}, busy]):
            with self.assertRaisesRegex(RuntimeError, "Abschluss abwarten"):
                launch.stop_server(8771, self.root)

    def test_configuration_accepts_bom_and_rejects_invalid_port(self):
        self.assertEqual(launch.configured_port(self.root), 8771)
        config = self.root / "archiv-einstellungen.json"
        config.write_text(json.dumps({"port": 8773}), encoding="utf-8-sig")
        self.assertEqual(launch.configured_port(self.root), 8773)
        for value in [0, 80, 65536, True, "8771.5", "abc"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                launch.validate_port(value)
        config.write_text('{"port": false}', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "ungültig"):
            launch.configured_port(self.root)

    def test_status_redirects_are_never_followed(self):
        with self.assertRaises(Exception):
            launch.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_installer_check_does_not_install_or_create_environment(self):
        with patch.object(installieren, "check_python"), patch.object(installieren, "venv") as env, patch.object(installieren.subprocess, "run") as run:
            self.assertEqual(installieren.main(["--check"]), 0)
            env.EnvBuilder.assert_not_called()
            run.assert_not_called()

    def test_bundled_runtime_used_instead_of_external_python(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        for name in ("python.exe", "pythonw.exe"):
            (runtime / name).touch()
        self.assertEqual(launch.venv_python(self.root), runtime / "python.exe")
        self.assertEqual(launch.venv_python(self.root, windowless=True), runtime / "pythonw.exe")

    def test_installer_rejects_python_older_than_312(self):
        with patch.object(installieren.sys, "version_info", (3, 11, 9)):
            with self.assertRaisesRegex(RuntimeError, "3.12"):
                installieren.check_python()

    def test_collection_routes_are_available(self):
        self.assertEqual(launch.COLLECTION_PATHS["un"], "/un/")
        self.assertEqual(launch.COLLECTION_PATHS["courts"], "/icc-icj/")
        self.assertEqual(launch.COLLECTION_PATHS["bip"], "/")


class ServerIdentityAndStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent / "Recherche"))
        from research_portal import server
        cls.module = server

    def setUp(self):
        module = self.module
        self.locks = [threading.Lock() for _ in range(3)]
        replacements = [
            patch.object(module.bip_server, "UPDATE_LOCK", self.locks[0]),
            patch.object(module, "COURT_UPDATE", self.locks[1]),
            patch.object(module, "UN_UPDATE", self.locks[2]),
            patch.object(module, "read_status", return_value={"phase": "idle"}),
            patch.object(module, "read_un_status", return_value={"phase": "idle"}),
            patch.object(module, "read_json", return_value={"phase": "idle"}),
        ]
        for replacement in replacements:
            replacement.start()
            self.addCleanup(replacement.stop)
        self.handler = module.Handler.__new__(module.Handler)
        self.handler.path = "/api/shutdown"
        self.handler.server = Mock(server_port=8771)
        self.handler.json = Mock()
        self.handler.headers = {
            "Host": "127.0.0.1:8771", "Origin": "http://127.0.0.1:8771",
            "X-BIP-Token": module.bip_server.TOKEN,
        }
        self.payload({"archive_root_fingerprint": module.archive_root_fingerprint()})

    def payload(self, value):
        body = json.dumps(value).encode("utf-8")
        self.handler.headers["Content-Length"] = str(len(body))
        self.handler.rfile = io.BytesIO(body)

    def test_server_and_launcher_identify_same_folder(self):
        self.assertEqual(self.module.archive_root_fingerprint(),
                         launch.root_fingerprint(Path(__file__).resolve().parent))
        self.assertNotIn(str(Path(__file__).resolve().parent), json.dumps(self.module.health_payload()))

    def test_shutdown_requires_correct_token(self):
        self.handler.headers["X-BIP-Token"] = "not-the-token"
        with patch.object(self.module.threading, "Thread") as start:
            self.handler.do_POST()
            self.assertEqual(self.handler.json.call_args.args[1], 403)
            start.assert_not_called()

    def test_shutdown_rejects_other_archive_identity(self):
        self.payload({"archive_root_fingerprint": "other"})
        with patch.object(self.module.threading, "Thread") as start:
            self.handler.do_POST()
            self.assertEqual(self.handler.json.call_args.args[1], 403)
            start.assert_not_called()

    def test_shutdown_rejects_active_update(self):
        self.locks[1].acquire()
        with patch.object(self.module.threading, "Thread") as start:
            self.handler.do_POST()
            self.assertEqual(self.handler.json.call_args.args[1], 409)
            start.assert_not_called()
        self.assertFalse(self.locks[0].locked())

    def test_shutdown_rejects_running_index_status(self):
        with patch.object(self.module, "read_un_status", return_value={"phase": "running"}), patch.object(self.module.threading, "Thread") as start:
            self.handler.do_POST()
            self.assertEqual(self.handler.json.call_args.args[1], 409)
            start.assert_not_called()
        self.assertFalse(any(lock.locked() for lock in self.locks))

    def test_successful_stop_holds_update_locks_until_exit(self):
        with patch.object(self.module.threading, "Thread") as start:
            self.handler.do_POST()
            self.handler.json.assert_called_once_with({"stopping": True})
            start.assert_called_once_with(target=self.handler.server.shutdown, daemon=True)
        self.assertTrue(all(lock.locked() for lock in self.locks))


if __name__ == "__main__":
    unittest.main()
