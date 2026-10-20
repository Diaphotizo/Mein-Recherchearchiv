"""Incremental, public-GET-only updater for the separate ICC/ICJ archive.

The original collection is read-only. All downloads and run reports are stored
under ICC-ICJ. Existing versions are never removed or overwritten.
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
import uuid
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path

from lxml import html as lhtml
import pypdfium2 as pdfium

from .paths import COURTS, COURT_DATA, COURT_SOURCE
from .storage import read_json as robust_read_json, atomic_json as robust_atomic_json, require_indexed_ids

ICJ_BASE = "https://www.icj-cij.org"
LT_API = "https://legal-tools.org/api/ltddocs/search"
ICJ_CASES = (4, 35, 36, 37, 49, 77, 131, 176, 186, 192, 193, 196)
ALLOWED_HOSTS = frozenset({"www.icj-cij.org", "icj-cij.org", "api.icj-cij.org",
    "icj-web.leman.un-icc.cloud", "legal-tools.org", "www.legal-tools.org",
    "ltd-docs.s3.nl-ams.scw.cloud", "www.icc-cpi.int", "icc-cpi.int"})
NOTES = [
    "Die 12 bekannten ICJ-Verfahren und die öffentlichen englischen Legal-Tools-Register für ICC-01/18 und ICC-01/13 werden geprüft; keine automatische Erweiterung auf neue Verfahren.",
    "Das ICC-Originalregister war bei der ursprünglichen Erfassung durch eine Zugriffssperre nicht auslesbar. Legal Tools ist eine öffentliche Sekundärdatenbank; der Abgleich bestätigt keine vollständige Abdeckung aller ICC-Originale.",
    "Auch bekannte PDF-URLs werden mit bedingten GETs und ETag, Last-Modified sowie Dateigröße geprüft. Bei einem Änderungsmerkmal wird die neue Datei heruntergeladen und per SHA-256 verglichen. Die erste Prüfung ohne frühere Validatoren kann gleich große, undatierte Ersetzungen nicht sicher erkennen; Server ohne verlässliche Validatoren bleiben eine technische Grenze.",
    "Bestehende Dokumente bleiben erhalten. Ein fehlender Registertreffer wird nicht als Löschung gewertet. Unklare Sprachzuordnungen werden zur Prüfung zurückgestellt.",
]
LT_SPECS = (
    ("palestine_external", {"languages.name": ["English"]}, {"externalId.search": "ICC-01/18"}),
    ("comoros_external", {"languages.name": ["English"]}, {"externalId.search": "ICC-01/13"}),
    ("palestine_situation", {"_decision.situationNameId": ["620cc7ed6a1f575abfb4fa0f"], "languages.name": ["English"]}, None),
    ("comoros_situation", {"_decision.situationNameId": ["620cc0c86a1f575abfb4f99a"], "languages.name": ["English"]}, None),
    ("comoros_case", {"caseNumber": ["ICC-01/13"], "languages.name": ["English"]}, None),
)
MONTHS = {name: i + 1 for i, name in enumerate("January February March April May June July August September October November December".split())}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    return robust_read_json(path,default)


def atomic_json(path, value):
    return robust_atomic_json(path,value)


@contextmanager
def update_lock():
    COURT_DATA.mkdir(parents=True, exist_ok=True)
    with (COURT_DATA / "update.lock").open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Ein ICC/ICJ-Abgleich läuft bereits.") from exc
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


def safe_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Nicht freigegebene öffentliche Quellen-URL: " + str(url))
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, limiter):
        super().__init__()
        self.limiter = limiter

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        safe_url(newurl)
        self.limiter.acquire(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class HostPaused(RuntimeError):
    def __init__(self, host, details):
        self.host = host
        self.details = details
        until = details.get("retry_not_before")
        super().__init__(f"{host}: {details['reason']}. Host für diesen Lauf ausgesetzt." + (f" Frühester erneuter Abruf: {until}." if until else ""))


class HostLimiter:
    """Shared by all workers: one request per host/second; never retry a denial."""
    def __init__(self, interval=1.0, clock=None, sleeper=None):
        self.interval = max(1.0, interval)
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self.lock = threading.Lock()
        self.next_allowed = {}
        self.pauses = {}

    def acquire(self, url):
        host = urllib.parse.urlsplit(url).hostname
        while True:
            with self.lock:
                if host in self.pauses:
                    raise HostPaused(host, self.pauses[host])
                current = self.clock()
                delay = self.next_allowed.get(host, current) - current
                if delay <= 0:
                    self.next_allowed[host] = current + self.interval
                    return
            # Recheck the host pause after waiting; another worker may see 429.
            self.sleeper(delay)

    def pause(self, url, reason, retry_after=None, http_status=None):
        host = urllib.parse.urlsplit(url).hostname
        retry_time = None
        if retry_after:
            if str(retry_after).strip().isdigit():
                retry_time = datetime.fromtimestamp(time.time() + int(retry_after), timezone.utc)
            else:
                retry_time = http_date(retry_after)
        with self.lock:
            if host not in self.pauses:
                self.pauses[host] = {"reason": reason, "http_status": http_status, "paused_at": now(),
                    "retry_after": retry_after, "retry_not_before": retry_time.isoformat() if retry_time else None}
            return HostPaused(host, dict(self.pauses[host]))

    def restore_cooldowns(self, saved):
        current = datetime.now(timezone.utc)
        with self.lock:
            for host, details in (saved or {}).items():
                until = http_date(details.get("retry_not_before"))
                if until and until > current:
                    self.pauses[host] = dict(details)

    def paused(self, url):
        with self.lock:
            return urllib.parse.urlsplit(url).hostname in self.pauses

    def snapshot(self):
        with self.lock:
            return {host: dict(value) for host, value in self.pauses.items()}


def record_error(errors, exc, **context):
    """One actionable error for an entire paused host, not thousands of copies."""
    if isinstance(exc, HostPaused):
        if any(item.get("paused_host") == exc.host for item in errors):
            return None
        context.update(paused_host=exc.host, **exc.details)
    problem = {**context, "error": str(exc)}
    errors.append(problem)
    return problem


class PublicClient:
    """No authentication, no browser challenge handling, no blocked-host fallback."""
    def __init__(self, limiter=None):
        self.limiter = limiter or HostLimiter()
        self.opener = urllib.request.build_opener(SafeRedirect(self.limiter))

    def open(self, url, accept="*/*", headers=None):
        safe_url(url)
        self.limiter.acquire(url)
        request = urllib.request.Request(url, headers={
            "User-Agent": "ResearchArchive/1.0 (personal public legal-document archive)",
            "Accept": accept,
            **(headers or {}),
        }, method="GET")
        try:
            response = self.opener.open(request, timeout=40)
            if accept == "application/pdf" and "text/html" in response.headers.get("Content-Type", ""):
                final_url = response.url
                response.close()
                raise self.limiter.pause(final_url, "HTML-/Zugriffssperrseite statt PDF-Datei")
            return response
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                host_error = self.limiter.pause(exc.url or url, f"HTTP {exc.code}: Zugriff gesperrt" if exc.code != 429 else "HTTP 429: Abruflimit erreicht", exc.headers.get("Retry-After"), exc.code)
                exc.close()
                raise host_error from exc
            raise

    def text(self, url):
        with self.open(url, "text/html,application/json") as response:
            body = response.read(20 * 1024 * 1024 + 1)
            if len(body) > 20 * 1024 * 1024:
                raise ValueError("Registerantwort größer als 20 MiB")
            return body.decode("utf-8-sig"), response.url

    def json(self, url):
        body, _ = self.text(url)
        if body.lstrip().startswith("<") and re.search(r"access denied|just a moment|gcore|captcha|too many requests", body, re.I):
            raise self.limiter.pause(url, "Zugriffssperrseite statt öffentlicher JSON-Antwort")
        return json.loads(body)


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def text(node):
    return clean(" ".join(node.itertext())) if node is not None else ""


def first(items):
    return items[0] if items else None


def cls(node, name):
    return node.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," ' + name + ' ")]')


def ancestor(node, name):
    return next((el for el in node.iterancestors() if name in el.get("class", "").split()), None)


def parse_date(label, filename=""):
    hit = re.search(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})\b", label or "")
    if hit:
        parts = (int(hit[3]), MONTHS[hit[2]], int(hit[1]))
    else:
        hit = re.search(r"-(\d{4})(\d{2})(\d{2})-", filename)
        if not hit:
            return None
        parts = tuple(map(int, hit.groups()))
    try:
        value = datetime(*parts)
        return value.date().isoformat() if value.year >= 1900 else None
    except ValueError:
        return None


def url_key(url):
    if not url:
        return ""
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ""
    if host in {"icj-cij.org", "www.icj-cij.org", "api.icj-cij.org", "icj-web.leman.un-icc.cloud"}:
        host = "icj-cij.org"
    if host == "www.legal-tools.org":
        host = "legal-tools.org"
    return urllib.parse.urlunsplit(("https", host, p.path, p.query, ""))


def known_pdf_urls(doc):
    values = [doc.get(k) for k in ("url", "pdf_url", "direct_pdf_url", "final_url", "icc_original_pdf_url")]
    values += doc.get("alternate_urls", []) + doc.get("alternative_urls", [])
    return {url_key(v) for v in values if isinstance(v, str) and v.startswith("http")}


def reviewed_exclusions():
    """Carry forward earlier manual PDF-language/identity findings by exact URL."""
    manifest = read_json(COURT_SOURCE / "Quellenpruefung" / "lt_manifest.json", {})
    excluded = {}
    for row in manifest.get("records", []):
        if row.get("recommended_download") is False:
            for url in known_pdf_urls(row):
                excluded[url] = row.get("exclusion_reason") or row.get("metadata_warning") or "In der ursprünglichen Quellenprüfung als Fehlzuordnung/Dublette ausgeschlossen"
    return excluded


def extract_icj_page(html, source_url, case_id):
    soup = lhtml.fromstring(html)
    title = text(first(soup.xpath("//h1")))
    links, rows, excluded, pending = [], [], [], []
    has_documents = bool(cls(soup, "cases-document"))
    for link in soup.xpath("//a[@href]"):
        url = urllib.parse.urldefrag(urllib.parse.urljoin(source_url, link.get("href")))[0]
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname in {"www.icj-cij.org", "icj-cij.org"} and re.match(rf"^/case/{case_id}(?:/|$)", parsed.path):
            if not re.search(r"/press-releases/?$", parsed.path):
                links.append(url)
        if not parsed.path.lower().endswith(".pdf") or f"/case-related/{case_id}/" not in parsed.path:
            continue
        safe_url(url)
        filename = parsed.path.rsplit("/", 1)[-1]
        label = text(link)
        doc = ancestor(link, "cases-document")
        sub = ancestor(link, "subdocument")
        local = sub if sub is not None else doc
        doc_title = text(first(cls(doc, "title"))) if doc is not None else ""
        subtitle = text(first(sub.xpath(".//h6 | .//h5 | ./a"))) if sub is not None else ""
        doc_title = subtitle or doc_title or (label if label not in ("English", "French", "Bilingual", "Original Language", "Translation") else filename)
        code = re.search(r"-\d{8}-([a-z]+)[_-]", filename, re.I)
        kind = code[1].lower() if code else ""
        section = text(first(doc.xpath("preceding::h4[1]"))) if doc is not None else ""
        if kind == "pre" or doc_title.lower().startswith("press release") or section.lower() == "press releases":
            excluded.append({"url": url, "reason": "Pressemitteilung"})
            continue
        if label == "French" or re.search(r"-frc?(?:-\d+)?\.pdf$", filename, re.I):
            excluded.append({"url": url, "reason": "Französische Fassung"})
            continue
        if label == "English" or re.search(r"-enc?(?:-\d+)?\.pdf$", filename, re.I):
            language = "en"
        elif label == "Bilingual" or re.search(r"-bic?(?:-\d+)?\.pdf$", filename, re.I):
            language = "en/fr"
        elif label in ("Original Language", "Translation") and "bilingual" in text(local).lower():
            language = "en/fr"
        else:
            pending.append({"url": url, "title": doc_title, "source": source_url, "reason": "Keine ausdrückliche englische/zweisprachige Sprachkennzeichnung", "label": label})
            continue
        date_label = text(first(cls(doc, "date"))) if doc is not None else ""
        rows.append({"court": "ICJ", "case_id": case_id, "case_title": title,
            "url": url, "pdf_url": url, "title": doc_title, "date": parse_date(date_label, filename),
            "date_as_listed": date_label, "language": language, "language_label": label,
            "language_evidence": {"source": source_url, "label": label, "filename": filename},
            "type": section or kind or "Verfahrensdokument", "source_register": source_url,
            "sources": [source_url], "checked_at": now()})
    # Only pagination for this English case register is followed; arbitrary next links are not.
    for link in soup.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," pager ")]//a[@href] | //a[@rel="next"]'):
        url = urllib.parse.urldefrag(urllib.parse.urljoin(source_url, link.get("href")))[0]
        p = urllib.parse.urlsplit(url)
        if p.hostname in {"icj-cij.org", "www.icj-cij.org"} and re.match(rf"^/case/{case_id}(?:/|$)", p.path):
            links.append(url)
    if not title and not has_documents:
        raise ValueError("Keine erkennbare ICJ-Registerseite (mögliche Fehler-/Zugriffssperrseite)")
    if re.search(r"access denied|just a moment|gcore|captcha", title, re.I):
        raise ValueError("ICJ liefert eine Zugriffssperrseite; keine Umgehung versucht")
    return rows, list(dict.fromkeys(links)), excluded, pending


def prefer_english(rows):
    def key(row):
        return re.sub(r"-(?:en|bi)c?(?=(?:-\d+)?\.pdf$)", "-LANG", url_key(row["url"]), flags=re.I)
    english = {key(row) for row in rows if row["language"] == "en"}
    return [row for row in rows if row["language"] != "en/fr" or key(row) not in english]


def collect_icj(client, progress=lambda *args: None, case_ids=ICJ_CASES):
    rows, audits, errors, pending, excluded = {}, [], [], [], []
    for case_id in case_ids:
        if isinstance(client, PublicClient) and client.limiter.paused(ICJ_BASE):
            host = urllib.parse.urlsplit(ICJ_BASE).hostname
            record_error(errors, HostPaused(host, client.limiter.snapshot()[host]), source="ICJ", url=ICJ_BASE)
            break
        queue = deque([f"{ICJ_BASE}/case/{case_id}"])
        visited = set()
        while queue:
            url = queue.popleft()
            key = url_key(url)
            if key in visited:
                continue
            if len(visited) >= 200:
                errors.append({"source": "ICJ", "case_id": case_id, "url": url, "error": "Sicherheitsgrenze von 200 Registerseiten erreicht; Abgleich unvollständig"})
                break
            visited.add(key)
            progress(f"ICJ {case_id}: Register prüfen", len(audits))
            try:
                body, final = client.text(url)
                found, next_pages, skipped, unclear = extract_icj_page(body, final, case_id)
                audits.append({"source": "ICJ", "case_id": case_id, "url": url, "final_url": final, "status": "ok", "pdf_links": len(found), "checked_at": now()})
                for row in found:
                    k = url_key(row["url"])
                    if k in rows:
                        rows[k]["sources"] = list(dict.fromkeys(rows[k]["sources"] + row["sources"]))
                    else:
                        rows[k] = row
                excluded.extend(skipped)
                pending.extend(unclear)
                queue.extend(u for u in next_pages if url_key(u) not in visited)
            except Exception as exc:
                if not isinstance(exc, HostPaused) and isinstance(client, PublicClient) and "Zugriffssperrseite" in str(exc):
                    exc = client.limiter.pause(url, "Zugriffssperrseite statt ICJ-Register")
                record_error(errors, exc, source="ICJ", case_id=case_id, url=url)
                if isinstance(exc, HostPaused):
                    break
    resolved = set(rows) | {url_key(item["url"]) for item in excluded}
    pending = list({url_key(item["url"]): item for item in pending if url_key(item["url"]) not in resolved}.values())
    return prefer_english(list(rows.values())), audits, errors, pending, excluded


def lt_query_url(where, match, start, limit=100):
    filt = {"where": where, "from": start, "limit": limit,
        "sort": {"dateCreated": {"order": "desc"}, "created": {"order": "asc"}, "externalId.keyword": {"order": "asc"}},
        "include": [{"relation": "languages", "scope": {"fields": ["id", "name"]}}]}
    if match:
        filt["match_phrase"] = match
    return LT_API + "?" + urllib.parse.urlencode({"filter": json.dumps(filt, separators=(",", ":"))})


def lt_record(hit, found_by):
    dec = hit.get("_decision") or {}
    ext = str(hit.get("externalId") or dec.get("fullDocumentNumber") or "").strip()
    languages = [item.get("name") for item in hit.get("languages", [])]
    if ext.startswith("ICC-01/05"):
        return None, "Falsch zugeordnetes sachfremdes ICC-01/05-Verfahren"
    if hit.get("deleted") or hit.get("isPublished") is not True or hit.get("confidentiality") != "public" or hit.get("requireDownloadPermission") is not False:
        return None, "Nicht ausdrücklich öffentlich/freigegeben"
    if "English" not in languages:
        return None, "Keine englischen Sprachmetadaten"
    if re.search(r"-FRA(?:-|$)", ext, re.I):
        return None, "Amtliche Dokumentkennung bezeichnet französische Fassung"
    if re.match(r"^ICC-01/18(?:-|$)", ext):
        proceeding = "ICC-01/18"
    elif re.match(r"^ICC-01/13(?:-|$)", ext):
        proceeding = "ICC-01/13"
    elif ext in ("", "None") and any(name.startswith("comoros_") for name in found_by) and "report" in str(hit.get("title", "")).lower() and str(hit.get("dateCreated", "")).startswith("2014-11-06"):
        proceeding = "ICC-01/13"
    else:
        return None, "Verfahrenszuordnung nicht eindeutig"
    if "press release" in str(hit.get("title", "")).lower():
        return None, "Pressemitteilung"
    url = hit.get("orignalPdfURL") or hit.get("originalPdfURL") or hit.get("pdfURL")
    if not url:
        return None, "Kein öffentlicher PDF-Link"
    safe_url(url)
    date = str(hit.get("dateCreated") or "")[:10]
    try:
        parsed = datetime.fromisoformat(date)
        if parsed.year < 1900:
            date = None
    except ValueError:
        date = None
    original = urllib.parse.urljoin("https://www.icc-cpi.int", dec["url"]) if dec.get("url") else None
    return {"id": hit.get("id"), "court": "ICC", "case_id": proceeding.replace("/", "-"),
        "case_number": proceeding, "proceeding": proceeding,
        "case_title": "Situation in the State of Palestine" if proceeding.endswith("18") else "Situation on Registered Vessels of Comoros, Greece and Cambodia",
        "external_id": ext or None, "document_id": ext or None, "title": hit.get("title") or ext or "ICC-Dokument",
        "date": date, "source_date": hit.get("dateCreated"), "source": hit.get("source"),
        "language": languages, "language_label": "English", "language_status": "Englisch laut öffentlicher Legal-Tools-Sprachmetadaten; keine eigene Übersetzung.",
        "record_url": hit.get("documentUrl"), "source_register": hit.get("documentUrl") or "https://www.legal-tools.org/",
        "url": url, "pdf_url": hit.get("pdfURL"), "direct_pdf_url": url, "icc_original_pdf_url": original,
        "database_updated": hit.get("updated"), "found_by": found_by,
        "language_evidence": {"database": "Legal Tools", "languages": languages, "record_url": hit.get("documentUrl")},
        "confidentiality": "public", "is_published": True, "require_download_permission": False, "checked_at": now()}, None


def collect_lt(client, progress=lambda *args: None):
    hits, audits, errors, pending, excluded = {}, [], [], [], []
    for name, where, match in LT_SPECS:
        if isinstance(client, PublicClient) and client.limiter.paused(LT_API):
            host = urllib.parse.urlsplit(LT_API).hostname
            record_error(errors, HostPaused(host, client.limiter.snapshot()[host]), source="Legal Tools", url=LT_API)
            break
        results, total, start, pages = {}, None, 0, []
        try:
            while total is None or start < total:
                url = lt_query_url(where, match, start)
                progress("ICC / Legal Tools: " + name, start)
                data = client.json(url)
                this_total = data.get("total")
                if not isinstance(this_total, int) or not 0 <= this_total <= 20000:
                    raise ValueError("Ungültige/unerwartet große Trefferzahl")
                if total is not None and total != this_total:
                    raise ValueError("Trefferzahl während Pagination geändert; bitte erneut abgleichen")
                total = this_total
                page = data.get("results")
                if not isinstance(page, list) or (not page and start < total):
                    raise ValueError("Fehlende Ergebnisse in API-Seite")
                for item in page:
                    hit = item.get("hit") or {}
                    if not hit.get("id"):
                        raise ValueError("Ergebnis ohne eindeutige ID")
                    results[str(hit["id"])] = hit
                pages.append({"from": start, "returned": len(page), "url": url})
                start += 100
            if len(results) != total:
                raise ValueError(f"Pagination nicht vollständig: {len(results)} eindeutige IDs bei {total} Treffern")
            audits.append({"source": "Legal Tools", "query": name, "status": "ok", "total": total, "distinct_ids": len(results), "pages": pages, "checked_at": now()})
            for ident, hit in results.items():
                if ident not in hits:
                    hits[ident] = {"hit": hit, "found_by": []}
                hits[ident]["found_by"].append(name)
        except Exception as exc:
            record_error(errors, exc, source="Legal Tools", query=name, url=lt_query_url(where, match, start))
    records = []
    for value in hits.values():
        hit = value["hit"]
        try:
            row, reason = lt_record(hit, value["found_by"])
            if row:
                records.append(row)
            else:
                item = {"id": hit.get("id"), "title": hit.get("title"), "url": hit.get("documentUrl"), "reason": reason}
                (pending if reason in ("Verfahrenszuordnung nicht eindeutig", "Kein öffentlicher PDF-Link") else excluded).append(item)
        except Exception as exc:
            pending.append({"id": hit.get("id"), "title": hit.get("title"), "reason": str(exc)})
    return records, audits, errors, pending, excluded


def validate_pdf(path):
    with path.open("rb") as handle:
        if not handle.read(1024).lstrip().startswith(b"%PDF-"):
            raise ValueError("Antwort enthält keinen PDF-Kopf")
    pdf = pdfium.PdfDocument(str(path))
    try:
        count = len(pdf)
        if count < 1:
            raise ValueError("PDF enthält keine Seiten")
        # Parse every page, but avoid expensive rendering/OCR during update.
        for number in range(count):
            page = pdf[number]
            try:
                if min(page.get_size()) <= 0:
                    raise ValueError(f"Ungültige PDF-Seite {number + 1}")
            finally:
                page.close()
        return count
    finally:
        pdf.close()


def language_check(path, row):
    """Catch conflicting English metadata, without treating this as translation/OCR."""
    if row["court"] != "ICC":
        return {"method": "Sprachkennzeichnung des offiziellen ICJ-Registers", "status": "source_metadata"}
    document = pdfium.PdfDocument(str(path))
    texts = []
    try:
        for number in range(min(4, len(document))):
            page = document[number]
            tp = page.get_textpage()
            try:
                texts.append(tp.get_text_range())
            finally:
                tp.close()
                page.close()
    finally:
        document.close()
    words = re.findall(r"[a-zàâçéèêëîïôùûüÿœ]+", " ".join(texts).lower())
    counts = Counter(words)
    english = sum(counts[w] for w in "the and of to in that for this with on from court chamber application decision victims shall pursuant concerning before has which are".split())
    french = sum(counts[w] for w in "le la les des du et une dans cette avec pour aux sur sont est requête décision victimes chambre conformément cour demande présent".split())
    if french >= 25 and french > max(1, english) * 2.5:
        raise ValueError(f"Sprachprüfung zurückgestellt: französischer Haupttext vermutet trotz englischer Metadaten (EN={english}, FR={french}); keine Aufnahme ohne Prüfung")
    return {"method": "Englische Legal-Tools-Metadaten plus EN/FR-Konfliktprüfung der ersten vier Textseiten; keine OCR", "status": "metadata_only_scan" if len(words) < 50 else "no_detected_language_conflict", "english_score": english, "french_score": french}


def http_date(value):
    if not value:
        return None
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def check_existing_pdf(client, row, existing, previous):
    """Conditional GET: cap response-body reading even when Range is ignored."""
    headers = {"Range": "bytes=0-0"}
    if previous.get("etag"):
        headers["If-None-Match"] = previous["etag"]
    modified = http_date(previous.get("last_modified")) or http_date(existing.get("downloaded_at") or existing.get("checked_at"))
    if modified:
        headers["If-Modified-Since"] = format_datetime(modified, usegmt=True)
    try:
        response = client.open(row["url"], "application/pdf", headers=headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            result = {**previous, "checked_at": now(), "status": "not_modified", "http_status": 304}
            for header, key in (("ETag", "etag"), ("Last-Modified", "last_modified")):
                if exc.headers.get(header):
                    result[key] = exc.headers[header]
            exc.close()
            return False, result
        raise
    with response:
        status = getattr(response, "status", 200)
        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type:
            raise ValueError("Bekannte PDF-Quelle liefert HTML/Zugriffssperre")
        sample = response.read(8 if status == 200 else 1)
        if status == 200 and not sample.lstrip().startswith(b"%PDF-"):
            raise ValueError("Bekannte PDF-Quelle liefert keinen PDF-Kopf")
        total = None
        content_range = response.headers.get("Content-Range", "")
        match = re.search(r"/(\d+)$", content_range)
        if match:
            total = int(match[1])
        elif status == 200 and response.headers.get("Content-Length", "").isdigit():
            total = int(response.headers["Content-Length"])
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        changed = bool((previous.get("etag") and etag and previous["etag"] != etag)
            or (total is not None and existing.get("bytes") and total != existing["bytes"]))
        remote_time = http_date(last_modified)
        if remote_time and modified and remote_time > modified:
            changed = True
        result = {"etag": etag, "last_modified": last_modified, "bytes": total,
            "checked_at": now(), "http_status": status,
            "status": "changed" if changed else "metadata_unchanged" if previous else "baseline",
            "limited_validators": not (etag or last_modified), "final_url": response.url}
        return changed, result


def download_pdf(client, row):
    temp_dir = COURT_DATA / "downloads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp = temp_dir / (uuid.uuid4().hex + ".part")
    digest, size = hashlib.sha256(), 0
    try:
        with client.open(row["url"], "application/pdf") as response, temp.open("xb") as out:
            final = response.url
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > 1024 * 1024 * 1024:
                    raise ValueError("PDF größer als 1 GiB; manueller Abruf erforderlich")
                if size == len(block) and not block[:1024].lstrip().startswith(b"%PDF-"):
                    raise ValueError("Quelle liefert keine PDF-Datei (möglicherweise Zugriffssperre)")
                digest.update(block)
                out.write(block)
        pages = validate_pdf(temp)
        language_validation = language_check(temp, row)
        sha = digest.hexdigest()
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", row.get("document_id") or row.get("title") or "Dokument").strip("-")[:85] or "Dokument"
        case = re.sub(r"[^A-Za-z0-9_-]+", "-", str(row["case_id"]))
        dest = COURTS / "PDF" / row["court"] / case / f"{slug}__{sha[:16]}.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if hashlib.sha256(dest.read_bytes()).hexdigest() != sha:
                raise ValueError("Zieldatei bereits vorhanden mit abweichendem Inhalt; nicht überschrieben")
        else:
            temp.replace(dest)
        return {**row, "file_path": str(dest.resolve()), "sha256": sha, "pages": pages, "bytes": size,
            "final_url": final, "status": "downloaded", "downloaded_at": now(), "content_type": "application/pdf",
            "language_validation": language_validation}
    finally:
        temp.unlink(missing_ok=True)


def run_update(client=None, rebuild=None):
    client = client or PublicClient()
    if rebuild is None:
        from .index_courts import build_index
        rebuild = build_index
    with update_lock():
        return _run_update(client, rebuild)


def _run_update(client, rebuild):
    started = now()
    if isinstance(client, PublicClient):
        last_status = read_json(COURT_DATA / "update_status.json", {})
        client.limiter.restore_cooldowns(last_status.get("source_pauses"))
    # A new installation starts without a legacy PDF collection. New public
    # documents are stored in additions.json, as in subsequent updates.
    original = read_json(COURT_SOURCE / "Verzeichnis.json", {"documents": []})
    if not isinstance(original, dict) or not isinstance(original.get("documents"), list):
        raise RuntimeError("Originalkatalog ist ungültig; Abgleich abgebrochen")
    previous = read_json(COURT_DATA / "additions.json", {"documents": []})
    existing = original["documents"] + previous.get("documents", [])
    if not (COURT_SOURCE / "Verzeichnis.json").is_file() or not (COURT_DATA / "additions.json").is_file() or not existing:
        require_indexed_ids(COURT_DATA / "court_index.sqlite3", (r.get("sha256") for r in existing))
    if not existing and any(COURTS.rglob("*.pdf")):
        raise RuntimeError("PDF-Bestand ohne Quellenkatalog; Abgleich abgebrochen, Dateien bleiben erhalten.")
    known_map = {u: row for row in existing for u in known_pdf_urls(row)}
    known_urls = {u for row in existing for u in known_pdf_urls(row)} | set(previous.get("known_urls", []))
    reviewed = reviewed_exclusions()
    validators = dict(previous.get("validators", {}))
    known_hashes = {r.get("sha256") for r in existing if r.get("sha256")}
    source_ids = {(r.get("court"), str(r.get("id"))) for r in existing if r.get("id")}
    last_complete = previous.get("checked_at") or original.get("coverage", {}).get("checked_at")
    state = {"phase": "running", "message": "ICC/ICJ-Abgleich startet", "time": started, "started_at": started,
        "progress": 0, "new": 0, "changed": 0, "errors": [], "checked_at": last_complete,
        "coverage_notes": NOTES, "new_documents": [], "open_downloads": []}
    def status(message, progress=None):
        state.update(message=message, time=now())
        if isinstance(client, PublicClient):
            state["source_pauses"] = client.limiter.snapshot()
        if progress is not None:
            state["progress"] = progress
        atomic_json(COURT_DATA / "update_status.json", state)
        print(message, flush=True)
    status(state["message"])
    try:
        records, audits, errors, pending, excluded = [], [], [], [], []
        for collector in (collect_icj, collect_lt):
            result = collector(client, lambda message, _number=None: status(message, min(35, 5 + len(audits))))
            for dest, values in zip((records, audits, errors, pending, excluded), result):
                dest.extend(values)
        state["errors"] = errors
        state["registers_checked"] = len(audits)
        if not audits:
            state.update(phase="error", progress=100, finished_at=now(), open_downloads=pending)
            status("Kein öffentliches Register erreichbar. Der bisherige Suchbestand bleibt verfügbar.")
            atomic_json(COURT_DATA / "last_attempt.json", {**state, "registers": audits, "excluded": excluded})
            return state
        pending = [item for item in pending if url_key(item.get("url")) not in known_urls]
        for row in records:
            if url_key(row["url"]) in reviewed:
                excluded.append({"url": row["url"], "title": row["title"], "reason": reviewed[url_key(row["url"])], "review": "Vorhandene manuelle Quellenprüfung"})
        records = [row for row in records if url_key(row["url"]) not in reviewed]
        candidates = {url_key(r["url"]): r for r in records if url_key(r["url"]) not in known_urls}
        # Newly published PDFs have priority over rechecking old URLs. A stale
        # historical link can deny an entire host; do not let that starve new
        # documents that the current public registers explicitly expose.
        additions = list(previous.get("documents", []))
        duplicate_count, new_count, changed_count = 0, 0, 0
        fresh = []
        changed_urls = set()
        observed_validators = {}
        state["download_total"] = 0
        state["download_completed"] = 0

        def download_batch(batch, label, start_progress, end_progress):
            nonlocal duplicate_count, new_count, changed_count
            ordered = sorted(batch.values(), key=lambda row: row.get("date") or "", reverse=True)
            completed_before = state["download_completed"]
            state["download_total"] = completed_before + len(ordered)
            for number, row in enumerate(ordered, 1):
                status(f"{label} {number}/{len(ordered)}: {row['title']}", round(start_progress + (end_progress - start_progress) * (number - 1) / max(1, len(ordered))))
                try:
                    saved = download_pdf(client, row)
                    if url_key(row["url"]) in observed_validators:
                        validators[url_key(row["url"])] = observed_validators[url_key(row["url"])]
                    known_urls.update(known_pdf_urls(saved))
                    if saved["sha256"] in known_hashes:
                        duplicate_count += 1
                    else:
                        saved["archive_id"] = saved["court"].lower() + "-" + saved["sha256"][:24]
                        changed = url_key(row["url"]) in changed_urls or bool(saved.get("id") and (saved["court"], str(saved["id"])) in source_ids)
                        saved["version_status"] = "Weitere Fassung" if changed else "Neu erfasst"
                        additions.append(saved)
                        fresh.append({"title": saved["title"], "court": saved["court"], "case_id": saved["case_id"], "date": saved.get("date"), "file_path": saved["file_path"], "version_status": saved["version_status"]})
                        known_hashes.add(saved["sha256"])
                        changed_count += int(changed)
                        new_count += int(not changed)
                        state.update(new=new_count, changed=changed_count)
                except Exception as exc:
                    problem = record_error(errors, exc, source=row["court"], url=row["url"], title=row["title"])
                    if problem is not None:
                        pending.append(problem)
                    if isinstance(exc, HostPaused):
                        state["download_skipped"] = state.get("download_skipped", 0) + 1
                state["download_completed"] = completed_before + number

        download_batch(candidates, "Neue Register-PDF", 35, 55)
        if fresh or duplicate_count:
            # A later failed/aborted historical recheck must not lose successful
            # imports. Keep the old verified date until the whole check ends.
            atomic_json(COURT_DATA / "additions.json", {**previous, "documents": additions,
                "known_urls": sorted(known_urls), "validators": validators, "checked_at": last_complete,
                "last_attempt_at": started, "index_pending": bool(fresh or previous.get("index_pending")),
                "changes": {"new": new_count, "changed": changed_count, "duplicates": duplicate_count}})
        candidates = {}
        known_checks = {url_key(row["url"]): row for row in sorted(records, key=lambda row: row.get("date") or "", reverse=True) if url_key(row["url"]) in known_map}
        state["known_pdf_total"] = len(known_checks)
        state["known_pdf_checked"] = 0
        thread_clients = threading.local()
        def check_one(item):
            key, row = item
            # Each thread has its own opener and conservative request pacing.
            if isinstance(client, PublicClient):
                if not hasattr(thread_clients, "client"):
                    thread_clients.client = PublicClient(limiter=client.limiter)
                use_client = thread_clients.client
            else:
                use_client = client
            return key, row, check_existing_pdf(use_client, row, known_map[key], validators.get(key, {}))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(check_one, item): item for item in known_checks.items()}
            for number, future in enumerate(as_completed(futures), 1):
                key, row = futures[future]
                try:
                    key, row, (changed, validator) = future.result()
                    if changed:
                        observed_validators[key] = validator
                        candidates[key] = row
                        changed_urls.add(key)
                    else:
                        validators[key] = validator
                except Exception as exc:
                    record_error(errors, exc, source=row["court"], url=row["url"], title=row["title"], stage="Bedingte PDF-Prüfung")
                    if isinstance(exc, HostPaused):
                        state["known_pdf_skipped"] = state.get("known_pdf_skipped", 0) + 1
                state["known_pdf_checked"] = number
                if number % 10 == 0 or number == len(known_checks):
                    status(f"Bekannte PDF-Quellen auf Änderungen prüfen: {number}/{len(known_checks)}", round(55 + 20 * number / max(1, len(known_checks))))
        state["changed_url_signals"] = len(changed_urls)
        state["limited_validator_count"] = sum(bool(v.get("limited_validators")) for v in validators.values())
        download_batch(candidates, "Geänderte bekannte PDF", 75, 90)
        state.update(new=new_count, changed=changed_count, duplicate_files=duplicate_count,
            new_documents=fresh, errors=errors, open_downloads=pending, source_documents_seen=len(records))
        # Preserve the last fully checked timestamp during partial failure.
        finished = now()
        phase = "partial" if errors or pending else "complete"
        current = {**previous, "documents": additions, "known_urls": sorted(known_urls), "validators": validators,
            "checked_at": finished if phase == "complete" else last_complete,
            "last_attempt_at": finished, "last_partial_check_at": finished if phase == "partial" else previous.get("last_partial_check_at"),
            "registers": audits, "errors": errors, "open_downloads": pending, "excluded": excluded,
            "coverage_notes": NOTES, "index_pending": bool(fresh or previous.get("index_pending")),
            "changes": {"new": new_count, "changed": changed_count, "duplicates": duplicate_count},
            "counts_by_court": dict(Counter(r["court"] for r in original["documents"] + additions))}
        atomic_json(COURT_DATA / "additions.json", current)
        if current["index_pending"]:
            status("Neue PDFs übernommen. Volltextindex wird aktualisiert …", 94)
            try:
                rebuild()
                current["index_pending"] = False
                atomic_json(COURT_DATA / "additions.json", current)
            except Exception as exc:
                errors.append({"source": "Suchindex", "error": str(exc)})
                phase = "partial"
                current["errors"] = errors
                atomic_json(COURT_DATA / "additions.json", current)
        state.update(phase=phase, progress=100, checked_at=current["checked_at"], last_attempt_at=finished,
            finished_at=now(), total_documents=len(original["documents"]) + len(additions), errors=errors,
            counts_by_court=current["counts_by_court"])
        message = f"{new_count} neue und {changed_count} weitere Fassungen; {state['total_documents']} PDFs im Archiv."
        if phase == "partial":
            message += f" Teilabgleich: {len(errors)} Quellen-/Indexfehler, {len(pending)} offene Prüfungen."
        else:
            message += " Öffentliche Register erfolgreich abgeglichen; Abdeckungsgrenzen bleiben bestehen."
        status(message)
        report = {**state, "registers": audits, "excluded": excluded}
        atomic_json(COURT_DATA / "last_attempt.json", report)
        atomic_json(COURT_DATA / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".json"), report)
        return state
    except Exception as exc:
        state["errors"].append({"source": "Abgleich", "error": str(exc)})
        state.update(phase="error", finished_at=now())
        status("Abgleich fehlgeschlagen; bisherige Dateien bleiben erhalten: " + str(exc))
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="Nur eine ICJ-Seite und einen kleinen Legal-Tools-Abruf lesen; keine Dateien ändern")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.probe:
        client = PublicClient()
        outcomes = []
        for name, url in (("ICJ", ICJ_BASE + "/case/192"), ("Legal Tools", lt_query_url(LT_SPECS[0][1], LT_SPECS[0][2], 0, 2))):
            try:
                body, final = client.text(url)
                result = {"source": name, "url": final, "bytes": len(body)}
                if name == "ICJ":
                    result["selected_links"] = len(extract_icj_page(body, final, 192)[0])
                else:
                    result["total"] = json.loads(body)["total"]
                outcomes.append(result)
            except Exception as exc:
                outcomes.append({"source": name, "error": str(exc)})
        print(json.dumps(outcomes, ensure_ascii=False, indent=2))
        return 1 if any("error" in row for row in outcomes) else 0
    try:
        result = run_update()
        return 0 if result["phase"] == "complete" else 2 if result["phase"] == "partial" else 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
