"""Read-only public UN PDF imports, preserving every validated local version.

Discovery is deliberately bounded by UN/data/sources.json. A successful run
confirms that configured sources were checked, never completeness of UN holdings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pypdfium2 as pdfium
from lxml import html as lhtml

from .paths import BASE
from .storage import atomic_json, read_json, require_indexed_ids

UN = BASE / "UN"
USER_AGENT = "LocalUNResearchArchive/1.0 (public documents; personal research)"
ROBOT_AGENT = "LocalUNResearchArchive"
ALLOWED_DOMAINS = frozenset({"un.org", "unmissions.org", "ohchr.org", "unrwa.org", "ochaopt.org",
    "who.int", "unicef.org", "wfp.org", "unctad.org", "ilo.org", "unesco.org",
    "undp.org", "unhcr.org", "refworld.org", "unwomen.org", "unfpa.org", "unops.org",
    "unhabitat.org", "unece.org", "unep.org", "fao.org"})
RELATIONS = ("germany", "israel", "palestine")
NOTES = [
    "Geprüft werden die ausdrücklich konfigurierten Dokumente und öffentlichen Quellenlisten. Die Sammlung ist kein vollständiger Nachweis aller UN-Dokumente.",
    "Aufnahme: englische und vorhandene deutsche Fassungen mit belegtem Deutschland-, Israel- oder zusätzlichem Palästina-/UNRWA-Bezug. Fehlende Sprachmetadaten werden nur bei eindeutigen PDF-Textmerkmalen ergänzt; unklare Fälle bleiben zur Prüfung offen. Es werden keine Übersetzungen erzeugt.",
    "Auch bekannte URLs außerhalb der aktuellen Quellenlisten werden per bedingtem GET geprüft; je URL wird nur die zuletzt bestätigte Fassung verglichen. HTTP 304 bestätigt nur die Serverauskunft; bei HTTP 200 wird die gesamte PDF-Datei validiert und per SHA-256 verglichen. Frühere Fassungen bleiben erhalten.",
    "Zugriffssperren, robots.txt und Abrufpausen werden beachtet. Ein fehlender Treffer gilt nicht als Löschung; der bisherige Suchbestand bleibt bei Abruffehlern verfügbar.",
]


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_url(url):
    if not isinstance(url, str):
        raise ValueError("Quellen-URL muss Text sein")
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not any(host == domain or host.endswith("." + domain) for domain in ALLOWED_DOMAINS)):
        raise ValueError("Keine freigegebene öffentliche UN-Quelle: " + url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def url_key(url):
    return safe_url(url)


def utc_date(value):
    if not value:
        return None
    try:
        result = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


@contextmanager
def update_lock(root=None):
    data = Path(root or UN) / "data"
    data.mkdir(parents=True, exist_ok=True)
    with (data / "update.lock").open("a+b") as handle:
        # Windows byte-range locks deny reads of the held byte as well. Size
        # inspection stays available, allowing contention to reach LK_NBLCK
        # and be reported as the expected recoverable RuntimeError.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Ein UN-Abgleich läuft bereits.") from exc
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class HostPaused(RuntimeError):
    def __init__(self, host, details):
        self.host, self.details = host, details
        super().__init__(f"{host}: {details['reason']}. Quelle für diesen Lauf ausgesetzt.")


class HostLimiter:
    def __init__(self, clock=None, sleeper=None):
        self.clock, self.sleeper = clock or time.monotonic, sleeper or time.sleep
        self.lock = threading.Lock()
        self.next_allowed, self.intervals, self.pauses = {}, {}, {}

    def acquire(self, url):
        host = urllib.parse.urlsplit(url).hostname
        while True:
            with self.lock:
                if host in self.pauses:
                    raise HostPaused(host, self.pauses[host])
                current = self.clock()
                delay = self.next_allowed.get(host, current) - current
                if delay <= 0:
                    self.next_allowed[host] = current + max(1.0, self.intervals.get(host, 1.0))
                    return
            self.sleeper(delay)

    def pause(self, url, reason, retry_after=None, http_status=None):
        host = urllib.parse.urlsplit(url).hostname
        retry = None
        if retry_after:
            retry = (datetime.fromtimestamp(time.time() + int(retry_after), timezone.utc)
                     if str(retry_after).strip().isdigit() else utc_date(retry_after))
        details = {"reason": reason, "paused_at": now(), "retry_after": retry_after,
                   "retry_not_before": retry.isoformat() if retry else None, "http_status": http_status}
        with self.lock:
            self.pauses.setdefault(host, details)
            return HostPaused(host, dict(self.pauses[host]))

    def restore_cooldowns(self, saved):
        for host, details in (saved or {}).items():
            until = utc_date(details.get("retry_not_before"))
            if until and until > datetime.now(timezone.utc):
                self.pauses[host] = dict(details)

    def snapshot(self):
        with self.lock:
            return {host: dict(details) for host, details in self.pauses.items()}


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, client):
        self.client = client
        super().__init__()

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        safe_url(newurl)
        is_robots = getattr(request, "_un_robots", False)
        self.client.authorize(newurl, robots=not is_robots)
        result = super().redirect_request(request, fp, code, msg, headers, newurl)
        if result is not None:
            result._un_robots = is_robots
        return result


class PublicClient:
    """GET only; official hosts, robots rules, host pacing and hard denial pauses."""
    def __init__(self, limiter=None):
        self.limiter = limiter or HostLimiter()
        self.opener = urllib.request.build_opener(SafeRedirect(self))
        self.robots = {}

    def _request(self, url, accept, headers=None, is_robots=False):
        request = urllib.request.Request(safe_url(url), method="GET", headers={
            "User-Agent": USER_AGENT, "Accept": accept, **(headers or {})})
        request._un_robots = is_robots
        try:
            return self.opener.open(request, timeout=45)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                paused = self.limiter.pause(exc.url or url, f"HTTP {exc.code}: Zugriff/Abruflimit",
                                            exc.headers.get("Retry-After"), exc.code)
                exc.close()
                raise paused from exc
            raise

    def _robots(self, url):
        parsed = urllib.parse.urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self.robots:
            return self.robots[origin]
        robots_url = origin + "/robots.txt"
        self.limiter.acquire(robots_url)
        parser = urllib.robotparser.RobotFileParser(robots_url)
        try:
            with self._request(robots_url, "text/plain", is_robots=True) as response:
                content = response.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError("robots.txt ist zu groß")
                if "text/html" in response.headers.get("Content-Type", "").lower() or content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    raise ValueError("robots.txt liefert HTML statt Regeln; Abruf zurückgestellt")
                parser.parse(content.decode("utf-8-sig", errors="replace").splitlines())
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 410):
                raise
            exc.close()
            parser.parse([])
            parser.allow_all = True
        interval = parser.crawl_delay(ROBOT_AGENT) or 1.0
        rate = parser.request_rate(ROBOT_AGENT)
        if rate and rate.requests:
            interval = max(interval, rate.seconds / rate.requests)
        host = parsed.hostname
        self.limiter.intervals[host] = max(1.0, float(interval))
        # Honour crawl-delay after the robots request too.
        self.limiter.next_allowed[host] = max(self.limiter.next_allowed.get(host, 0),
                                             self.limiter.clock() + max(1.0, float(interval)))
        self.robots[origin] = parser
        return parser

    def authorize(self, url, robots=True):
        safe_url(url)
        if robots and not self._robots(url).can_fetch(ROBOT_AGENT, url):
            raise ValueError("robots.txt erlaubt diesen Dokumentabruf nicht: " + url)
        self.limiter.acquire(url)

    def open(self, url, accept="*/*", headers=None):
        self.authorize(url)
        return self._request(url, accept, headers)

    def text(self, url):
        with self.open(url, "text/html") as response:
            content = response.read(10 * 1024 * 1024 + 1)
            if len(content) > 10 * 1024 * 1024:
                raise ValueError("Quellenliste größer als 10 MiB")
            return content.decode("utf-8-sig", errors="replace"), safe_url(response.url)

    def fetch_bytes(self, url):
        with self.open(url) as response:
            content = response.read(20 * 1024 * 1024 + 1)
            if len(content) > 20 * 1024 * 1024:
                raise ValueError("Quellenantwort größer als 20 MiB")
            return content


def text_list(values):
    if isinstance(values, str):
        values = [values]
    return list(dict.fromkeys(str(value).strip() for value in (values or []) if str(value).strip()))


def normalized_language(value):
    language = str(value or "").strip().lower()
    return {"english": "en", "eng": "en", "german": "de", "deutsch": "de", "deu": "de", "ger": "de"}.get(language, language)


def normalize_manual_review(review, language):
    """A visual language decision applies only to the reviewed PDF bytes."""
    if not isinstance(review, dict) or review.get("status") not in ("language_verified", "language_verified_scan"):
        raise ValueError("Ungültige manuelle Sprachprüfung: bestätigter Prüfstatus fehlt")
    sha = str(review.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("Ungültige manuelle Sprachprüfung: konkrete SHA-256 fehlt")
    reviewed_language = normalized_language(review.get("language") or language)
    if reviewed_language not in ("en", "de"):
        raise ValueError("Ungültige manuelle Sprachprüfung: geprüfte Sprache fehlt")
    return {**review, "sha256": sha, "language": reviewed_language}


def normalize_record(raw):
    language = normalized_language(raw.get("language"))
    if language in ("und", "unknown"):
        language = ""
    if language not in ("en", "de", ""):
        raise ValueError("Nur ausdrücklich englische/deutsche Fassungen werden aufgenommen")
    relations = {key: raw.get("relations", {}).get(key) is True for key in RELATIONS}
    if not any(relations.values()):
        raise ValueError("Kein belegter Deutschland-, Israel- oder Palästina-Themenbezug")
    pdf_url = safe_url(raw.get("pdf_url") or raw.get("url"))
    source_url = safe_url(raw.get("url") or pdf_url)
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("Dokumenttitel fehlt")
    doc_date = str(raw.get("date") or "")
    if doc_date:
        date.fromisoformat(doc_date)
    result = {"title": title, "date": doc_date, "symbol": str(raw.get("symbol") or ""),
        "body": str(raw.get("body") or ""), "author": str(raw.get("author") or ""),
        "kind": str(raw.get("kind") or "Dokument"), "language": language,
        "url": source_url, "pdf_url": pdf_url, "relations": relations,
        "relation_reasons": {key: text_list(raw.get("relation_reasons", {}).get(key)) for key in RELATIONS},
        "tags": text_list(raw.get("tags")),
        "metadata_note": str(raw.get("metadata_note") or "Sprach- und Themenzuordnung gemäß Quellenmanifest")}
    if raw.get("manual_review") is not None:
        result["manual_review"] = normalize_manual_review(raw["manual_review"], language)
    return result


def merge_records(existing, incoming):
    """SHA dedup keeps existing notes and merges provenance and scope evidence."""
    result = {**existing}
    for key in ("title", "date", "symbol", "body", "author", "kind", "language", "url", "pdf_url", "metadata_note"):
        if not result.get(key) and incoming.get(key):
            result[key] = incoming[key]
    if incoming.get("manual_review"):
        review = incoming["manual_review"]
        if not result.get("manual_review") or review.get("sha256") == result.get("sha256"):
            result["manual_review"] = review
    result["relations"] = {key: bool(existing.get("relations", {}).get(key) or incoming.get("relations", {}).get(key)) for key in RELATIONS}
    result["relation_reasons"] = {key: text_list(text_list(existing.get("relation_reasons", {}).get(key)) + text_list(incoming.get("relation_reasons", {}).get(key))) for key in RELATIONS}
    for field, singular in (("pdf_urls", "pdf_url"), ("source_urls", "url"), ("titles", "title"), ("languages", "language"), ("symbols", "symbol")):
        result[field] = text_list(text_list(existing.get(field)) + [existing.get(singular) or ""] + text_list(incoming.get(field)) + [incoming.get(singular) or ""])
    result["tags"] = text_list(text_list(existing.get("tags")) + text_list(incoming.get("tags")))
    return result


def discover_listing(client, descriptor):
    if descriptor.get("type") != "html_pdf_listing":
        raise ValueError("Unbekannter UN-Quellentyp")
    url = safe_url(descriptor.get("url"))
    metadata = descriptor.get("metadata", {})
    content, final = client.text(url)
    tree = lhtml.fromstring(content)
    rows, seen, external = [], set(), 0
    limit = min(2000, max(1, int(descriptor.get("max_documents", 500))))
    for anchor in tree.xpath("//a[@href]"):
        target = urllib.parse.urljoin(final, anchor.get("href"))
        if not re.search(r"\.pdf(?:$|[?#])", target, re.IGNORECASE):
            continue
        try:
            target = safe_url(target)
        except ValueError:
            external += 1
            continue
        if target in seen:
            continue
        seen.add(target)
        title = " ".join(anchor.text_content().split()) or urllib.parse.unquote(urllib.parse.urlsplit(target).path.rsplit("/", 1)[-1])
        rows.append(normalize_record({**metadata, "title": title, "url": url, "pdf_url": target,
            "metadata_note": "Titel aus Linktext; Sprache und Themenbezug aus ausdrücklich konfigurierter Quellenliste"}))
    return rows[:limit], {"url": url, "final_url": final, "discovered": len(rows), "selected": min(len(rows), limit),
        "limited": len(rows) > limit, "excluded_external_links": external,
        "coverage": "Nur PDF-Links dieser konfigurierten Listenseite; keine automatische Vollständigkeitsannahme"}


def validate_pdf(path):
    with Path(path).open("rb") as handle:
        if not handle.read(1024).lstrip().startswith(b"%PDF-"):
            raise ValueError("Quelle liefert keinen PDF-Kopf")
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = len(pdf)
        if pages < 1:
            raise ValueError("PDF enthält keine Seiten")
        for number in range(pages):
            page = pdf[number]
            try:
                if min(page.get_size()) <= 0:
                    raise ValueError(f"Ungültige PDF-Seite {number + 1}")
            finally:
                page.close()
        return pages
    finally:
        pdf.close()


def detect_language(text):
    """Conservative evidence for sources that do not provide language metadata."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    english = sum(words.count(word) for word in "the and of to that for with we you have government rights this your would on has which are".split())
    german = sum(words.count(word) for word in "die der und das den von zu des für mit wir sie haben regierung menschenrechte dieser ihre wurden ist sind".split())
    en_mark = bool(re.search(r"(?im)^\s*English\s*$|\bOriginal\s*:\s*English\b|\bDear\s+(?:Mr|Ms|Madam|Sir|Excellency)\b|\bExcellency,", text))
    de_mark = bool(re.search(r"(?im)^\s*(?:Deutsch|German)\s*$|\bOriginal\s*:\s*(?:German|Deutsch)\b|\bSehr\s+geehrte", text))
    if english >= 15 and english > max(1, german) * 3 and (en_mark or english >= 35):
        return "en", {"method": "PDF-Text: englische Sprach-/Briefmerkmale und Funktionswörter", "english_score": english, "german_score": german}
    if german >= 15 and german > max(1, english) * 3 and (de_mark or german >= 35):
        return "de", {"method": "PDF-Text: deutsche Sprach-/Briefmerkmale und Funktionswörter", "english_score": english, "german_score": german}
    raise ValueError("PDF-Sprache nicht eindeutig als Englisch/Deutsch belegt; zur Prüfung zurückgestellt")


def pdf_language(path, row, sha256=None):
    review = None
    if row.get("manual_review") is not None:
        review = normalize_manual_review(row["manual_review"], row.get("language"))
        if sha256 is None:
            with Path(path).open("rb") as handle:
                sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
        if review["sha256"] == sha256:
            return review["language"], {"method": "Manuelle Sprachprüfung der exakt übereinstimmenden SHA-256-Fassung",
                "sha256": sha256, "review_status": review["status"], "review_method": review.get("method", "")}
    # An explicit seed language from a manual review is not source metadata for
    # future revisions. Changed bytes must pass independent content validation.
    if not review and row.get("language") in ("en", "de"):
        return row["language"], {"method": "Ausdrückliche Sprachkennzeichnung der konfigurierten Quelle"}
    document = pdfium.PdfDocument(str(path))
    texts = []
    try:
        for number in range(min(3, len(document))):
            page = document[number]
            try:
                textpage = page.get_textpage()
                try:
                    texts.append(textpage.get_text_range())
                finally:
                    textpage.close()
            finally:
                page.close()
    finally:
        document.close()
    try:
        language, validation = detect_language("\n".join(texts))
    except ValueError as exc:
        if review:
            raise ValueError("PDF-SHA-256 geändert; die manuelle Sprachprüfung gilt nur für " + review["sha256"]
                + ". Neue Fassung " + str(sha256) + " sprachlich nicht eindeutig: erneute Prüfung erforderlich.") from exc
        raise
    if review:
        validation.update(sha256=sha256, previous_review_sha256=review["sha256"],
            note="PDF-SHA-256 geändert; frühere manuelle Freigabe nicht angewendet. Neue Fassung anhand ihres PDF-Texts geprüft.")
    return language, validation


def existing_file(root, record):
    path = Path(record.get("file_path", "")).resolve()
    try:
        path.relative_to((Path(root) / "PDF").resolve())
    except ValueError:
        return False
    return path.is_file()


def download_pdf(client, row, root, previous_validator=None, known=None):
    headers = {}
    if known and existing_file(root, known):
        for key, name in (("etag", "If-None-Match"), ("last_modified", "If-Modified-Since")):
            if (previous_validator or {}).get(key):
                headers[name] = previous_validator[key]
    try:
        response = client.open(row["pdf_url"], "application/pdf", headers=headers)
    except urllib.error.HTTPError as exc:
        if exc.code != 304 or not headers or not known or not existing_file(root, known):
            raise
        validator = {**(previous_validator or {}), "checked_at": now(), "http_status": 304,
                     "validation": "server_not_modified", "sha256": known["sha256"]}
        exc.close()
        return None, validator
    root = Path(root)
    temporary = root / "data" / "downloads" / (uuid.uuid4().hex + ".part")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    try:
        with response, temporary.open("xb") as handle:
            if getattr(response, "status", 200) != 200:
                raise ValueError("Kein vollständiger PDF-Abruf (HTTP " + str(response.status) + ")")
            final_url = safe_url(response.url)
            validator = {"etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified"),
                         "final_url": final_url, "checked_at": now(), "http_status": 200, "validation": "full_download_sha256"}
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > 512 * 1024 * 1024:
                    raise ValueError("PDF größer als 512 MiB; manuelle Prüfung erforderlich")
                if size == len(block) and not block[:1024].lstrip().startswith(b"%PDF-"):
                    raise ValueError("Antwort enthält keine PDF-Datei")
                digest.update(block)
                handle.write(block)
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) != size:
                raise ValueError("PDF-Abruf unvollständig: Dateigröße stimmt nicht")
        pages = validate_pdf(temporary)
        sha = digest.hexdigest()
        language, language_validation = pdf_language(temporary, row, sha256=sha)
        destination = root / "PDF" / (sha + ".pdf")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            with destination.open("rb") as handle:
                old_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            if old_hash != sha:
                raise ValueError("Bestehende Zieldatei hat abweichenden Inhalt; nicht überschrieben")
        else:
            temporary.replace(destination)
        validator.update(sha256=sha, bytes=size)
        record = {**row, "id": sha, "sha256": sha, "file_path": str(destination.resolve()),
            "pages": pages, "bytes": size, "final_url": final_url, "downloaded_at": now(),
            "language": language, "languages": [language], "language_validation": language_validation}
        # Do not add a superseded manual seed language to the new PDF's list.
        return merge_records(record, {**row, "language": language, "languages": [language]}), validator
    finally:
        temporary.unlink(missing_ok=True)


def record_error(errors, exc, **context):
    if isinstance(exc, HostPaused):
        if any(row.get("paused_host") == exc.host for row in errors):
            return
        context.update(paused_host=exc.host, **exc.details)
    errors.append({**context, "error": str(exc)})


def resolve_source_warnings(errors, resolutions, successful_urls):
    """Resolve only missing UNISPAL attachments backed by this run's PDF checks.

    A saved catalog entry or an alternate URL in the manifest is not enough.
    Callers must pass only URLs whose validated GET or conditional 304 succeeded
    in the current run. Page/list retrieval errors remain visible.
    """
    successful_urls = set(successful_urls)
    mappings = {}
    for raw in resolutions if isinstance(resolutions, list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            source_url = safe_url(raw.get("source_url"))
            pdf_url = safe_url(raw.get("pdf_url"))
            evidence_url = safe_url(raw.get("evidence_url"))
        except (TypeError, ValueError):
            continue
        note = str(raw.get("note") or "").strip()
        if pdf_url not in successful_urls or not note:
            continue
        mappings[source_url] = {"source_url": source_url, "pdf_url": pdf_url,
                                "evidence_url": evidence_url, "note": note}
    remaining, resolved = [], {}
    for error in errors:
        mapping = mappings.get(error.get("url")) if error.get("source") == "UNISPAL-Anlage" else None
        if mapping:
            resolved[mapping["source_url"]] = mapping
        else:
            remaining.append(error)
    return remaining, list(resolved.values())


def resolve_manual_source_warnings(errors, manual_acquisitions, documents, root):
    """Distinguish byte-verified local copies from successful online checks.

    Only a missing UNISPAL attachment can be resolved locally. Actual fetch
    failures remain errors, even when the corresponding manually imported PDF
    is available. This helper neither mutates inputs nor performs network IO.
    """
    root = Path(root).resolve()
    pdf_root = (root / "PDF").resolve()
    try:
        pdf_root.relative_to(root)
    except ValueError:
        return list(errors), []
    records = documents.values() if isinstance(documents, dict) else documents
    by_sha = {record.get("sha256"): record for record in records if isinstance(record, dict)}
    mappings, available = {}, {}
    for raw in manual_acquisitions if isinstance(manual_acquisitions, list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            source_url = safe_url(raw.get("source_url"))
            pdf_url = safe_url(raw.get("pdf_url"))
            sha = str(raw.get("sha256") or "").lower()
            note = str(raw.get("note") or "").strip()
            record = by_sha.get(sha, {})
            proof = record.get("import_provenance") or {}
            if (not re.fullmatch(r"[0-9a-f]{64}", sha) or not note
                    or not isinstance(proof, dict) or proof.get("method") != "user_download"
                    or proof.get("sha256") != sha or not proof.get("reviewed_at")):
                continue
            if source_url not in text_list(record.get("source_urls")) + [record.get("url")]:
                continue
            if pdf_url not in text_list(record.get("pdf_urls")) + [record.get("pdf_url")]:
                continue
            path = Path(record.get("file_path") or "").resolve()
            path.relative_to(pdf_root)
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            with path.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != sha:
                    continue
        except (OSError, TypeError, ValueError):
            continue
        mapping = {"source_url": source_url, "pdf_url": pdf_url, "sha256": sha,
                   "note": note, "method": "manual_local"}
        mappings[source_url] = mapping
        available[source_url] = available[pdf_url] = mapping
    remaining, resolved = [], {}
    prefix = "Manuell geprüfte PDF lokal vorhanden; Online-Abgleich: "
    for error in errors:
        mapping = mappings.get(error.get("url")) if error.get("source") == "UNISPAL-Anlage" else None
        if mapping:
            resolved[mapping["source_url"]] = mapping
            continue
        mapping = available.get(error.get("url"))
        if mapping:
            message = str(error.get("error") or "")
            error = {**error, "local_copy_available": True, "local_copy_sha256": mapping["sha256"],
                     "error": message if message.startswith(prefix) else prefix + message}
        remaining.append(error)
    return remaining, list(resolved.values())


def run_update(client=None, rebuild=None, root=None):
    root = Path(root or UN)
    client = client or PublicClient()
    if rebuild is None:
        from .index_un import build_index
        rebuild = lambda: build_index(root=root)
    with update_lock(root):
        return _run_update(client, rebuild, root)


def _run_update(client, rebuild, root):
    data = root / "data"
    previous = read_json(data / "catalog.json", {"documents": []})
    manifest = read_json(data / "sources.json", {})
    state = {"phase": "running", "started_at": now(), "progress": 0, "new": 0, "changed": 0,
             "duplicates": 0, "errors": [], "total_documents": len(previous.get("documents", [])),
             "coverage_notes": NOTES, "checked_at": previous.get("checked_at"), "processed": 0}
    limiter = getattr(client, "limiter", None)
    if limiter:
        limiter.restore_cooldowns(read_json(data / "update_status.json", {}).get("source_pauses"))

    def status(message, progress=None):
        state["message"] = message
        if progress is not None:
            state["progress"] = progress
        if limiter:
            state["source_pauses"] = limiter.snapshot()
        atomic_json(data / "update_status.json", state)
        print(message, flush=True)

    try:
        status("Konfigurierte UN-Quellen prüfen …", 1)
        if not previous.get("documents"):
            require_indexed_ids(data / "un_index.sqlite3", [])
            if any((root / "PDF").rglob("*.pdf")):
                raise RuntimeError("PDF-Bestand ohne UN-Quellenkatalog; Abgleich abgebrochen, Dateien bleiben erhalten.")
        rows, audits = [], []
        raw_documents = list(manifest.get("documents", []))
        discovery_notes = []
        if manifest.get("discovery"):
            try:
                from .un_discovery import discover
                discovery = discover(client.fetch_bytes, manifest)
                raw_documents.extend(discovery.get("documents", []))
                discovery_notes.extend(discovery.get("coverage_notes", []))
                state["errors"].extend(discovery.get("errors", []))
            except Exception as exc:
                record_error(state["errors"], exc, source="Quellensuche")
        for raw in raw_documents:
            try:
                rows.append(normalize_record(raw))
            except Exception as exc:
                record_error(state["errors"], exc, source="Quellenmanifest", url=raw.get("pdf_url") or raw.get("url"))
        for descriptor in manifest.get("sources", []):
            try:
                discovered, audit = discover_listing(client, descriptor)
                rows.extend(discovered)
                audits.append(audit)
                if audit["limited"]:
                    state["errors"].append({"source": "Quellenliste", "url": audit["url"], "error": "Konfiguriertes Abruflimit erreicht; weitere Treffer bleiben offen"})
            except Exception as exc:
                record_error(state["errors"], exc, source="Quellenliste", url=descriptor.get("url"))
        candidates = {}
        for row in rows:
            key = url_key(row["pdf_url"])
            candidates[key] = merge_records(candidates[key], row) if key in candidates else merge_records(row, row)
        documents = {row["sha256"]: dict(row) for row in previous.get("documents", [])}
        validators = dict(previous.get("validators", {}))
        known_urls = {}
        for record in sorted(documents.values(), key=lambda value: value.get('downloaded_at') or ''):
            for pdf_url in text_list(record.get("pdf_urls")) + [record.get("pdf_url")]:
                if pdf_url:
                    known_urls[url_key(pdf_url)] = record
        # Validators identify the latest version when an URL has several hashes.
        for key, validator in validators.items():
            if validator.get("sha256") in documents:
                known_urls[key] = documents[validator["sha256"]]
        # Recent source lists are windows, not deletion notices. Retain rechecks
        # for earlier PDFs, once per URL and against its latest validated SHA.
        # Fresh discovery has priority over saved descriptive metadata.
        for key, known in known_urls.items():
            if key in candidates:
                continue
            primary_language = known.get("language") or ""
            if primary_language not in ("en", "de"):
                declared = text_list(known.get("languages"))
                primary_language = next((value for value in declared if value in ("en", "de")), "")
                if not primary_language and known.get("language") in ("en-de", "de-en"):
                    primary_language = "en"
            try:
                candidates[key] = normalize_record({**known, "pdf_url": key, "language": primary_language})
            except Exception as exc:
                record_error(state["errors"], exc, source="Bekannte PDF-Quelle", url=key)
        if not candidates:
            raise ValueError("Keine freigegebenen UN-Dokumente gefunden; bisheriger Bestand bleibt verfügbar")
        successes = 0
        successful_urls = set()
        state["total"] = len(candidates)
        for number, (key, row) in enumerate(sorted(candidates.items(), key=lambda item: item[1].get("date") or "", reverse=True), 1):
            status(f"UN-Dokument {number}/{len(candidates)}: {row['title']}", 5 + round(85 * (number - 1) / len(candidates)))
            known = known_urls.get(key)
            try:
                if known and known.get("manual_review") and not row.get("manual_review"):
                    # Fresh discovery may omit a historic manual proof. Keep
                    # its byte-bound gate when the known URL is checked again.
                    row = {**row, "manual_review": known["manual_review"]}
                downloaded, validator = download_pdf(client, row, root, validators.get(key), known)
                if downloaded is None:
                    documents[known["sha256"]] = merge_records(documents[known["sha256"]], row)
                elif downloaded["sha256"] in documents:
                    documents[downloaded["sha256"]] = merge_records(documents[downloaded["sha256"]], downloaded)
                    state["duplicates"] += 1
                else:
                    if known:
                        downloaded["previous_version_id"] = known["id"]
                        state["changed"] += 1
                    else:
                        state["new"] += 1
                    documents[downloaded["sha256"]] = downloaded
                validators[key] = validator
                known_urls[key] = documents[validator["sha256"]]
                successes += 1
                successful_urls.add(key)
            except Exception as exc:
                record_error(state["errors"], exc, source="PDF", url=row["pdf_url"], title=row["title"])
            state["processed"] = number
        state["successful_checks"] = successes
        if not successes:
            raise ValueError("Kein PDF-Abruf erfolgreich; vorhandener Katalog und Suchindex bleiben unverändert")
        state["errors"], resolved_sources = resolve_source_warnings(
            state["errors"], manifest.get("source_resolutions", []), successful_urls)
        state["errors"], manual_sources = resolve_manual_source_warnings(
            state["errors"], manifest.get("manual_acquisitions", []), documents, root)
        state["resolved_sources"] = resolved_sources + manual_sources
        discovery_notes.extend(
            f"Quellenlücke durch geprüfte Original-PDF geschlossen: {item['note']} "
            f"Quelle: {item['source_url']} · PDF: {item['pdf_url']} · Nachweis: {item['evidence_url']}"
            for item in resolved_sources)
        discovery_notes.extend(
            f"PDF manuell übernommen und lokal geprüft; kein Nachweis eines erfolgreichen Online-Abgleichs: "
            f"{item['note']} Quelle: {item['source_url']} · PDF: {item['pdf_url']}"
            for item in manual_sources)
        current_documents = list(documents.values())
        changed = current_documents != previous.get("documents", [])
        phase = "partial" if state["errors"] else "complete"
        current = {**previous, "documents": current_documents, "validators": validators,
            "checked_at": now() if phase == "complete" else previous.get("checked_at"), "last_attempt_at": now(),
            "coverage_notes": text_list(NOTES + text_list(manifest.get("coverage_notes")) + discovery_notes), "registers": audits,
            "errors": state["errors"], "index_pending": bool(changed or previous.get("index_pending") or not (data / "un_index.sqlite3").is_file())}
        atomic_json(data / "catalog.json", current)
        if current["index_pending"]:
            status("Validierte UN-PDFs übernommen. Volltextsuche wird aktualisiert …", 94)
            try:
                rebuild()
                current["index_pending"] = False
                atomic_json(data / "catalog.json", current)
            except Exception as exc:
                record_error(state["errors"], exc, source="Suchindex")
                phase = "partial"
                current["errors"] = state["errors"]
                atomic_json(data / "catalog.json", current)
        state.update(phase=phase, finished_at=now(), checked_at=current["checked_at"],
                     total_documents=len(current_documents), progress=100)
        message = f"{state['new']} neue und {state['changed']} weitere Fassungen; {len(current_documents)} UN-PDFs im Archiv."
        message += (f" Teilabgleich: {len(state['errors'])} Quellen-/Indexhinweise." if phase == "partial"
                    else " Konfigurierte Quellen geprüft.")
        status(message)
    except Exception as exc:
        record_error(state["errors"], exc, source="Abgleich")
        state.update(phase="error", finished_at=now(), progress=100)
        status("UN-Abgleich fehlgeschlagen; bisheriger Suchbestand bleibt verfügbar: " + str(exc))
    atomic_json(data / "last_attempt.json", state)
    atomic_json(data / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".json"), state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = run_update()
        return 0 if result["phase"] == "complete" else 2 if result["phase"] == "partial" else 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
