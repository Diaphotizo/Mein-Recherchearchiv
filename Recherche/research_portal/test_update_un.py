"""Safety and preservation checks for the public UN importer (no network)."""
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

from . import update_un as u


def pdf_bytes(text="A valid public document"):
    text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 30 700 Td ({text}) Tj ET".encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    start = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(output)


class Response(io.BytesIO):
    def __init__(self, data, url, headers=None):
        super().__init__(data)
        self.url, self.status = url, 200
        self.headers = {"Content-Length": str(len(data)), "Content-Type": "application/pdf", **(headers or {})}


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes, self.calls = outcomes, []

    def open(self, url, accept="*/*", headers=None):
        self.calls.append((url, dict(headers or {})))
        result = self.outcomes[url]
        if isinstance(result, Exception):
            raise result
        if isinstance(result, tuple):
            return Response(result[0], url, result[1])
        return Response(result, url)

    def text(self, url):
        with self.open(url) as response:
            return response.read().decode(), url

    def fetch_bytes(self, url):
        with self.open(url) as response:
            return response.read()


def row(url="https://www.un.org/a.pdf", country="germany", language="en"):
    return {"title": "UN public report", "date": "2025-10-01", "language": language,
        "symbol": "A/TEST/1", "pdf_url": url, "url": url,
        "relations": {country: True}, "relation_reasons": {country: ["Explicit subject metadata"]}}


class ImporterTests(unittest.TestCase):
    def setUp(self):
        temp_base = Path(__file__).resolve().parents[1] / ".test-tmp"
        temp_base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="un-import-test-", dir=temp_base)
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        self.pdf = pdf_bytes()
        self.rebuild = Mock(side_effect=lambda: (self.root / "data" / "un_index.sqlite3").write_bytes(b"valid-index"))

    def tearDown(self):
        allowed = (Path(__file__).resolve().parents[1] / ".test-tmp").resolve()
        self.root.resolve().relative_to(allowed)
        # Dropbox can briefly retain directory handles after child deletion.
        # Retry cleanup instead of silently accumulating test directories.
        for attempt in range(8):
            try:
                self.temporary.cleanup()
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.15 * (attempt + 1))

    def manifest(self, rows, **extra):
        u.atomic_json(self.root / "data" / "sources.json", {"documents": rows, **extra})

    def run_import(self, client):
        with redirect_stdout(io.StringIO()):
            return u.run_update(client=client, root=self.root, rebuild=self.rebuild)

    def catalog(self):
        return u.read_json(self.root / "data" / "catalog.json")

    def seed(self):
        self.manifest([row()])
        result = self.run_import(FakeClient({row()["pdf_url"]: (self.pdf, {"ETag": '"v1"'})}))
        self.assertEqual(result["phase"], "complete")
        self.rebuild.reset_mock()

    def test_pdf_is_validated_and_indexed(self):
        self.seed()
        record = self.catalog()["documents"][0]
        self.assertEqual(record["sha256"], hashlib.sha256(self.pdf).hexdigest())
        self.assertEqual(record["id"], record["sha256"])
        self.assertEqual(record["pages"], 1)
        self.assertEqual(Path(record["file_path"]).read_bytes(), self.pdf)

    @unittest.skipUnless(u.os.name == "nt", "Windows byte-range lock regression")
    def test_live_windows_lock_returns_recoverable_error_without_reading_locked_byte(self):
        code = ("import sys\nfrom research_portal.update_un import update_lock\n"
                "with update_lock(sys.argv[1]):\n"
                " print('locked', flush=True)\n"
                " sys.stdin.readline()\n")
        process = subprocess.Popen([sys.executable, "-B", "-c", code, str(self.root)],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(RuntimeError, "UN-Abgleich läuft bereits"):
                with u.update_lock(self.root):
                    self.fail("A second process must not acquire the held lock")
        finally:
            try:
                unused, errors = process.communicate("\n", timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
        self.assertEqual(process.returncode, 0, errors)
        with u.update_lock(self.root):
            self.assertEqual((self.root / "data" / "update.lock").stat().st_size, 1)

    def test_wrong_language_and_scope_are_not_downloaded(self):
        self.seed()
        before = self.catalog()["documents"]
        self.manifest([row("https://www.un.org/french.pdf", language="fr"),
                       {**row("https://www.un.org/unrelated.pdf"), "relations": {}}])
        unchanged = urllib.error.HTTPError(row()["pdf_url"], 304, "Unchanged", {}, None)
        client = FakeClient({row()["pdf_url"]: unchanged})
        result = self.run_import(client)
        self.assertEqual(result["phase"], "partial")
        self.assertEqual([call[0] for call in client.calls], [row()["pdf_url"]])
        self.assertEqual(before, self.catalog()["documents"])

    def test_unknown_language_requires_clear_pdf_evidence(self):
        self.manifest([row(language="")])
        client = FakeClient({row()["pdf_url"]: self.pdf})
        self.assertEqual(self.run_import(client)["phase"], "error")
        self.assertIsNone(self.catalog())
        english = "Dear Excellency, " + "We have the honour to address you on the rights of the people and the government in this matter. " * 4
        result = self.run_import(FakeClient({row()["pdf_url"]: pdf_bytes(english)}))
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(self.catalog()["documents"][0]["language"], "en")

    def test_manual_review_normalization_preserves_evidence_and_rejects_invalid_proof(self):
        proof = {"status": "language_verified", "sha256": hashlib.sha256(self.pdf).hexdigest(),
                 "method": "All pages visually reviewed", "review_images": ["review-1.png"]}
        normalized = u.normalize_record({**row(), "manual_review": proof})
        self.assertEqual(normalized["manual_review"], {**proof, "language": "en"})
        self.assertEqual(u.merge_records(row(), normalized)["manual_review"], normalized["manual_review"])
        for invalid in ({}, {**proof, "sha256": "unknown"}, {**proof, "status": "pending"}, {**proof, "language": "fr"}):
            with self.subTest(proof=invalid), self.assertRaisesRegex(ValueError, 'manuelle Sprachprüfung'):
                u.normalize_record({**row(), "manual_review": invalid})

    def test_manual_language_proof_accepts_exact_short_or_scan_pdf_only(self):
        items, outcomes = [], {}
        for number, (text, status) in enumerate((("Short response", "language_verified"), ("", "language_verified_scan"))):
            content = pdf_bytes(text)
            item = row(f"https://www.un.org/reviewed-{number}.pdf")
            item["manual_review"] = {"status": status, "sha256": hashlib.sha256(content).hexdigest(),
                                     "method": "All pages visually reviewed"}
            items.append(item)
            outcomes[item["pdf_url"]] = content
        self.manifest(items)
        self.assertEqual(self.run_import(FakeClient(outcomes))["phase"], "complete")
        for item in self.catalog()["documents"]:
            self.assertEqual(item["manual_review"]["sha256"], item["sha256"])
            self.assertEqual(item["manual_review"]["language"], "en")
            self.assertEqual(item["language_validation"]["sha256"], item["sha256"])
            self.assertIn("Manuelle Sprachprüfung", item["language_validation"]["method"])

    def test_changed_manually_reviewed_pdf_preserves_archive_when_language_is_unclear(self):
        reviewed = {**row(), "manual_review": {"status": "language_verified",
            "sha256": hashlib.sha256(self.pdf).hexdigest(), "method": "All pages visually reviewed"}}
        self.manifest([reviewed])
        self.assertEqual(self.run_import(FakeClient({row()["pdf_url"]: (self.pdf, {"ETag": '"v1"'})}))["phase"], "complete")
        before = (self.root / "data" / "catalog.json").read_bytes()
        self.rebuild.reset_mock()
        # Both fresh discovery without the proof and historical fallback must
        # retain the known manual gate, even with language=en in metadata.
        for manifest_rows in ([row()], []):
            self.manifest(manifest_rows)
            result = self.run_import(FakeClient({row()["pdf_url"]: (pdf_bytes("Changed short response"), {"ETag": '"v2"'})}))
            self.assertEqual(result["phase"], "error")
            self.assertTrue(any("SHA-256 geändert" in error["error"] and "erneute Prüfung" in error["error"] for error in result["errors"]))
            self.assertEqual(before, (self.root / "data" / "catalog.json").read_bytes())
        self.rebuild.assert_not_called()

    def test_changed_manually_reviewed_pdf_is_reclassified_from_content(self):
        reviewed_sha = hashlib.sha256(self.pdf).hexdigest()
        reviewed = {**row(), "manual_review": {"status": "language_verified", "sha256": reviewed_sha,
                                               "method": "All pages visually reviewed"}}
        self.manifest([reviewed])
        self.assertEqual(self.run_import(FakeClient({row()["pdf_url"]: self.pdf}))["phase"], "complete")
        german = "die der und das den von zu des mit wir sie haben regierung menschenrechte dieser ihre wurden ist sind " * 4
        changed = pdf_bytes(german)
        result = self.run_import(FakeClient({row()["pdf_url"]: changed}))
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(result["changed"], 1)
        item = next(item for item in self.catalog()["documents"] if item["sha256"] == hashlib.sha256(changed).hexdigest())
        self.assertEqual(item["language"], "de")
        self.assertEqual(item["languages"], ["de"])
        self.assertEqual(item["manual_review"]["language"], "en")
        self.assertEqual(item["manual_review"]["sha256"], reviewed_sha)
        self.assertEqual(item["language_validation"]["previous_review_sha256"], reviewed_sha)
        self.assertIn("nicht angewendet", item["language_validation"]["note"])

    def test_failed_fetch_preserves_catalog_index_and_validator(self):
        self.seed()
        before = (self.root / "data" / "catalog.json").read_bytes()
        failure = urllib.error.HTTPError(row()["pdf_url"], 503, "Unavailable", {}, None)
        result = self.run_import(FakeClient({row()["pdf_url"]: failure}))
        self.assertEqual(result["phase"], "error")
        self.assertEqual(before, (self.root / "data" / "catalog.json").read_bytes())
        self.assertEqual((self.root / "data" / "un_index.sqlite3").read_bytes(), b"valid-index")
        self.rebuild.assert_not_called()

    def test_corrupt_new_download_does_not_change_old_version(self):
        self.seed()
        before = (self.root / "data" / "catalog.json").read_bytes()
        self.run_import(FakeClient({row()["pdf_url"]: (b"<html>blocked</html>", {"ETag": '"v2"'})}))
        self.assertEqual(before, (self.root / "data" / "catalog.json").read_bytes())

    def test_hash_dedup_merges_relations_and_keeps_notes(self):
        self.seed()
        catalog = self.catalog()
        catalog["documents"][0]["notes"] = "My own annotation"
        u.atomic_json(self.root / "data" / "catalog.json", catalog)
        second = row("https://www.ohchr.org/second.pdf", "israel", "de")
        self.manifest([second])
        result = self.run_import(FakeClient({second["pdf_url"]: self.pdf}))
        self.assertEqual(result["duplicates"], 1)
        documents = self.catalog()["documents"]
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["notes"], "My own annotation")
        self.assertTrue(documents[0]["relations"]["germany"])
        self.assertTrue(documents[0]["relations"]["israel"])
        self.assertEqual(set(documents[0]["languages"]), {"en", "de"})
        self.assertIn(second["pdf_url"], documents[0]["pdf_urls"])

    def test_changed_pdf_retains_previous_version_and_annotations(self):
        self.seed()
        old = self.catalog()["documents"][0]
        annotations = self.root / "data" / "annotations.json"
        annotations.write_text('{"private":"keep"}', encoding="utf-8")
        result = self.run_import(FakeClient({row()["pdf_url"]: (pdf_bytes("Updated public report"), {"ETag": '"v2"'})}))
        self.assertEqual(result["changed"], 1)
        self.assertEqual(len(self.catalog()["documents"]), 2)
        self.assertEqual(Path(old["file_path"]).read_bytes(), self.pdf)
        self.assertEqual(annotations.read_text(), '{"private":"keep"}')

    def test_304_is_reported_as_server_confirmation_without_reindex(self):
        self.seed()
        unchanged = urllib.error.HTTPError(row()["pdf_url"], 304, "Unchanged", {}, None)
        client = FakeClient({row()["pdf_url"]: unchanged})
        result = self.run_import(client)
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(client.calls[0][1]["If-None-Match"], '"v1"')
        self.assertEqual(self.catalog()["validators"][row()["pdf_url"]]["validation"], "server_not_modified")
        self.rebuild.assert_not_called()

    def test_304_without_local_file_is_not_accepted(self):
        self.seed()
        Path(self.catalog()["documents"][0]["file_path"]).unlink()
        unchanged = urllib.error.HTTPError(row()["pdf_url"], 304, "Unchanged", {}, None)
        client = FakeClient({row()["pdf_url"]: unchanged})
        self.assertEqual(self.run_import(client)["phase"], "error")
        self.assertFalse(client.calls[0][1])

    def test_old_url_outside_recent_window_checks_latest_version_once(self):
        self.seed()
        old_url = row()["pdf_url"]
        latest_pdf = pdf_bytes("A more recent archived version")
        self.run_import(FakeClient({old_url: (latest_pdf, {"ETag": '"v2"'})}))
        previous = self.catalog()
        # Keep deliberately confusing historical order and date: the last
        # verified validator, not catalog iteration, owns the current version.
        previous["documents"][0]["downloaded_at"] = "2099-01-01T00:00:00Z"
        previous["documents"][1]["language"] = "en-de"
        previous["documents"][1]["languages"] = ["en", "de"]
        u.atomic_json(self.root / "data" / "catalog.json", previous)
        recent = {**row("https://www.un.org/recent-window.pdf", "israel"), "date": "2026-10-13"}
        self.manifest([recent])
        unchanged = urllib.error.HTTPError(old_url, 304, "Unchanged", {}, None)
        client = FakeClient({old_url: unchanged, recent["pdf_url"]: pdf_bytes("Recently listed")})
        result = self.run_import(client)
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["changed"], 0)
        old_calls = [headers for url, headers in client.calls if url == old_url]
        self.assertEqual(old_calls, [{"If-None-Match": '"v2"'}])
        self.assertEqual(self.catalog()["validators"][old_url]["sha256"], hashlib.sha256(latest_pdf).hexdigest())
        self.assertEqual(len(self.catalog()["documents"]), 3)
        for record in previous["documents"]:
            self.assertTrue(Path(record["file_path"]).is_file())

    def test_current_discovery_metadata_has_priority_over_archived_fallback(self):
        self.seed()
        current = {**row(country="israel"), "title": "Current authoritative title"}
        self.manifest([current])
        unchanged = urllib.error.HTTPError(row()["pdf_url"], 304, "Unchanged", {}, None)
        client = FakeClient({row()["pdf_url"]: unchanged})
        self.assertEqual(self.run_import(client)["phase"], "complete")
        self.assertEqual(len(client.calls), 1)
        record = self.catalog()["documents"][0]
        self.assertIn("Current authoritative title", record["titles"])
        self.assertTrue(record["relations"]["israel"])
        self.assertTrue(record["relations"]["germany"])

    def test_partial_success_keeps_last_full_check_time(self):
        self.seed()
        checked = self.catalog()["checked_at"]
        fresh = row("https://www.un.org/new.pdf", "palestine")
        bad = row("https://www.un.org/bad.pdf")
        self.manifest([fresh, bad])
        result = self.run_import(FakeClient({fresh["pdf_url"]: pdf_bytes("New"), bad["pdf_url"]: ValueError("Unavailable")}))
        self.assertEqual(result["phase"], "partial")
        self.assertEqual(self.catalog()["checked_at"], checked)
        self.assertEqual(len(self.catalog()["documents"]), 2)

    def test_index_failure_preserves_old_index_and_sets_pending(self):
        self.seed()
        self.rebuild.side_effect = RuntimeError("Index build failed before atomic publish")
        result = self.run_import(FakeClient({row()["pdf_url"]: pdf_bytes("New version")}))
        self.assertEqual(result["phase"], "partial")
        self.assertTrue(self.catalog()["index_pending"])
        self.assertEqual((self.root / "data" / "un_index.sqlite3").read_bytes(), b"valid-index")

    def test_catalog_publication_failure_preserves_previous_bytes(self):
        self.seed()
        before = (self.root / "data" / "catalog.json").read_bytes()
        original = u.atomic_json
        def fail_catalog(path, value):
            if Path(path).name == "catalog.json":
                raise PermissionError("Catalog locked")
            return original(path, value)
        with patch.object(u, "atomic_json", side_effect=fail_catalog):
            result = self.run_import(FakeClient({row()["pdf_url"]: pdf_bytes("New version")}))
        self.assertEqual(result["phase"], "error")
        self.assertEqual(before, (self.root / "data" / "catalog.json").read_bytes())
        self.rebuild.assert_not_called()

    def test_discovery_hook_feeds_same_validation_path(self):
        self.manifest([], discovery={"enabled": True})
        module = Mock()
        module.discover.return_value = {"documents": [row()], "coverage_notes": ["Selected source only"], "errors": []}
        with patch.dict("sys.modules", {"research_portal.un_discovery": module}):
            result = self.run_import(FakeClient({row()["pdf_url"]: self.pdf}))
        self.assertEqual(result["phase"], "complete")
        self.assertIn("Selected source only", self.catalog()["coverage_notes"])

    def test_discovery_failure_still_imports_valid_configured_documents(self):
        self.manifest([row()], discovery={"enabled": True})
        module = Mock()
        module.discover.side_effect = RuntimeError("Source list unavailable")
        with patch.dict("sys.modules", {"research_portal.un_discovery": module}):
            result = self.run_import(FakeClient({row()["pdf_url"]: self.pdf}))
        self.assertEqual(result["phase"], "partial")
        self.assertEqual(len(self.catalog()["documents"]), 1)
        self.assertIsNone(self.catalog()["checked_at"])


    page = "https://www.un.org/unispal/document/briefing/"

    def resolution(self):
        return {"source_url": self.page, "pdf_url": row()["pdf_url"],
                "evidence_url": "https://unsco.unmissions.org/en/briefing",
                "note": "Offizielle UNSCO-Anlage zur selben Rede."}

    def attachment_warning(self):
        return {"source": "UNISPAL-Anlage", "url": self.page, "error": "Keine direkt zugeordnete PDF-Anlage"}

    def test_matching_validated_get_resolves_attachment_warning(self):
        self.manifest([row()], discovery={"enabled": True}, source_resolutions=[self.resolution()])
        module = Mock()
        module.discover.return_value = {"errors": [self.attachment_warning()]}
        with patch.dict("sys.modules", {"research_portal.un_discovery": module}):
            result = self.run_import(FakeClient({row()["pdf_url"]: self.pdf}))
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(result["resolved_sources"], [self.resolution()])
        self.assertTrue(any(self.resolution()["note"] in note for note in self.catalog()["coverage_notes"]))

    def test_matching_304_with_existing_pdf_resolves_attachment_warning(self):
        self.seed()
        self.manifest([row()], discovery={"enabled": True}, source_resolutions=[self.resolution()])
        module = Mock()
        module.discover.return_value = {"errors": [self.attachment_warning()]}
        unchanged = urllib.error.HTTPError(row()["pdf_url"], 304, "Unchanged", {}, None)
        with patch.dict("sys.modules", {"research_portal.un_discovery": module}):
            result = self.run_import(FakeClient({row()["pdf_url"]: unchanged}))
        self.assertEqual(result["phase"], "complete")
        self.assertEqual(result["resolved_sources"], [self.resolution()])
        self.rebuild.assert_not_called()

    def test_failed_alternate_pdf_does_not_resolve_warning(self):
        fresh = row("https://www.un.org/other.pdf")
        self.manifest([row(), fresh], discovery={"enabled": True}, source_resolutions=[self.resolution()])
        module = Mock()
        module.discover.return_value = {"errors": [self.attachment_warning()]}
        with patch.dict("sys.modules", {"research_portal.un_discovery": module}):
            result = self.run_import(FakeClient({row()["pdf_url"]: b"<html>not a PDF</html>",
                                                fresh["pdf_url"]: self.pdf}))
        self.assertEqual(result["phase"], "partial")
        self.assertIn(self.attachment_warning(), result["errors"])
        self.assertEqual(result["resolved_sources"], [])

    def test_other_url_or_historical_catalog_is_not_current_success(self):
        warning = self.attachment_warning()
        for successful in ([], ["https://www.un.org/a.pdf?different=1"]):
            with self.subTest(successful=successful):
                remaining, resolved = u.resolve_source_warnings([warning], [self.resolution()], successful)
                self.assertEqual(remaining, [warning])
                self.assertEqual(resolved, [])

    def test_page_fetch_and_unrelated_attachment_errors_stay_visible(self):
        page_error = {"source": "UNISPAL-Dokumentseite", "url": self.page, "error": "HTTP 403"}
        other = {**self.attachment_warning(), "url": self.page + "different/"}
        errors = [self.attachment_warning(), page_error, other]
        remaining, resolved = u.resolve_source_warnings(errors, [self.resolution()], [row()["pdf_url"]])
        self.assertEqual(remaining, [page_error, other])
        self.assertEqual(resolved, [self.resolution()])
        self.assertEqual(len(errors), 3)  # The helper must not mutate its input.

    def test_untrusted_or_incomplete_mapping_cannot_hide_warning(self):
        for mapping in ({**self.resolution(), "evidence_url": "https://evil.test/source"},
                        {**self.resolution(), "note": ""}, {"source_url": self.page}):
            with self.subTest(mapping=mapping):
                remaining, resolved = u.resolve_source_warnings(
                    [self.attachment_warning()], [mapping], [row()["pdf_url"]])
                self.assertEqual(remaining, [self.attachment_warning()])
                self.assertEqual(resolved, [])

    def manual_source(self):
        sha = hashlib.sha256(self.pdf).hexdigest()
        path = self.root / "PDF" / (sha + ".pdf")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(self.pdf)
        record = {**row(), "id": sha, "sha256": sha, "file_path": str(path),
                  "pages": 1, "bytes": len(self.pdf), "source_urls": [self.page],
                  "import_provenance": {"method": "user_download", "sha256": sha,
                                        "reviewed_at": "2026-10-20T12:00:00+00:00",
                                        "source_filenames": ["original-ENa.pdf"]}}
        mapping = {"source_url": self.page, "pdf_url": row()["pdf_url"], "sha256": sha,
                   "note": "Manuell heruntergeladen und am Dokument geprüft."}
        return record, mapping

    def test_manual_copy_resolves_only_exact_attachment_warning(self):
        record, mapping = self.manual_source()
        errors = [self.attachment_warning(), {**self.attachment_warning(), "url": self.page + "other/"}]
        original = json.dumps([errors, record, mapping], sort_keys=True)
        remaining, resolved = u.resolve_manual_source_warnings(errors, [mapping], [record], self.root)
        self.assertEqual(remaining, [errors[1]])
        self.assertEqual(resolved, [{**mapping, "method": "manual_local"}])
        self.assertEqual(json.dumps([errors, record, mapping], sort_keys=True), original)

    def test_manual_copy_missing_corrupted_or_outside_archive_cannot_resolve(self):
        for condition in ("missing", "corrupt", "outside", "wrong_proof_sha", "wrong_mapping_sha",
                          "wrong_method", "missing_review", "wrong_source", "wrong_pdf"):
            with self.subTest(condition=condition):
                record, mapping = self.manual_source()
                path = Path(record["file_path"])
                if condition == "missing":
                    path.unlink()
                elif condition == "corrupt":
                    path.write_bytes(self.pdf + b"changed")
                elif condition == "outside":
                    outside = self.root / "outside.pdf"
                    outside.write_bytes(self.pdf)
                    record["file_path"] = str(outside)
                elif condition == "wrong_proof_sha":
                    record["import_provenance"]["sha256"] = "a" * 64
                elif condition == "wrong_mapping_sha":
                    mapping["sha256"] = "a" * 64
                elif condition == "wrong_method":
                    record["import_provenance"]["method"] = "remote_get"
                elif condition == "missing_review":
                    record["import_provenance"].pop("reviewed_at")
                elif condition == "wrong_source":
                    record["source_urls"] = []
                elif condition == "wrong_pdf":
                    record["pdf_url"] += "?other=1"
                warning = self.attachment_warning()
                remaining, resolved = u.resolve_manual_source_warnings([warning], [mapping], [record], self.root)
                self.assertEqual(remaining, [warning])
                self.assertEqual(resolved, [])

    def test_manual_copy_does_not_hide_pdf_host_or_page_fetch_failures(self):
        record, mapping = self.manual_source()
        errors = [
            {"source": "PDF", "url": row()["pdf_url"], "error": "Kein vollständiger PDF-Abruf (HTTP 202)"},
            {"source": "PDF", "url": row()["pdf_url"], "paused_host": "www.un.org", "error": "HTTP 403"},
            {"source": "UNISPAL-Dokumentseite", "url": self.page, "error": "HTTP 403"},
            {"source": "Quellenliste", "url": self.page, "error": "HTTP 429"},
        ]
        original = json.dumps(errors, sort_keys=True)
        remaining, resolved = u.resolve_manual_source_warnings(errors, [mapping], [record], self.root)
        self.assertEqual(resolved, [])
        self.assertEqual(len(remaining), 4)
        for before, after in zip(errors, remaining):
            self.assertEqual(after["source"], before["source"])
            self.assertEqual(after["url"], before["url"])
            self.assertTrue(after["local_copy_available"])
            self.assertEqual(after["local_copy_sha256"], mapping["sha256"])
            self.assertIn(before["error"], after["error"])
        self.assertEqual(remaining[1]["paused_host"], "www.un.org")
        self.assertEqual(json.dumps(errors, sort_keys=True), original)
        again, unused = u.resolve_manual_source_warnings(remaining, [mapping], [record], self.root)
        self.assertEqual(again, remaining)

    def test_update_keeps_manual_copy_available_but_202_is_partial(self):
        self.seed()
        previous = self.catalog()
        previous_checked = previous["checked_at"]
        record, mapping = self.manual_source()
        previous["documents"][0].update(record)
        u.atomic_json(self.root / "data" / "catalog.json", previous)
        other = row("https://www.un.org/other.pdf")
        self.manifest([row(), other], discovery={"enabled": True}, manual_acquisitions=[mapping])
        discovery = Mock()
        discovery.discover.return_value = {"errors": [self.attachment_warning()]}
        blocked = urllib.error.HTTPError(row()["pdf_url"], 202, "Not complete", {}, None)
        with patch.dict("sys.modules", {"research_portal.un_discovery": discovery}):
            result = self.run_import(FakeClient({row()["pdf_url"]: blocked,
                                                other["pdf_url"]: pdf_bytes("A separate public document")}))
        self.assertEqual(result["phase"], "partial")
        self.assertEqual(result["successful_checks"], 1)
        self.assertEqual(result["resolved_sources"], [{**mapping, "method": "manual_local"}])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["source"], "PDF")
        self.assertIn("202", result["errors"][0]["error"])
        self.assertTrue(result["errors"][0]["local_copy_available"])
        self.assertEqual(self.catalog()["checked_at"], previous_checked)
        preserved = next(item for item in self.catalog()["documents"] if item["sha256"] == mapping["sha256"])
        self.assertEqual(preserved["import_provenance"], record["import_provenance"])


class PublicAccessTests(unittest.TestCase):
    def test_only_official_public_https_hosts(self):
        for url in ("http://www.un.org/a.pdf", "https://un.org.evil.test/a", "https://user@www.un.org/a",
                    "https://127.0.0.1/a", "https://www.un.org:8443/a", "file:///tmp/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                u.safe_url(url)
        self.assertEqual(u.safe_url("https://spcommreports.ohchr.org/a.pdf#page=1"), "https://spcommreports.ohchr.org/a.pdf")
        self.assertEqual(u.safe_url("https://unsco.unmissions.org/sites/default/files/briefing.pdf"),
                         "https://unsco.unmissions.org/sites/default/files/briefing.pdf")
        with self.assertRaises(ValueError):
            u.safe_url("https://unmissions.org.evil.test/a.pdf")
        self.assertEqual(u.safe_url("https://www.refworld.org/sites/default/files/document.pdf"),
                         "https://www.refworld.org/sites/default/files/document.pdf")
        with self.assertRaises(ValueError):
            u.safe_url("https://refworld.org.evil.test/a.pdf")

    def client(self, outcomes):
        clock = [0.0]
        limiter = u.HostLimiter(clock=lambda: clock[0], sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        client = u.PublicClient(limiter)
        calls = []
        def open_request(request, timeout):
            calls.append((request.full_url, request.get_method(), clock[0]))
            outcome = outcomes[request.full_url]
            if isinstance(outcome, Exception):
                raise outcome
            return Response(outcome, request.full_url, {"Content-Type": "text/plain" if request.full_url.endswith("robots.txt") else "application/pdf"})
        client.opener = Mock()
        client.opener.open.side_effect = open_request
        return client, calls

    def test_robots_disallow_prevents_document_request(self):
        client, calls = self.client({"https://www.un.org/robots.txt": b"User-agent: *\nDisallow: /private/\n"})
        with self.assertRaisesRegex(ValueError, "robots.txt"):
            client.open("https://www.un.org/private/a.pdf")
        self.assertEqual(len(calls), 1)

    def test_crawl_delay_and_get_only(self):
        client, calls = self.client({"https://www.un.org/robots.txt": b"User-agent: *\nCrawl-delay: 5\n",
            "https://www.un.org/a.pdf": b"%PDF-", "https://www.un.org/b.pdf": b"%PDF-"})
        client.open("https://www.un.org/a.pdf").close()
        client.open("https://www.un.org/b.pdf").close()
        self.assertEqual([call[1] for call in calls], ["GET"] * 3)
        self.assertGreaterEqual(calls[1][2] - calls[0][2], 5)
        self.assertGreaterEqual(calls[2][2] - calls[1][2], 5)

    def test_403_pauses_host_without_retry(self):
        url = "https://www.un.org/a.pdf"
        client, calls = self.client({"https://www.un.org/robots.txt": b"User-agent: *\nAllow: /\n",
            url: urllib.error.HTTPError(url, 403, "Denied", {}, None)})
        for unused in range(2):
            with self.assertRaises(u.HostPaused):
                client.open(url)
        self.assertEqual(len(calls), 2)

    def test_429_cooldown_survives_next_client(self):
        limiter = u.HostLimiter()
        limiter.pause("https://www.un.org/a", "Limit", "3600", 429)
        restored = u.HostLimiter()
        restored.restore_cooldowns(limiter.snapshot())
        with self.assertRaises(u.HostPaused):
            restored.acquire("https://www.un.org/b")

    def test_redirect_to_unapproved_host_rejected_before_request(self):
        client = Mock()
        handler = u.SafeRedirect(client)
        with self.assertRaises(ValueError):
            handler.redirect_request(urllib.request.Request("https://www.un.org/a"), None, 302, "Found", {}, "https://evil.test/a.pdf")
        client.authorize.assert_not_called()

    def test_listing_does_not_invent_date_or_scope(self):
        url = "https://www.un.org/list"
        client = FakeClient({url: b'<a href="/one.pdf">An official report</a>'})
        rows, audit = u.discover_listing(client, {"type": "html_pdf_listing", "url": url,
            "metadata": {"language": "de", "relations": {"germany": True}}})
        self.assertEqual(rows[0]["date"], "")
        self.assertFalse(rows[0]["relations"]["israel"])
        self.assertEqual(audit["selected"], 1)

    def test_listing_ignores_external_pdf_links(self):
        url = "https://www.un.org/list"
        client = FakeClient({url: b'<a href="https://example.com/unsafe.pdf">External</a><a href="/one.pdf">Official</a>'})
        rows, audit = u.discover_listing(client, {"type": "html_pdf_listing", "url": url,
            "metadata": {"language": "en", "relations": {"palestine": True}}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(audit["excluded_external_links"], 1)


if __name__ == "__main__":
    unittest.main()
