"""Bounded discovery of public, official UN source lists.

Every network read uses the importer's robots-aware GET client. Country
relations are evidence from document metadata, not the language of a website.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin, urlsplit

from lxml import html

from .un_spcomm import parse_communications


def clean(value):
    return ' '.join(str(value or '').split())


def ocha_links(body, url):
    tree = html.fromstring(body)
    found = []
    for a in tree.xpath('//a[@href]'):
        target = urljoin(url, a.get('href'))
        parts = urlsplit(target)
        if parts.scheme == 'https' and parts.hostname == 'www.ochaopt.org' and parts.path.startswith('/content/'):
            if target not in found:
                found.append(target)
    return found


def parse_ocha(body, url):
    tree = html.fromstring(body)
    headings = tree.xpath('//h1')
    title = clean(headings[0].text_content()) if headings else ''
    if not title:
        raise ValueError('OCHA-Dokumenttitel fehlt')
    # Source publication date; a reference date in the title remains in the title.
    times = tree.xpath('//time[@datetime]/@datetime')
    published = times[0][:10] if times else ''
    if published:
        date.fromisoformat(published)
    pal_match = re.search(r'Gaza|West Bank|Palestin|UNRWA', title, re.I)
    israel_match = re.search(r'\bIsrael(?:i)?\b', title, re.I)
    if not pal_match and not israel_match:
        return []
    relations = {'germany': False, 'israel': bool(israel_match), 'palestine': bool(pal_match)}
    reasons = {key: ['Dokumenttitel der OCHA-Quelle: „' + title + '“.']
               for key, value in relations.items() if value}
    rows, seen = [], set()
    for link in tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," document ")]//a[@href]'):
        target = urljoin(url, link.get('href'))
        parts = urlsplit(target)
        # Actual publication attachments on OCHA; no unrelated external citations.
        if (parts.scheme != 'https' or parts.hostname != 'www.ochaopt.org'
                or not parts.path.startswith('/sites/default/files/')
                or not parts.path.lower().endswith('.pdf') or target in seen):
            continue
        seen.add(target)
        language_hint = ' '.join([parts.path, link.text_content(), link.get('hreflang', '')])
        if re.search(r'(?:^|[_/ .-])(?:ar|ara|arabic|fr|french|ru|es|zh)(?:[_/ .-]|$)', language_hint, re.I):
            continue
        german = bool(re.search(r'\b(?:German|Deutsch)\b|[_-]de(?:[_\.-]|$)', language_hint, re.I))
        rows.append({'title': title, 'date': published, 'symbol': '',
                     'body': 'OCHA · besetztes palästinensisches Gebiet',
                     'author': 'United Nations Office for the Coordination of Humanitarian Affairs (OCHA)',
                     'kind': 'Humanitärer Lagebericht / Übersicht',
                     'language': 'de' if german else '', 'url': url, 'pdf_url': target,
                     'relations': relations, 'relation_reasons': reasons,
                     'tags': [x for x in ('Gaza' if 'gaza' in title.lower() else '',
                                         'Westbank' if 'west bank' in title.lower() else '',
                                         'Humanitäre Lage') if x],
                     'metadata_note': 'Datum: Veröffentlichung der OCHA-Seite; Stichtag ggf. im Titel. Sprache am PDF geprüft. Länderbezug konservativ nach Dokumenttitel.'})
    return rows


def discover_ocha(fetch_bytes, config):
    url = config.get('url', 'https://www.ochaopt.org/publications')
    limit = min(30, max(1, int(config.get('max_documents', 9))))
    links = ocha_links(fetch_bytes(url), url)
    rows, errors, no_pdf = [], [], 0
    for link in links[:limit]:
        try:
            result = parse_ocha(fetch_bytes(link), link)
            rows.extend(result)
            no_pdf += not bool(result)
        except Exception as exc:
            errors.append({'source': 'OCHA', 'url': link, 'error': str(exc)})
    return {'documents': rows, 'errors': errors, 'coverage_notes': [
        f'OCHA: neueste öffentliche Übersichtsseite, bis zu {limit} verlinkte Veröffentlichungen pro Abgleich; {len(rows)} PDF-Verweise gefunden. Ältere Jahrgänge werden damit nicht vollständig erfasst.',
        f'OCHA: {no_pdf} geprüfte Seiten ohne passende englische/deutsche PDF-Verknüpfung; reine Webseiten sind nicht Teil des PDF-Bestands.']}


def discover_special(fetch_bytes, config):
    rows, notes, errors = [], [], []
    for source in config.get('sources', []):
        url = source['url']
        try:
            content = fetch_bytes(url)
            tree = html.fromstring(content)
            if not tree.xpath('//table[@id="table-results"]'):
                raise ValueError('Öffentliche OHCHR-Ergebnisliste nicht erkannt')
            result = parse_communications(content, url)
            limit = min(500, max(1, int(source.get('max_documents', 150))))
            # Newest first, preserving a bounded, repeatable update window.
            result.sort(key=lambda row: (row.get('date', ''), row['pdf_url']), reverse=True)
            rows.extend(result[:limit])
            notes.append(f'OHCHR-Sonderverfahren: {source.get("label", url)}. {len(result[:limit])} relevante Mitteilungen/Antworten aus {len(result)} Treffern dieser Mandatsliste; keine vollständige Ländersammlung aller Sonderverfahren. Antworten von Mitgliedstaaten und anderen Adressaten werden gesondert zugeschrieben.')
        except Exception as exc:
            errors.append({'source': 'OHCHR-Sonderverfahren', 'url': url, 'error': str(exc)})
    return {'documents': rows, 'coverage_notes': notes, 'errors': errors}


def discover(fetch_bytes, manifest):
    config = manifest.get('discovery', {})
    result = {'documents': [], 'coverage_notes': [], 'errors': []}
    from .un_german import discover_german
    from .un_unispal import discover_unispal
    adapters = [('special_procedures', discover_special), ('ocha', discover_ocha),
                ('unispal', discover_unispal), ('german', discover_german)]
    for name, adapter in adapters:
        if not config.get(name):
            continue
        print(f'UN-Quellenliste: {name}', flush=True)
        try:
            part = adapter(fetch_bytes, config[name])
            for field in result:
                result[field].extend(part.get(field, []))
            print(f'  {len(part.get("documents", []))} PDF-Verweise, {len(part.get("errors", []))} offene Abrufe', flush=True)
        except Exception as exc:
            result['errors'].append({'source': name, 'error': str(exc)})
    return result
