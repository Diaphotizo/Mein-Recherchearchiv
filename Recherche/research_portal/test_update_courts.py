"""Updater contracts: source boundaries, language scope and failure preservation."""
import io
import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from . import update_courts as u


class ParsingTests(unittest.TestCase):
    def test_workers_share_one_host_request_interval(self):
        clock = [0.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        limiter = u.HostLimiter(clock=lambda: clock[0], sleeper=sleep)
        one, two = u.PublicClient(limiter), u.PublicClient(limiter)
        one.opener = Mock()
        two.opener = Mock()
        one.open("https://www.icj-cij.org/case/192")
        two.open("https://www.icj-cij.org/case/193")
        one.open("https://legal-tools.org/api/ltddocs/search")
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(clock[0], 1.0)

    def test_429_stops_all_remaining_icj_cases_and_retains_retry_after(self):
        client = u.PublicClient()
        client.opener = Mock()
        url = u.ICJ_BASE + "/case/4"
        client.opener.open.side_effect = urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "120"}, None)
        rows, audits, errors, pending, excluded = u.collect_icj(client)
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["http_status"], 429)
        self.assertEqual(errors[0]["retry_after"], "120")
        self.assertTrue(errors[0]["retry_not_before"])
        self.assertEqual(rows, [])
        with self.assertRaises(u.HostPaused):
            u.PublicClient(client.limiter).open(u.ICJ_BASE + "/sites/default/files/a.pdf")

    def test_403_stops_remaining_legal_tools_queries(self):
        client = u.PublicClient()
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError(u.LT_API, 403, "Forbidden", {}, None)
        _, audits, errors, _, _ = u.collect_lt(client)
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["http_status"], 403)
        self.assertEqual(audits, [])

    def test_restored_retry_after_does_not_make_request_or_report_success(self):
        first = u.HostLimiter()
        first.pause(u.ICJ_BASE, "HTTP 429", retry_after="120", http_status=429)
        restored = u.HostLimiter()
        restored.restore_cooldowns(first.snapshot())
        client = u.PublicClient(restored)
        client.opener = Mock()
        _, audits, errors, _, _ = u.collect_icj(client)
        client.opener.open.assert_not_called()
        self.assertEqual(audits, [])
        self.assertEqual(len(errors), 1)

    def test_waiting_worker_rechecks_pause_and_errors_are_grouped(self):
        clock = [0.0]
        def pause_during_wait(seconds):
            limiter.pause(u.ICJ_BASE, "HTTP 429", retry_after="60", http_status=429)
            clock[0] += seconds
        limiter = u.HostLimiter(clock=lambda: clock[0], sleeper=pause_during_wait)
        limiter.acquire(u.ICJ_BASE)
        errors = []
        for _ in range(3):
            try:
                limiter.acquire(u.ICJ_BASE)
            except u.HostPaused as exc:
                u.record_error(errors, exc, source="ICJ")
        self.assertEqual(len(errors), 1)
        self.assertEqual(clock[0], 1.0)

    def test_icj_language_selection_and_case_pagination(self):
        body = '''<html><h1>Case title</h1><h4>Orders</h4>
        <div class="cases-document"><a class="title">Order</a><div class="date">3 October 2026</div>
        <a href="/sites/default/files/case-related/192/192-20261003-ord-01-en.pdf">English</a>
        <a href="/sites/default/files/case-related/192/192-20261003-ord-01-bi.pdf">Bilingual</a>
        <a href="/sites/default/files/case-related/192/192-20261003-ord-01-fr.pdf">French</a></div>
        <div class="cases-document"><a href="/sites/default/files/case-related/192/mystery.pdf">Original Language</a></div>
        <a href="/case/192/orders?page=1">Next orders</a>
        <div class="pager"><a href="?page=2">Next</a><a href="/case/193?page=3">Wrong case</a></div>
        <a href="/sites/default/files/case-related/193/other-en.pdf">English</a></html>'''
        rows, links, excluded, pending = u.extract_icj_page(body, u.ICJ_BASE + "/case/192", 192)
        self.assertEqual(len(rows), 2)
        selected = u.prefer_english(rows)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["language"], "en")
        self.assertEqual(selected[0]["date"], "2026-10-03")
        self.assertEqual(len(excluded), 1)
        self.assertEqual(len(pending), 1)
        self.assertIn(u.ICJ_BASE + "/case/192?page=2", links)
        self.assertFalse(any("/193" in link for link in links))

    def test_icj_no_challenge_or_arbitrary_redirect(self):
        with self.assertRaises(ValueError):
            u.extract_icj_page("<h1>Access denied</h1>", u.ICJ_BASE + "/case/192", 192)
        for url in ("http://www.icj-cij.org/case/192", "https://localhost/test.pdf", "https://www.icj-cij.org.evil.invalid/file.pdf"):
            with self.assertRaises(ValueError):
                u.safe_url(url)
        self.assertEqual(u.url_key("https://api.icj-cij.org/sites/default/files/case-related/192/a.pdf"),
                         u.url_key("https://www.icj-cij.org/sites/default/files/case-related/192/a.pdf"))

    def hit(self):
        return {"id": "abc", "externalId": "ICC-01/18-99", "title": "Test decision", "isPublished": True,
            "confidentiality": "public", "requireDownloadPermission": False, "languages": [{"name": "English"}],
            "orignalPdfURL": "https://ltd-docs.s3.nl-ams.scw.cloud/alldocs/test.pdf", "dateCreated": "0001-01-01"}

    def test_icc_public_language_and_case_checks(self):
        hit = self.hit()
        row, reason = u.lt_record(hit, ["palestine_external"])
        self.assertIsNone(reason)
        self.assertIsNone(row["date"])
        for key, value in (("confidentiality", "confidential"), ("isPublished", False),
                           ("requireDownloadPermission", True), ("languages", [{"name": "French"}]),
                           ("externalId", "ICC-01/05-01/13-1")):
            row, reason = u.lt_record({**hit, key: value}, ["palestine_external"])
            self.assertIsNone(row, (key, value))
            self.assertTrue(reason)

    def test_icc_pagination_mismatch_is_not_success(self):
        client = Mock()
        client.json.side_effect = [{"total": 101, "results": [{"hit": self.hit()}]},
                                   {"total": 101, "results": [{"hit": self.hit()}]}]
        with patch.object(u, "LT_SPECS", (u.LT_SPECS[0],)):
            rows, audits, errors, _, _ = u.collect_lt(client)
        self.assertEqual(rows, [])
        self.assertEqual(audits, [])
        self.assertEqual(len(errors), 1)

    def test_conditional_get_accepts_304_without_downloading_body(self):
        client = Mock()
        client.open.side_effect = urllib.error.HTTPError("https://legal-tools.org/a.pdf", 304, "Not Modified", {"ETag": '"same"'}, None)
        changed, result = u.check_existing_pdf(client, {"url": "https://legal-tools.org/a.pdf"}, {"checked_at": "2026-10-01", "bytes": 500}, {"etag": '"same"'})
        self.assertFalse(changed)
        self.assertEqual(result["status"], "not_modified")
        self.assertEqual(client.open.call_args.kwargs["headers"]["Range"], "bytes=0-0")
        self.assertEqual(client.open.call_args.kwargs["headers"]["If-None-Match"], '"same"')

    def test_conditional_get_new_etag_signals_changed_and_caps_body(self):
        class Response(io.BytesIO):
            status = 200
            url = "https://legal-tools.org/a.pdf"
            headers = {"ETag": '"new"', "Content-Type": "application/pdf", "Content-Length": "1000000"}
            def close(self):
                self.final_position = self.tell()
                super().close()
        response = Response(b"%PDF-1.7" + b"x" * 1000000)
        client = Mock()
        client.open.return_value = response
        changed, result = u.check_existing_pdf(client, {"url": response.url}, {"bytes": 1000000}, {"etag": '"old"'})
        self.assertTrue(changed)
        self.assertEqual(response.final_position, 8)
        self.assertEqual(result["etag"], '"new"')


class UpdatePreservationTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / "tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.root = Path(self.temp.name)
        self.source = self.root / "original"
        self.data = self.root / "ICC-ICJ" / "data"
        self.source.mkdir()
        self.data.mkdir(parents=True)
        self.original = {"coverage": {"checked_at": "2026-10-01"}, "documents": [{"court": "ICJ", "case_id": 192,
            "title": "Old PDF", "url": "https://www.icj-cij.org/sites/default/files/case-related/192/old.pdf", "sha256": "oldhash"}]}
        (self.source / "Verzeichnis.json").write_text(json.dumps(self.original), encoding="utf-8")
        (self.data / "annotations.json").write_text('{"mine":"preserve"}', encoding="utf-8")
        self.before = (self.source / "Verzeichnis.json").read_bytes()
        self.patches = [patch.object(u, "COURT_SOURCE", self.source), patch.object(u, "COURT_DATA", self.data), patch.object(u, "COURTS", self.data.parent)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.assertEqual((self.source / "Verzeichnis.json").read_bytes(), self.before)
        self.assertEqual((self.data / "annotations.json").read_text(), '{"mine":"preserve"}')
        for attempt in range(8):
            try:
                self.temp.cleanup()
                break
            except OSError:
                if attempt == 7:
                    raise
                time.sleep(0.15 * (attempt + 1))

    def test_all_sources_fail_preserves_previous_catalog_and_check_date(self):
        previous = {"documents": [], "checked_at": "2026-10-10"}
        u.atomic_json(self.data / "additions.json", previous)
        failed = ([], [], [{"error": "unreachable"}], [], [])
        rebuild = Mock()
        with patch.object(u, "collect_icj", return_value=failed), patch.object(u, "collect_lt", return_value=failed):
            state = u.run_update(client=Mock(), rebuild=rebuild)
        self.assertEqual(state["phase"], "error")
        self.assertEqual(state["checked_at"], "2026-10-10")
        self.assertEqual(u.read_json(self.data / "additions.json"), previous)
        rebuild.assert_not_called()

    def test_known_alternative_pdf_url_not_downloaded(self):
        row = {**self.original["documents"][0], "url": "https://api.icj-cij.org/sites/default/files/case-related/192/old.pdf"}
        with patch.object(u, "collect_icj", return_value=([row], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "collect_lt", return_value=([], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "check_existing_pdf", return_value=(False, {"status": "not_modified"})), \
             patch.object(u, "download_pdf") as download:
            state = u.run_update(client=Mock(), rebuild=Mock())
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["new"], 0)
        download.assert_not_called()

    def test_same_url_new_bytes_retains_original_and_counts_changed(self):
        row = self.original["documents"][0]
        saved = {**row, "file_path": str(self.root / "new-version.pdf"), "sha256": "changedhash", "pages": 1, "bytes": 200}
        with patch.object(u, "collect_icj", return_value=([row], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "collect_lt", return_value=([], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "check_existing_pdf", return_value=(True, {"etag": '"new"', "status": "changed"})), \
             patch.object(u, "download_pdf", return_value=saved):
            state = u.run_update(client=Mock(), rebuild=Mock())
        self.assertEqual(state["changed"], 1)
        self.assertEqual(state["new"], 0)
        self.assertEqual(state["total_documents"], 2)

    def test_new_register_pdf_is_saved_before_historical_host_denial(self):
        old = self.original["documents"][0]
        new = {"court": "ICJ", "case_id": 192, "title": "New public order", "date": "2026-10-13",
            "url": "https://www.icj-cij.org/sites/default/files/case-related/192/new-en.pdf"}
        saved = {**new, "file_path": str(self.root / "new.pdf"), "sha256": "newhash", "pages": 1, "bytes": 100}
        events = []
        client = u.PublicClient()
        def download(use_client, row):
            self.assertFalse(use_client.limiter.paused(row["url"]))
            events.append("download_new")
            return saved
        def deny_known(use_client, row, existing, previous):
            events.append("check_old")
            checkpoint = u.read_json(self.data / "additions.json")
            self.assertEqual(checkpoint["documents"][0]["sha256"], "newhash")
            self.assertEqual(checkpoint["checked_at"], "2026-10-01")
            raise use_client.limiter.pause(row["url"], "HTTP 403: Zugriff gesperrt", http_status=403)
        rebuild = Mock()
        with patch.object(u, "collect_icj", return_value=([old, new], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "collect_lt", return_value=([], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "download_pdf", side_effect=download), \
             patch.object(u, "check_existing_pdf", side_effect=deny_known):
            state = u.run_update(client=client, rebuild=rebuild)
        self.assertEqual(events, ["download_new", "check_old"])
        self.assertEqual(state["phase"], "partial")
        self.assertEqual(state["new"], 1)
        self.assertEqual(state["total_documents"], 2)
        self.assertTrue(client.limiter.paused(new["url"]))
        self.assertEqual(len(u.read_json(self.data / "additions.json")["documents"]), 1)
        rebuild.assert_called_once()

    def test_new_document_priority_does_not_bypass_saved_retry_after(self):
        new = {"court": "ICJ", "case_id": 192, "title": "New public order",
            "url": "https://www.icj-cij.org/sites/default/files/case-related/192/new-en.pdf"}
        limiter = u.HostLimiter()
        limiter.pause(new["url"], "HTTP 429", retry_after="120", http_status=429)
        u.atomic_json(self.data / "update_status.json", {"source_pauses": limiter.snapshot()})
        client = u.PublicClient()
        client.opener = Mock()
        with patch.object(u, "collect_icj", return_value=([new], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "collect_lt", return_value=([], [{"status": "ok"}], [], [], [])):
            state = u.run_update(client=client, rebuild=Mock())
        client.opener.open.assert_not_called()
        self.assertEqual(state["phase"], "partial")
        self.assertEqual(state["new"], 0)
        self.assertEqual(state["errors"][0]["http_status"], 429)

    def test_failed_replacement_download_does_not_commit_new_validator(self):
        row = self.original["documents"][0]
        key = u.url_key(row["url"])
        u.atomic_json(self.data / "additions.json", {"documents": [], "validators": {key: {"etag": '"old"'}}})
        with patch.object(u, "collect_icj", return_value=([row], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "collect_lt", return_value=([], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "check_existing_pdf", return_value=(True, {"etag": '"new"', "status": "changed"})), \
             patch.object(u, "download_pdf", side_effect=ValueError("incomplete PDF")):
            state = u.run_update(client=Mock(), rebuild=Mock())
        self.assertEqual(state["phase"], "partial")
        self.assertEqual(u.read_json(self.data / "additions.json")["validators"][key]["etag"], '"old"')

    def test_partial_run_preserves_last_complete_date_and_adds_valid_new_pdf(self):
        row = {"court": "ICC", "case_id": "ICC-01-18", "title": "New PDF", "url": "https://ltd-docs.s3.nl-ams.scw.cloud/alldocs/new.pdf"}
        saved = {**row, "file_path": str(self.root / "new.pdf"), "sha256": "newhash", "pages": 1, "bytes": 100}
        rebuild = Mock()
        with patch.object(u, "collect_icj", return_value=([], [], [{"error": "HTTP 403"}], [], [])), \
             patch.object(u, "collect_lt", return_value=([row], [{"status": "ok"}], [], [], [])), \
             patch.object(u, "download_pdf", return_value=saved):
            state = u.run_update(client=Mock(), rebuild=rebuild)
        self.assertEqual(state["phase"], "partial")
        self.assertEqual(state["checked_at"], "2026-10-01")
        self.assertEqual(state["new"], 1)
        self.assertEqual(len(u.read_json(self.data / "additions.json")["documents"]), 1)
        rebuild.assert_called_once()

    def test_pending_index_is_retried_without_redownload(self):
        u.atomic_json(self.data / "additions.json", {"documents": [], "index_pending": True})
        ok = ([], [{"status": "ok"}], [], [], [])
        rebuild = Mock()
        with patch.object(u, "collect_icj", return_value=ok), patch.object(u, "collect_lt", return_value=ok):
            state = u.run_update(client=Mock(), rebuild=rebuild)
        self.assertEqual(state["phase"], "complete")
        self.assertFalse(u.read_json(self.data / "additions.json")["index_pending"])
        rebuild.assert_called_once()

    def test_pdf_validation_rejects_html_and_reads_real_pdf(self):
        invalid = self.root / "invalid.pdf"
        invalid.write_bytes(b"<html>Access denied</html>")
        with self.assertRaises(ValueError):
            u.validate_pdf(invalid)
        document = u.pdfium.PdfDocument.new()
        page = document.new_page(200, 300)
        page.close()
        path = self.root / "valid.pdf"
        document.save(str(path))
        document.close()
        self.assertEqual(u.validate_pdf(path), 1)


if __name__ == "__main__":
    unittest.main()
