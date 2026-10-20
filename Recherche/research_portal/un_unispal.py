"""Bounded UNISPAL discovery with document-page attachment provenance.

All I/O is supplied by fetch_bytes(url). PDF validation and downloads belong to
the importer; links cited inside document text are deliberately not harvested.
"""
from __future__ import annotations

from datetime import datetime
import html
import json
import re
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, unquote

from lxml import html as lhtml

API_URL = 'https://www.un.org/unispal/wp-json/wp/v2/document'
FIELDS = 'id,date,modified,link,title,content,class_list'
CORE_CATEGORIES = {
    'annual-report', 'report', 'secretary-general-report', 'resolution',
    'general-assembly-resolution', 'security-council-resolution', 'draft-resolution',
    'decision', 'meeting-record', 'verbatim-record', 'summary-record', 'letter',
    'identical-letters', 'note', 'note-verbale', 'concluding-observations',
    'recommendations', 'working-paper', 'policy-paper', 'study', 'agreement',
    'convention', 'treaty', 'plan-of-action',
}
EXCLUDED_CATEGORIES = {'non-un-document', 'ngo-action-news', 'press-release',
                       'news-item', 'meeting-coverage', 'ceirpp-newsletter', 'newsletter'}
UN_SOURCE_SLUGS = {
    'general-assembly', 'security-council', 'economic-and-social-council-ecosoc',
    'human-rights-council', 'secretary-general', 'ceirpp',
    'division-for-palestinian-rights-dpr', 'international-court-of-justice-icj',
    'office-for-the-coordination-of-humanitarian-affairs-ocha',
    'office-of-the-united-nations-high-commissioner-for-human-rights-ohchr',
    'commission-of-inquiry-on-the-occupied-palestinian-territory',
}
LANGUAGES = {'en': ('en', 'eng', 'english'), 'de': ('de', 'deu', 'ger', 'german', 'deutsch'),
             'ar': ('ar', 'ara', 'arabic'), 'fr': ('fr', 'fra', 'fre', 'french'),
             'es': ('es', 'spa', 'spanish'), 'ru': ('ru', 'rus', 'russian'),
             'zh': ('zh', 'chi', 'zho', 'chinese')}


def plain(value):
    if not value:
        return ''
    tree = lhtml.fragment_fromstring(str(value), create_parent='div')
    return ' '.join(html.unescape(tree.text_content()).split())


def official_url(value, record=False):
    parts = urlsplit(str(value))
    host = (parts.hostname or '').lower()
    if parts.scheme not in ('http', 'https') or parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError('Ungültige UNISPAL-Quellenadresse.')
    if record and not (host in ('www.un.org', 'un.org') and parts.path.startswith('/unispal/document/')):
        raise ValueError('Keine konkrete UNISPAL-Dokumentseite.')
    target = urlunsplit(('https', host, parts.path, parts.query, ''))
    # Reuse the importer's official UN-system host allowlist for attachments;
    # agency publications can be hosted by UNRWA, UNFPA, OHCHR, etc.
    from .update_un import safe_url
    return safe_url(target)


def classes_for(row):
    value = row.get('class_list', [])
    return set(value if isinstance(value, list) else [])


def core_record(row):
    classes = classes_for(row)
    categories = {c.removeprefix('document-category-') for c in classes if c.startswith('document-category-')}
    if categories & EXCLUDED_CATEGORIES or not categories & CORE_CATEGORIES:
        return False
    if 'entity-united-nations-system' not in classes:
        return False
    sources = {c.removeprefix('document-source-') for c in classes if c.startswith('document-source-')}
    if not any(source in UN_SOURCE_SLUGS or source.startswith(('united-nations-', 'special-rapporteur-', 'special-committee-')) for source in sources):
        return False
    title = plain(row.get('title', {}).get('rendered', ''))
    return not re.search(r'\bnon[ -]un\b|\bngo action news\b', title, re.I)


def metadata_fields(tree):
    fields = {}
    boxes = tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," fusion-sharing-box ")]')
    for box in boxes:
        for label in box.xpath('.//strong'):
            key = re.sub(r'\s+', ' ', label.text_content()).strip(' :').casefold()
            values = [label.tail or '']
            for sibling in label.itersiblings():
                if sibling.tag in ('br', 'strong'):
                    break
                values.extend([sibling.text_content(), sibling.tail or ''])
            value = ' '.join(' '.join(values).split()).strip(' ,')
            if key and value:
                fields.setdefault(key, value)
    return fields, boxes


def explicit_language(anchor, target):
    for attribute in ('lang', 'hreflang'):
        value = anchor.get(attribute, '').casefold().strip().split('-')[0]
        for code, aliases in LANGUAGES.items():
            if value in aliases:
                return code
    for candidate in (anchor.get('title', ''), anchor.text_content().strip()):
        if candidate.startswith(('https://', 'http://')):
            continue
        value = re.sub(r'[()\[\]]', ' ', candidate.casefold()).strip()
        for code, aliases in LANGUAGES.items():
            for alias in aliases:
                if value == alias or re.search(r'\b(?:download|pdf|version|text|language|fassung)\s+' + alias + r'\b|\b' + alias + r'\s+(?:version|pdf|text|fassung)\b', value):
                    return code
    path = unquote(urlsplit(target).path).casefold()
    filename = path.rsplit('/', 1)[-1].removesuffix('.pdf')
    for code, aliases in LANGUAGES.items():
        for alias in aliases:
            # Codes such as 'de' and 'en' in a French filename can simply be
            # prepositions. Only a separate final suffix/prefix is a code mark.
            if alias in path.split('/')[:-1] or re.search(r'(?:^|[_-])' + alias + r'$', filename):
                return code
            if len(alias) > 3 and re.search(r'\b' + alias + r'\b', filename.replace('_', ' ')):
                return code
    return ''


def attachment_language(anchor, target, classes, ordinal):
    explicit = explicit_language(anchor, target)
    if explicit:
        return explicit, 'Sprachkennzeichnung an der PDF-Anlage'
    declared = {c.removeprefix('document-language-') for c in classes if c.startswith('document-language-')}
    filename = urlsplit(target).path.rsplit('/', 1)[-1]
    legacy = re.fullmatch(r'nr?\d+[-_]([acefrs])\.pdf', filename, re.I)
    if legacy:
        language = {'a': 'ar', 'c': 'zh', 'e': 'en', 'f': 'fr', 'r': 'ru', 's': 'es'}[legacy[1].lower()]
        return language, 'Sprachsuffix der historischen UN-PDF-Datei'
    if 'french' in declared and re.search(r'\d[fF]\.pdf$', filename):
        return 'fr', 'Historische französische PDF-Fassung: F-Suffix und französische Quellentaxonomie'
    # The record's language taxonomy does not identify the actual attachment:
    # e.g. resolution 242's doc.pdf is Russian despite an English record tag.
    # Only language labels on this attachment may authorize an EN/DE import;
    # otherwise its own content (or a byte-bound manual review) must establish it.
    if declared in ({'english'}, {'german'}):
        return '', 'UNISPAL-Sprachtaxonomie betrifft den Datensatz, nicht die konkrete PDF-Anlage; PDF-Inhaltsprüfung erforderlich'
    # A numeric production filename is not a language identifier. Multi-language
    # records are checked inside the PDF even if their first attachment is
    # commonly English; historic lists sometimes put another language first.
    if ordinal == 0 and 'english' in declared and re.fullmatch(r'[nas]\d{6,10}\.pdf', filename, re.I):
        return '', 'Erste numerische UN-PDF-Anlage bei mehrsprachigem Datensatz; Englisch wird erst nach PDF-Inhaltsprüfung bestätigt'
    return '', 'Keine eindeutige Sprachkennzeichnung der Anlage; PDF-Inhalt muss geprüft werden'


def relation_evidence(row, fields, title, source_url):
    text = plain(row.get('content', {}).get('rendered', ''))
    relations = {'germany': False, 'israel': False, 'palestine': True}
    reasons = {'germany': [], 'israel': [], 'palestine': [f'Themenzuordnung im UN-Informationssystem zur Palästinafrage (UNISPAL): {source_url}']}
    for key, pattern in (('germany', r'\bgerman(?:y)?\b|\bdeutschland\b'), ('israel', r'\bisrael(?:i)?\b')):
        if re.search(pattern, title, re.I):
            relations[key] = True
            reasons[key].append(f'Expliziter Länderbezug im Dokumenttitel: {title} ({source_url})')
    patterns = {
        'germany': r'\bGermany\s+(?:(?:has|had)\s+)?(?:submitted|presented|provided|reported|requested|called|urged|funded|contributed|voted|supported|opposed|signed|ratified)\b|\b(?:Government|representative|delegation|statement|submission|letter)\s+of\s+Germany\b|\bGerman\s+(?:Government|delegation|authorities|submission)\b',
        'israel': r'\bIsraeli\s+(?:authorities|forces|military|settlements|occupation|Government|restrictions|attacks|operations)\b|\bIsrael\s+(?:must|shall|should|continues|has\s+(?:occupied|restricted|imposed)|is\s+required)\b',
    }
    for key, pattern in patterns.items():
        if not relations[key]:
            hit = re.search(pattern, text, re.I)
            if hit:
                relations[key] = True
                # Retain a short, inspectable excerpt rather than an opaque flag.
                excerpt = text[max(0, hit.start() - 35): min(len(text), hit.end() + 100)]
                reasons[key].append(f'Beleg im veröffentlichten Dokumenttext: „{excerpt}“ ({source_url})')
    return relations, reasons


def parse_document(row, content):
    source_url = official_url(row.get('link'), record=True)
    tree = lhtml.fromstring(content)
    fields, boxes = metadata_fields(tree)
    title = plain(row.get('title', {}).get('rendered', ''))
    if not title:
        raise ValueError('UNISPAL-Dokumenttitel fehlt.')
    date = ''
    if fields.get('publication date'):
        match = re.search(r'\b(\d{2}/\d{2}/\d{4})\b', fields['publication date'])
        if match:
            date = datetime.strptime(match[1], '%d/%m/%Y').date().isoformat()
    if date and date < '1945-01-01':
        # UNISPAL also catalogs League of Nations/mandate-era background texts;
        # later UN source tags do not turn these into documents issued by the UN.
        raise ValueError('Historische Vorläuferquelle vor 1945; kein von den Vereinten Nationen veröffentlichtes Dokument.')
    sources = fields.get('document sources', '')
    if not sources:
        raise ValueError('Dokumentseite nennt keine Document Sources.')
    symbol = fields.get('document symbol', '')
    kind_text = fields.get('document type', '')
    lower = kind_text.casefold()
    kind = ('Resolution' if 'resolution' in lower else 'Bericht' if 'report' in lower else
            'Sitzungsprotokoll' if 'record' in lower else 'Schreiben' if 'letter' in lower else
            'Beschluss' if 'decision' in lower else kind_text or 'UN-Dokument')
    body = next((name for name in ('General Assembly', 'Security Council', 'Human Rights Council') if name in sources), sources)
    relations, reasons = relation_evidence(row, fields, title, source_url)
    anchors = [a for box in boxes for a in box.xpath('.//a[contains(concat(" ",normalize-space(@class)," ")," pdf-link ")][@href]')]
    documents, errors, seen = [], [], set()
    for ordinal, anchor in enumerate(anchors):
        try:
            target = official_url(urljoin(source_url, anchor.get('href')))
            if not urlsplit(target).path.casefold().endswith('.pdf') or target in seen:
                continue
            seen.add(target)
            language, language_note = attachment_language(anchor, target, classes_for(row), ordinal)
            if language not in ('', 'en', 'de'):
                continue
            documents.append({'title': title, 'date': date, 'symbol': symbol, 'body': body,
                'author': sources, 'kind': kind, 'language': language, 'url': source_url, 'pdf_url': target,
                'relations': relations, 'relation_reasons': reasons, 'tags': ['Palästina'],
                'metadata_note': f'UNISPAL-Datensatz {row.get("id", "")}; Quellen- und Dokumentdaten aus der Dokumentseite, PDF aus „Download Document Files“. {language_note}. '
                    + ('Publikationsdatum aus „Publication Date“.' if date else 'Kein belegtes Publikationsdatum; Web-Veröffentlichungsdatum wird nicht als Dokumentdatum verwendet.')})
        except Exception as exc:
            errors.append({'source': 'UNISPAL-Anlage', 'url': source_url, 'error': str(exc)})
    if not anchors:
        original = fields.get('url source', '')
        ods_links = tree.xpath('//article//a[contains(@href,"undocs.org") or contains(@href,"docs.un.org")]/@href')
        explanation = ('Die Dokumentseite verlinkt nur UN-ODS-Sprachfassungen, keine unmittelbar verfügbare PDF-Anlage. Automatisierte ODS-Abrufe werden nicht umgangen.'
                       if ods_links else 'Keine direkt zugeordnete PDF-Anlage auf der Dokumentseite.')
        error = {'source': 'UNISPAL-Anlage', 'url': source_url, 'error': explanation}
        if original:
            error['original_source'] = original
            error['error'] += ' Die angegebene Originalquelle ist separat zu prüfen: ' + original
        errors.append(error)
    return documents, errors


def register_query(config):
    """WordPress time constraints concern website records, not PDF dates."""
    result = {}
    if config.get('search'):
        result['search'] = str(config['search'])[:300]
    order = str(config.get('order', 'desc')).lower()
    if order not in ('asc', 'desc'):
        raise ValueError('UNISPAL-Sortierung muss asc oder desc sein.')
    result.update(order=order, orderby='date')
    for field in ('before', 'after'):
        if not config.get(field):
            continue
        value = str(config[field]).strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            value += 'T23:59:59' if field == 'before' else 'T00:00:00'
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        result[field] = value
    return result


def record_from_page(url, content):
    """Read explicit document-page metadata for configured historic landmarks."""
    url = official_url(url, record=True)
    tree = lhtml.fromstring(content)
    articles = tree.xpath('//article[contains(concat(" ",normalize-space(@class)," ")," document ")]')
    if len(articles) != 1:
        raise ValueError('Keine eindeutige UNISPAL-Dokumentseite mit Quellentaxonomie.')
    article = articles[0]
    # Avada's older pages put the page-title H1 outside the article and repeat
    # the document title as H2.entry-title inside it. Restrict the fallback to
    # the theme's identified title, never an arbitrary page/subscribe heading.
    headings = article.xpath('.//*[self::h1 or self::h2][contains(concat(" ",normalize-space(@class)," ")," entry-title ")]')
    if not headings:
        headings = article.xpath('.//h1') or tree.xpath('//h1[@id="awb-ptb-heading"][contains(concat(" ",normalize-space(@class)," ")," entry-title ")]')
    if not headings:
        raise ValueError('Dokumenttitel fehlt auf der UNISPAL-Seite.')
    content_nodes = article.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," post-content ")]')
    row = {'id': (article.get('id') or '').removeprefix('post-'), 'link': url,
           'title': {'rendered': headings[0].text_content()},
           'class_list': (article.get('class') or '').split(),
           'content': {'rendered': lhtml.tostring(content_nodes[0] if content_nodes else article, encoding='unicode')}}
    if not core_record(row):
        raise ValueError('Die konfigurierte Seite ist kein belegtes UN-Kerndokument.')
    return row


def discover_unispal(fetch_bytes, config=None):
    """Return normalized source candidates, bounded coverage notes and errors.

    config: per_page (60), max_pages (1), max_documents (20), start_page (1).
    Optional queries: [{label, search, order, before, after, per_page, max_pages,
    start_page, max_documents}], inheriting the top-level defaults. max_documents
    also caps the total number of unique document pages. Optional record_urls
    are prioritized explicit UNISPAL pages, requiring the same source/type tests.
    Dates in coverage describe register publication, not document publication.
    """
    config = config or {}
    max_documents = max(1, min(200, int(config.get('max_documents', 20))))
    documents, errors, selected, seen, coverage_notes = [], [], [], set(), []
    page_cache = {}
    record_urls = config.get('record_urls', [])
    if isinstance(record_urls, str):
        record_urls = [record_urls]
    for entry in record_urls[:max_documents]:
        source_url = entry.get('url', '') if isinstance(entry, dict) else entry
        try:
            source_url = official_url(source_url, record=True)
            if source_url in seen:
                continue
            content = fetch_bytes(source_url)
            row = record_from_page(source_url, content)
            page_cache[source_url] = content
            seen.add(source_url)
            selected.append(row)
        except Exception as exc:
            errors.append({'source': 'UNISPAL-Dokumentseite', 'url': source_url, 'error': str(exc)})
    if record_urls:
        coverage_notes.append(f'UNISPAL: {min(len(record_urls), max_documents)} ausdrücklich konfigurierte Dokumentseiten geprüft. Historische Dokumentdaten werden ausschließlich aus deren belegten Publikationsangaben übernommen.')
    queries = config.get('queries', [config])
    if not isinstance(queries, list):
        raise ValueError('UNISPAL queries muss eine Liste sein.')
    for number, query in enumerate(queries[:20], 1):
        if isinstance(query, str):
            query = {'search': query}
        if not isinstance(query, dict):
            raise ValueError('Ungültige UNISPAL-Registerabfrage.')
        effective = {**config, **query}
        params = register_query(effective)
        per_page = max(1, min(100, int(effective.get('per_page', 60))))
        max_pages = max(1, min(20, int(effective.get('max_pages', 1))))
        start_page = max(1, int(effective.get('start_page', 1)))
        query_limit = max(1, min(max_documents, int(query.get('max_documents', max_documents))))
        label = str(query.get('label') or params.get('search') or f'Registerabfrage {number}')
        inspected = eligible = 0
        register_dates = []
        for page in range(start_page, start_page + max_pages):
            url = API_URL + '?' + urlencode({'per_page': per_page, '_fields': FIELDS, 'page': page, **params})
            try:
                rows = json.loads(fetch_bytes(url))
                if not isinstance(rows, list):
                    raise ValueError('UNISPAL-Register lieferte keine Dokumentliste.')
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    inspected += 1
                    if row.get('date'):
                        register_dates.append(str(row['date'])[:10])
                    if not core_record(row):
                        continue
                    eligible += 1
                    try:
                        source_url = official_url(row.get('link'), record=True)
                        if source_url not in seen and eligible <= query_limit:
                            seen.add(source_url)
                            selected.append(row)
                    except ValueError as exc:
                        errors.append({'source': 'UNISPAL-Dokumentseite', 'url': row.get('link', ''), 'error': str(exc)})
                if len(rows) < per_page:
                    break
            except Exception as exc:
                errors.append({'source': 'UNISPAL-Register', 'url': url, 'error': str(exc)})
                break
        dates = sorted(register_dates)
        window = f'Web-Veröffentlichungen {dates[0]} bis {dates[-1]}' if dates else 'Zeitraum mangels Registerdaten unbekannt'
        filters = ', '.join(f'{key}={value}' for key, value in params.items() if key != 'orderby')
        coverage_notes.append(f'UNISPAL – {label}: begrenzter Registerausschnitt ab Seite {start_page}, höchstens {max_pages} Seite(n) mit je {per_page} Einträgen; {inspected} Einträge geprüft, {window}; {filters}. '
            f'{eligible} geeignete UN-Kerndokumente im Ausschnitt, höchstens {query_limit} davon ausgewählt. Kein vollständiger historischer Bestand. before/after beziehen sich auf Web-Veröffentlichungen, nicht auf das Datum des Dokuments.')
        if eligible > query_limit:
            coverage_notes.append(f'Das konfigurierte Limit lässt {eligible - query_limit} geeignete Dokumentseiten der Abfrage „{label}“ ungeprüft.')
    for row in selected[:max_documents]:
        try:
            source_url = official_url(row['link'], record=True)
            content = page_cache[source_url] if source_url in page_cache else fetch_bytes(source_url)
            found, warnings = parse_document(row, content)
            documents.extend(found)
            errors.extend(warnings)
        except Exception as exc:
            errors.append({'source': 'UNISPAL-Dokumentseite', 'url': row.get('link', ''), 'error': str(exc)})
    coverage_notes.extend([
        f'UNISPAL: {len(selected)} eindeutige Dokumentseiten ausgewählt; insgesamt höchstens {max_documents} Dokumentseiten geprüft, {len(documents)} englische/deutsche oder noch sprachlich zu prüfende PDF-Anlagen ermittelt. Nicht-UN-Texte, NGO-Rundbriefe, Presse- und allgemeine Nachrichten werden ausgeschlossen.',
        'Nur direkt auf der jeweiligen UNISPAL-Dokumentseite zugeordnete Download-PDFs werden erfasst. Zitate und andere PDF-Verweise im Fließtext sind keine eigenständigen Importziele. Verfügbare deutsche Anlagen werden zusätzlich berücksichtigt; unklare Sprachen werden im PDF geprüft.',
    ])
    if len(selected) > max_documents:
        coverage_notes.append(f'Das konfigurierte Seitenlimit lässt {len(selected) - max_documents} geeignete Dokumentseiten dieses Registerausschnitts ungeprüft.')
    return {'documents': documents, 'coverage_notes': coverage_notes, 'errors': errors}
