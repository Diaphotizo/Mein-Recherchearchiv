"""Offline checks for clean source packaging and unsigned build boundaries."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

MODULE_PATH = Path(__file__).with_name("build_setup.py")
spec = importlib.util.spec_from_file_location("archive_build_setup", MODULE_PATH)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class SourcePackagingTests(unittest.TestCase):
    def setUp(self):
        fixtures = MODULE_PATH.parent / "build" / "packaging-tests"
        fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=fixtures)
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        assert self.repo.is_relative_to(fixtures.resolve())
        self.installer = self.repo / "installer"
        self.installer.mkdir()
        self.application = self.repo / "archiv_start.py"
        self.application.write_text("# public app", encoding="utf-8")
        for relative in build.INSTALLER_SOURCES:
            path = self.installer / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# public build source", encoding="utf-8")
        for relative in build.PUBLICATION_SOURCES:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# public repository source", encoding="utf-8")
        docs = self.repo / "docs"
        docs.mkdir()
        (docs / "FREIGABE.md").write_text("Public instructions", encoding="utf-8")
        for relative in ("runtime/python.exe", "UN/PDF/private.pdf",
                         "UN/data/annotations.json", "BIP/recherche/data/library.sqlite",
                         "installer/downloads/key.pfx", ".git/config"):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"PRIVATE_SENTINEL")
        self.addCleanup(patch.stopall)
        patch.object(build, "ROOT", self.installer).start()
        patch.object(build, "PAYLOAD", self.repo).start()
        patch.object(build, "app_files", return_value=[self.application]).start()

    def test_source_zip_keeps_public_layout_and_excludes_runtime_and_user_data(self):
        output = build.source_zip()
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            members = set(archive.namelist())
            self.assertIn("Mein-Recherchearchiv/installer/build_setup.py", members)
            self.assertIn("Mein-Recherchearchiv/docs/FREIGABE.md", members)
            self.assertIn("Mein-Recherchearchiv/.github/workflows/windows-unsigned.yml", members)
            manifest = json.loads(archive.read("Mein-Recherchearchiv/DATEIEN-SHA256.json"))
            self.assertEqual(len(manifest) + 1, len(members))
            for relative, expected in manifest.items():
                content = archive.read("Mein-Recherchearchiv/" + relative)
                self.assertNotIn(b"PRIVATE_SENTINEL", content)
                self.assertEqual(expected["sha256"], hashlib.sha256(content).hexdigest())
                self.assertEqual(expected["bytes"], len(content))

    def test_existing_source_zip_is_not_overwritten(self):
        output = build.source_zip()
        previous = output.read_bytes()
        with self.assertRaises(FileExistsError):
            build.source_zip()
        self.assertEqual(previous, output.read_bytes())

    def test_missing_public_source_fails_before_zip_creation(self):
        (self.repo / "SECURITY.md").unlink()
        with self.assertRaises(FileNotFoundError):
            build.source_zip()
        self.assertFalse((self.installer / "output").exists())

    def test_source_zip_does_not_require_runtime_or_download(self):
        with patch.object(sys, "argv", ["build_setup.py", "--source-zip"]), \
             patch.object(build, "verify_publication"), \
             patch.object(build, "prepare_runtime") as prepare, \
             patch.object(build, "write_manifest") as manifest:
            build.main()
        prepare.assert_not_called()
        manifest.assert_not_called()

    def test_mismatched_runtime_download_is_rejected_before_installation(self):
        downloads = self.installer / "downloads"
        downloads.mkdir(exist_ok=True)
        (downloads / build.PYTHON_URL.rsplit("/", 1)[-1]).write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            build.prepare_runtime()

    def test_failed_publication_guard_prevents_download_or_compile(self):
        with patch.object(sys, "argv", ["build_setup.py", "--prepare-runtime"]), \
             patch.object(build, "verify_publication", side_effect=RuntimeError("blocked")), \
             patch.object(build, "prepare_runtime") as prepare:
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                build.main()
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
