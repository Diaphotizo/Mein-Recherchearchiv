"""Regression checks for excluding private archive content from publication."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_publication import audit


class PublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("README.md", "# Public project\n")
        self.write("UN/sources-start.json", '{"sources": []}\n')
        self.write("UN/data/sources.json", '{"sources": []}\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_clean_source_and_generated_runtime_are_distinguished(self):
        self.write("runtime/python.exe", "generated binary")
        self.write("installer/output/setup.exe", "generated binary")
        result = audit(self.root)
        self.assertEqual(result["source_files"], 3)
        self.assertEqual(result["status"], "pass")

    def test_downloaded_pdf_is_rejected(self):
        self.write("UN/PDF/private.pdf", "not source")
        with self.assertRaisesRegex(ValueError, "not an approved source"):
            audit(self.root)

    def test_annotations_are_rejected(self):
        self.write("BIP/recherche/data/annotations.json", '{"notes": "private"}')
        with self.assertRaisesRegex(ValueError, "not an approved source"):
            audit(self.root)

    def test_custom_source_settings_are_rejected(self):
        self.write("UN/data/sources.json", '{"sources": ["private"]}\n')
        with self.assertRaisesRegex(ValueError, "differs from public seed"):
            audit(self.root)

    def test_personal_path_in_allowed_source_is_rejected(self):
        self.write("README.md", "C:" + "\\Users\\example-person\\private\\file")
        with self.assertRaisesRegex(ValueError, "personal home path"):
            audit(self.root)

    def test_secret_in_allowed_source_is_rejected_without_printing_value(self):
        value = "gh" + "p_" + "a" * 36
        self.write("README.md", value)
        with self.assertRaisesRegex(ValueError, "GitHub access token") as raised:
            audit(self.root)
        self.assertNotIn(value, str(raised.exception))

    def test_secret_file_is_rejected(self):
        self.write(".env", "SIGNPATH_API_TOKEN=example")
        with self.assertRaisesRegex(ValueError, "not an approved source"):
            audit(self.root)

    def test_renamed_private_document_is_not_accepted(self):
        self.write("docs/financial.pdf", "not source")
        with self.assertRaisesRegex(ValueError, "not an approved source"):
            audit(self.root)


if __name__ == "__main__":
    unittest.main()
