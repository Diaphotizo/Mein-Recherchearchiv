"""Bounded discovery in the UN German Translation Service document library.

All retrieval uses the supplied fetch_bytes function. Library language never
establishes a Germany relation: only each document's title and subject tags do.
"""
from __future__ import annotations

from datetime import date
import hashlib
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from lxml import html


SOURCE = 'Deutscher Übersetzungsdienst der Vereinten Nationen'
RELATIONS = {
    'germany': re.compile(r'\b(?:Deutschlands?|Germany|Bundesrepublik Deutschland)\b', re.I),
    'israel': re.compile(r'\b(?:Israel(?:s|isch\w*)?|Israeli)\b', re.I),
    'palestine': re.compile(r'\b(?:Palästin\w*|Palestin\w*|Gaza|Westbank|West Bank|'
                            r'Westjordanland|Ostjerusalem|East Jerusalem|UNRWA)\b', re.I),
}


def clean(value):
    return ' '.join(str(value or '').split())


def is_un_document(record: dict) -> bool:
    """Exclude national legislation merely hosted in the UN language library.

    State reports and replies submitted to a UN procedure remain in scope.
    A UN report *about* a national law is also distinct from the law itself.
    """
    title = clean(record.get('title'))
    symbol = clean(record.get('symbol')).upper()
    kind = clean(record.get('kind')).casefold()
    if symbol.startswith(('DBGBL/', 'BGBL/')):
        return False
    law_title = bool(re.match(r'^Gesetz\b', title, re.I))
    law_kind = kind in ('gesetz', 'bundesgesetz', 'nationales gesetz', 'gesetzestext')
    if law_title or law_kind:
        review = re.search(r'\b(?:Bericht|Prüfung|Bewertung|Stellungnahme|Analyse)\b', title, re.I)
        return bool(review)
    return True


def infer_authorship(record: dict) -> dict:
    """Fill missing authorship only from explicit, attributable title wording.

    Returns a copy. A body such as the Human Rights Council is not automatically
    the author of every document filed there. Existing author metadata wins.
    """
    result = dict(record)
    if clean(result.get('author')):
        return result
    title = clean(result.get('title'))
    symbol = clean(result.get('symbol'))
    author = kind = ''
    upr = re.search(r'\bBericht der Arbeitsgruppe für die Allgemeine regelmäßige Überprüfung\b', title, re.I)
    german_upr = upr and re.search(r'Überprüfung\s*[–—\-:]\s*Deutschland\b', title, re.I)
    state_reply = re.search(r'Antworten des überprüften Staates|Auffassungen zu den Schlussfolgerungen', title, re.I)
    if german_upr and state_reply:
        author, kind = 'Bundesrepublik Deutschland', 'Staatliche Antwort im UPR-Verfahren'
    elif re.search(r'\bBericht der Bundesrepublik Deutschland\b', title, re.I):
        author, kind = 'Bundesrepublik Deutschland', 'Staatlicher Bericht'
    elif upr and not (state_reply or re.search(r'\bAddendum\b|/Add\.', title + ' ' + symbol, re.I)):
        author = 'Arbeitsgruppe für die Allgemeine regelmäßige Überprüfung (UPR)'
        kind = 'Bericht der UPR-Arbeitsgruppe'
    else:
        rapporteur = re.search(
            r'\bBericht (?:des Sonderberichterstatters|der Sonderberichterstatterin)'
            r'(?P<mandate>[^,]+),\s*(?P<name>[A-ZÀ-ÖØ-Þ][\w’\-]+'
            r'(?:\s+(?:(?:de|van|von)\s+)?[A-ZÀ-ÖØ-Þ][\w’\-]+){1,4})\s*,', title)
        if rapporteur:
            role = 'UN-Sonderberichterstatterin' if 'der Sonderberichterstatterin' in rapporteur[0] else 'UN-Sonderberichterstatter'
            author = rapporteur['name'] + ' · ' + role + ' ' + clean(rapporteur['mandate'])
            kind = 'Bericht eines unabhängigen UN-Sachverständigen'
        elif re.search(r'\bAbschließende Bemerkungen\b', title, re.I):
            committees = {
                'des Ausschusses für die Rechte des Kindes': 'UN-Ausschuss für die Rechte des Kindes',
                'des Ausschusses für die Beseitigung der Diskriminierung der Frau': 'UN-Ausschuss für die Beseitigung der Diskriminierung der Frau',
                'des Ausschusses für die Beseitigung der Rassendiskriminierung': 'UN-Ausschuss für die Beseitigung der Rassendiskriminierung',
                'des Ausschusses gegen Folter': 'UN-Ausschuss gegen Folter',
                'des Ausschusses für wirtschaftliche, soziale und kulturelle Rechte': 'UN-Ausschuss für wirtschaftliche, soziale und kulturelle Rechte',
                'des Menschenrechtsausschusses': 'UN-Menschenrechtsausschuss',
            }
            for wording, committee in committees.items():
                if wording.casefold() in title.casefold():
                    author, kind = committee, 'Abschließende Bemerkungen'
                    break
    if author:
        result.update(author=author, kind=kind)
        evidence = 'Urheberschaft aus dem ausdrücklichen Dokumenttitel: „' + title + '“.'
        result['metadata_note'] = (clean(result.get('metadata_note')) + ' ' + evidence).strip()
    return result


def _url(value, source, detail=False, listing=False):
    value = urljoin(source, str(value or '').strip())
    parts = urlsplit(value)
    if (parts.scheme != 'https' or parts.hostname not in ('www.un.org', 'un.org')
            or parts.username or parts.password or parts.port not in (None, 443)):
        return ''
    if detail and not re.fullmatch(r'/german/de/media/\d+/?', parts.path):
        return ''
    if listing and parts.path.rstrip('/') != '/german/de/dokumentenbibliothek':
        return ''
    return value


def _date(value):
    try:
        return date.fromisoformat(clean(value)[:10]).isoformat()
    except ValueError:
        return ''


def _scope(title, tags):
    fields = [('Dokumenttitel', title), ('Stichwörter der UN-Bibliothek', ', '.join(tags))]
    reasons = {key: [label + ': „' + value + '“' for label, value in fields if pattern.search(value)]
               for key, pattern in RELATIONS.items()}
    return {key: bool(values) for key, values in reasons.items()}, reasons


def _next_listing_url(href, source_url):
    """Carry confirmed search filters through a real next-page navigation link."""
    value = _url(href, source_url, listing=True)
    if not value:
        return ''
    parts = urlsplit(value)
    query = parse_qs(parts.query, keep_blank_values=True)
    current = parse_qs(urlsplit(source_url).query, keep_blank_values=True)
    for key, values in current.items():
        if key == 'search_api_fulltext' or key.startswith('uw_search_'):
            if key in query and query[key] != values:
                raise ValueError('Die nächste UN-Listenseite verändert einen gewählten Suchfilter.')
            query.setdefault(key, values)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ''))


def parse_listing(body: bytes, source_url: str) -> dict:
    """Extract eligible table records and an actual next-page link.

    Returned document records still lack pdf_url until their media detail page
    is read. No URL is synthesized from a document number.
    """
    tree = html.fromstring(body.decode('utf-8-sig'))
    query = parse_qs(urlsplit(source_url).query)
    requested_tags = set(query.get('uw_search_media_tags[]', []) + query.get('uw_search_media_tags', []))
    selected_tags = set(tree.xpath('//select[@name="uw_search_media_tags[]"]//option[@selected]/@value'))
    warnings = []
    if requested_tags and not requested_tags.issubset(selected_tags):
        warnings.append('Der angeforderte Stichwortfilter ist im zurückgegebenen UN-Formular nicht bestätigt. '
                        'Erfasst werden nur anhand ihrer eigenen Titel und Stichwörter relevante Tabellenzeilen.')
    requested_text = clean(query.get('search_api_fulltext', [''])[0])
    returned_text = {clean(value) for value in tree.xpath('//input[@name="search_api_fulltext"]/@value')}
    if requested_text and tree.xpath('//input[@name="search_api_fulltext"]') and requested_text not in returned_text:
        warnings.append('Der angeforderte Suchtext ist im zurückgegebenen UN-Formular nicht bestätigt. '
                        'Die Quelle liefert möglicherweise eine ungefilterte Liste.')
    tables = tree.xpath('//main//table[.//th[contains(@id,"document-symbol")]]')
    if not tables:
        # The known Drupal no-results response is an honest empty search.
        empty = tree.xpath('//main//*[contains(@class,"view-empty")]')
        if empty:
            return {'documents': [], 'next_url': '', 'row_count': 0, 'warnings': warnings, 'page_signature': ''}
        raise ValueError('Dokumenttabelle der deutschen UN-Bibliothek nicht erkannt.')
    records = []
    row_count = 0
    row_keys = []
    for row in tables[0].xpath('.//tbody/tr'):
        cells = row.xpath('./td')
        if len(cells) < 6:
            continue
        row_count += 1
        links = cells[1].xpath('.//a[@href]')
        detail_url = next((_url(link.get('href'), source_url, detail=True) for link in links
                           if _url(link.get('href'), source_url, detail=True)), '')
        if not detail_url:
            continue
        title = clean(cells[1].text_content())
        row_keys.append(detail_url + '|' + title)
        tags = [clean(tag) for tag in cells[5].text_content().split(',') if clean(tag) not in ('', '-')]
        relations, reasons = _scope(title, tags)
        if not any(relations.values()):
            continue
        times = cells[2].xpath('.//time/@datetime')
        document_date = _date(times[0] if times else cells[2].text_content())
        organ = clean(cells[3].text_content())
        record = {
            'title': title, 'symbol': clean(cells[0].text_content()), 'date': document_date,
            'body': organ if organ != 'Andere' else '', 'author': '',
            'kind': clean(cells[4].text_content()) or 'UN-Dokument', 'language': 'de',
            'url': detail_url, 'listing_url': source_url, 'tags': tags,
            'relations': relations, 'relation_reasons': reasons,
            'metadata_note': 'Deutsche Fassung beim Deutschen Übersetzungsdienst der Vereinten Nationen; '
                             'Titel, Datum, Organ, Kategorie und Stichwörter aus der Dokumenttabelle. '
                             'Deutsch als Dokumentsprache bedeutet keinen Deutschland-Bezug.',
        }
        if is_un_document(record):
            records.append(infer_authorship(record))
    next_url = ''
    for link in tree.xpath('//main//nav[contains(@class,"paginator") or contains(@class,"pager")]//a[@href]'):
        label = clean(link.get('aria-label', '') + ' ' + link.text_content()).lower()
        if ('nächsten' in label or 'next' in label or link.get('rel') == 'next'):
            next_url = _next_listing_url(link.get('href'), source_url)
            break
    return {'documents': records, 'next_url': next_url, 'row_count': row_count, 'warnings': warnings,
            'page_signature': hashlib.sha256('\n'.join(row_keys).encode()).hexdigest() if row_keys else ''}


def parse_document(body: bytes, source_url: str, record: dict | None = None) -> dict:
    """Resolve the PDF attached to one media detail page, excluding navigation.

    Missing or ambiguous attachments are reported instead of assigning an
    arbitrary footer, organigram or related-document PDF to the record.
    """
    if not _url(source_url, source_url, detail=True):
        raise ValueError('Keine gültige Detailseite der deutschen UN-Bibliothek.')
    tree = html.fromstring(body.decode('utf-8-sig'))
    roots = tree.xpath('//main[@id="content"]') or tree.xpath('//main[@role="main"]')
    if not roots:
        raise ValueError('Hauptinhalt der UN-Dokumentseite nicht erkannt.')
    root = roots[0]
    # Drupal media fields are most specific; the main element is the fallback.
    fields = root.xpath('.//*[contains(@class,"field--name-field-media") or '
                        'contains(@class,"field--name-field-document") or '
                        'contains(@class,"field--name-field-uw-document") or '
                        'contains(@class,"field--name-uw-file")]')
    scopes = fields or [root]
    candidates = {}
    for scope in scopes:
        for link in scope.xpath('.//a[@href]'):
            if link.xpath('ancestor::nav|ancestor::aside') or (not fields and link.xpath('ancestor::footer')):
                continue
            value = _url(link.get('href'), source_url)
            if value and urlsplit(value).path.lower().endswith('.pdf'):
                candidates[value] = clean(link.text_content())
        for frame in scope.xpath('.//iframe[@src]|.//embed[@src]|.//object[@data]'):
            value = _url(frame.get('src') or frame.get('data'), source_url)
            if value and urlsplit(value).path.lower().endswith('.pdf'):
                candidates[value] = ''
    if not candidates:
        raise ValueError('Kein eindeutiger PDF-Anhang im Hauptinhalt der deutschen UN-Dokumentseite gefunden.')
    if len(candidates) != 1:
        raise ValueError(f'{len(candidates)} unterschiedliche PDF-Anhänge gefunden; Zuordnung muss geprüft werden.')
    result = dict(record or {})
    if not result.get('title'):
        headings = root.xpath('.//h1')
        result['title'] = clean(headings[0].text_content()) if headings else ''
    if not result.get('title'):
        raise ValueError('Dokumenttitel fehlt auf der Detailseite.')
    result.update(url=source_url, pdf_url=next(iter(candidates)), language='de')
    return result


def discover_german(fetch_bytes, config) -> dict:
    """Discover configured lists, fetching at most max_pages per starting URL."""
    config = config if isinstance(config, dict) else {}
    documents, errors, notes = {}, [], []
    if config.get('enabled') is False:
        return {'documents': [], 'coverage_notes': [], 'errors': []}
    urls = config.get('urls', [])
    if isinstance(urls, str):
        urls = [urls]
    max_pages = max(1, min(100, int(config.get('max_pages', 1))))
    max_documents = max(1, min(10000, int(config.get('max_documents', 12))))
    per_source_default = max(1, min(max_documents, int(config.get('max_documents_per_source', max_documents))))
    exclude_urls = set(config.get('exclude_urls') or [])
    detail_cache = {}
    paused_hosts = set()
    listing_count = 0
    truncated = 0
    attempted_details = set()
    document_limit_hit = False
    skipped_known = set()
    for descriptor in urls:
        source = descriptor.get('url', '') if isinstance(descriptor, dict) else descriptor
        current = _url(source, source, listing=True)
        if not current:
            errors.append({'source': SOURCE, 'url': str(source), 'error': 'Ungültige UN-Dokumentbibliotheksadresse.'})
            continue
        visited = set()
        seen_pages = set()
        source_count = 0
        source_limit = max(1, min(max_documents, int(descriptor.get('max_documents', per_source_default)))) if isinstance(descriptor, dict) else per_source_default
        source_max_pages = max(1, min(100, int(descriptor.get('max_pages', max_pages)))) if isinstance(descriptor, dict) else max_pages
        for number in range(source_max_pages):
            if current in visited or urlsplit(current).hostname in paused_hosts:
                break
            visited.add(current)
            try:
                listing = parse_listing(fetch_bytes(current), current)
                listing_count += 1
                for warning in listing.get('warnings', []):
                    errors.append({'source': SOURCE, 'url': current, 'error': warning})
                signature = listing.get('page_signature')
                if signature and signature in seen_pages:
                    errors.append({'source': SOURCE, 'url': current, 'error': 'Die UN-Bibliothek wiederholt eine bereits gelesene Ergebnisseite. Historische Fortsetzung nicht bestätigt.'})
                    break
                if signature:
                    seen_pages.add(signature)
            except Exception as exc:
                errors.append({'source': SOURCE, 'url': current, 'error': str(exc)})
                if getattr(exc, 'host', None):
                    paused_hosts.add(exc.host)
                break
            for record in listing['documents']:
                detail = record['url']
                if detail in exclude_urls:
                    skipped_known.add(detail)
                    continue
                if detail not in attempted_details and len(attempted_details) >= max_documents:
                    document_limit_hit = True
                    continue
                if detail not in attempted_details and source_count >= source_limit:
                    document_limit_hit = True
                    continue
                if urlsplit(detail).hostname in paused_hosts:
                    break
                if detail not in attempted_details:
                    source_count += 1
                    attempted_details.add(detail)
                try:
                    if detail not in detail_cache:
                        detail_cache[detail] = fetch_bytes(detail)
                    document = parse_document(detail_cache[detail], detail, record)
                    key = document['pdf_url']
                    if key not in documents:
                        documents[key] = document
                    else:
                        prior = documents[key]
                        prior['tags'] = list(dict.fromkeys(prior['tags'] + document['tags']))
                        for relation in RELATIONS:
                            prior['relations'][relation] |= document['relations'][relation]
                            prior['relation_reasons'][relation] = list(dict.fromkeys(
                                prior['relation_reasons'][relation] + document['relation_reasons'][relation]))
                except Exception as exc:
                    errors.append({'source': SOURCE, 'url': detail, 'error': str(exc)})
                    if getattr(exc, 'host', None):
                        paused_hosts.add(exc.host)
            next_url = listing['next_url']
            if not next_url:
                break
            if number + 1 == source_max_pages:
                truncated += 1
            current = next_url
    notes.append(f'Deutscher UN-Übersetzungsdienst: {listing_count} konfigurierte Listenseiten geprüft, '
                 f'{len(documents)} relevante deutsche PDF-Verweise erfasst. '
                 f'Je Ausgangsliste werden höchstens {max_pages} Seiten geprüft; keine vollständige historische Erfassung.')
    if truncated:
        notes.append(f'{truncated} deutsche Ergebnislisten haben weitere Seiten außerhalb des eingestellten Abrufumfangs.')
    if document_limit_hit:
        notes.append(f'Der aktuelle Lauf prüft höchstens {max_documents} deutsche Dokumentdetailseiten; zusätzliche Listenlimits können den Umfang weiter begrenzen. Weitere Treffer bleiben für eine Erweiterung des Abrufumfangs offen.')
    if skipped_known:
        notes.append(f'{len(skipped_known)} bereits bekannte deutsche Detailseiten wurden für diese ergänzende Suche übersprungen; ihre PDF-Fassungen werden durch den normalen Aktualisierungsablauf geprüft.')
    return {'documents': list(documents.values()), 'coverage_notes': notes, 'errors': errors}
