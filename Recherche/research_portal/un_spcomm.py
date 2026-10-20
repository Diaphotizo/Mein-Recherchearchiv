"""Read public OHCHR Special Procedures communication result tables.

Only the addressee and substantive summary establish country relations. The
adjacent mandate column deliberately does not: it often contains a Palestine
mandate even when the actual communication concerns an unrelated country.
No network access occurs in this parser. Document language is left unknown
unless the listing explicitly identifies it; an English summary is not proof
that the attached government reply is written in English.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import parse_qs, urljoin, urlsplit

from lxml import html


COUNTRIES = {'DEU': ('germany', 'Bundesrepublik Deutschland'),
             'ISR': ('israel', 'Israel')}
TOPICS = {
    'germany': re.compile(r'\b(?:Germany|German|Deutschland|deutsch\w*)\b', re.I),
    'israel': re.compile(r'\b(?:Israel|Israeli|israelisch\w*)\b', re.I),
    'palestine': re.compile(r'\b(?:Palestin\w*|Palästin\w*|Gaza|West Bank|Westbank|'
                            r'Westjordanland|East Jerusalem|Ostjerusalem|UNRWA)\b', re.I),
}
MONTHS = {name: number for number, name in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
LABELS = {'germany': 'Deutschland', 'israel': 'Israel', 'palestine': 'Palästina / UNRWA'}

# Deliberately narrow exclusions: a labelled country/distribution list or an
# explicit dispatch sentence followed solely by country names. We do not use a
# verb whitelist to decide which substantive allegations or policy issues count.
_COUNTRY_WORD = r"[A-ZÀ-ÖØ-Þ][\w’'\-]*"
_COUNTRY_NAME = _COUNTRY_WORD + r'(?:\s+(?:(?:of|the|der|des|von)\s+)?' + _COUNTRY_WORD + r')*'
_COUNTRY_LIST = re.compile(r'(?:the\s+)?' + _COUNTRY_NAME +
                           r'(?:\s*(?:,\s*(?:(?:and|or|und|oder)\s+)?|;\s*|\s+(?:and|or|und|oder)\s+)'
                           r'(?:the\s+)?' + _COUNTRY_NAME + r')*')
_LIST_CONTEXTS = (
    re.compile(r'(?:^|(?<=[.;]))\s*(?:countries|states|member states|recipients|addressees|'
               r'distribution(?: list)?|copy to|cc|Länder|Staaten|Adressaten|Verteiler)\s*:\s*'
               r'(?P<list>[^.\n]+)', re.I),
    re.compile(r'\b(?:list of|listing of|Liste der)\s+(?:countries|states|member states|'
               r'addressees|recipients|Staaten|Länder|Adressaten)\s*'
               r'(?:including|comprising|namely|contains?|:)?\s*(?P<list>[^.\n]+)', re.I),
    re.compile(r'(?:^|(?<=[.;]))\s*(?:(?:this|the|a)\s+)?(?:communication|letter|document|report)\s+'
               r'(?:(?:was|has been|is|were)\s+)?(?:also\s+)?'
               r'(?:sent|addressed|transmitted|circulated|distributed)\s+(?:also\s+)?to\s+'
               r'(?:(?:the following|the)\s+)?(?:(?:countries|states|member states|recipients)\s*:\s*)?'
               r'(?P<list>[^.\n]+)', re.I),
)


def _list_ranges(summary):
    ranges = []
    for context in _LIST_CONTEXTS:
        for match in context.finditer(summary):
            value = match['list'].strip(' ,;:()')
            if _COUNTRY_LIST.fullmatch(value):
                ranges.append(match.span('list'))
    # A summary consisting solely of a country enumeration is not prose about
    # those states. A lone country name is retained, avoiding title heuristics.
    value = summary.strip(' .;()')
    if re.search(r',|;|\s(?:and|or|und|oder)\s', value) and _COUNTRY_LIST.fullmatch(value):
        ranges.append((0, len(summary)))
    return ranges


def summary_match(summary, pattern):
    excluded = _list_ranges(summary)
    return next((match for match in pattern.finditer(summary)
                 if not any(start <= match.start() < end for start, end in excluded)), None)


def clean(value):
    return ' '.join(str(value or '').split())


def listing_date(value):
    """Parse an actual listed date without inventing a day from a year."""
    text = clean(value)
    match = re.search(r'\b(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})\b', text)
    if match and match[2][:3].lower() in MONTHS:
        try:
            return date(int(match[3]), MONTHS[match[2][:3].lower()], int(match[1])).isoformat()
        except ValueError:
            return ''
    match = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', text)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            return ''
    return ''


def download_url(link, source_url, reply=False):
    value = urljoin(source_url, link.get('href', '').strip())
    parts = urlsplit(value)
    expected = '/tmresultsbase/downloadfile' if reply else '/tmresultsbase/downloadpubliccommunicationfile'
    query = {key.lower(): val for key, val in parse_qs(parts.query).items()}
    if (parts.scheme not in ('https', 'http') or parts.hostname != 'spcommreports.ohchr.org'
            or parts.path.lower().rstrip('/') != expected
            or not re.fullmatch(r'\d+', query.get('gid', [''])[0])):
        return ''
    return value


def explicit_language(link):
    """Only per-document language annotations count, never page lang='en'."""
    aliases = {'en': 'en', 'eng': 'en', 'english': 'en', 'englisch': 'en',
               'de': 'de', 'deu': 'de', 'ger': 'de', 'german': 'de', 'deutsch': 'de',
               'fr': 'fr', 'fra': 'fr', 'french': 'fr', 'français': 'fr',
               'es': 'es', 'spa': 'es', 'spanish': 'es'}
    for field in ('hreflang', 'data-language', 'lang'):
        value = clean(link.get(field)).lower()
        if value in aliases:
            return aliases[value]
    text = clean(link.text_content())
    for label in ('English', 'German', 'Deutsch', 'French', 'Spanish'):
        if re.search(r'\b' + label + r'\b', text, re.I):
            return aliases[label.lower()]
    return ''


def short_summary(summary, limit=220):
    if len(summary) <= limit:
        return summary
    return summary[:limit + 1].rsplit(' ', 1)[0].rstrip(' ,;:') + ' …'


def summary_evidence(summary, pattern):
    match = summary_match(summary, pattern)
    if not match:
        return ''
    # Evidence is a verbatim excerpt of the source, not a generated conclusion.
    start, end = max(0, match.start() - 120), min(len(summary), match.end() + 200)
    excerpt = summary[start:end]
    return ('…' if start else '') + excerpt + ('…' if end < len(summary) else '')


def parse_communications(body: bytes, source_url: str) -> list[dict]:
    """Return relevant communications and separately attributed reply records.

    DEU/ISR addressees qualify directly. Other addressees qualify only where
    Germany or Israel occurs in the substantive summary. Palestine-only rows
    intentionally do not broaden this particular parser's remit.
    """
    tree = html.fromstring(body)
    tables = tree.xpath('//table[@id="table-results"]')
    if not tables:
        return []
    records = {}
    for row in tables[0].xpath('.//tr'):
        cells = row.xpath('./td')
        if len(cells) < 4:
            continue
        links = [link for link in cells[0].xpath('.//a[@href]') if download_url(link, source_url)]
        if not links:
            continue
        summary = clean(cells[2].text_content())
        summary = re.split(r'Alleged victims\s*:|More details\.{0,3}', summary, maxsplit=1, flags=re.I)[0].strip()
        recipient_blocks = cells[0].xpath('./ul')
        recipient = clean(recipient_blocks[1].text_content()) if len(recipient_blocks) > 1 else ''
        sent_date = listing_date(clean(recipient_blocks[0].text_content()) if recipient_blocks else cells[0].text_content())
        communication_type = clean(recipient_blocks[2].text_content()) if len(recipient_blocks) > 2 else ''
        for link in links:
            symbol = clean(link.text_content())
            code_match = re.match(r'^([A-Z]{3})\s+\d+/\d{4}\b', symbol)
            if not code_match:
                continue
            code = code_match[1]
            relations = {key: bool(summary_match(summary, pattern)) for key, pattern in TOPICS.items()}
            reasons = {key: ['Sachverhalt laut Quellenzusammenfassung: „' + summary_evidence(summary, pattern) + '“']
                       for key, pattern in TOPICS.items() if relations[key]}
            if code in COUNTRIES:
                key, country = COUNTRIES[code]
                relations[key] = True
                reasons.setdefault(key, []).insert(0, f'Adressat laut OHCHR: {recipient or country}; Kennung {symbol}.')
            if not (relations['germany'] or relations['israel']):
                continue
            title = symbol + (' · ' + short_summary(summary) if summary else '')
            base = {
                'url': source_url, 'title': title, 'date': sent_date, 'symbol': symbol,
                'body': 'OHCHR · Sonderverfahren', 'author': 'UN-Sonderberichterstatter / Arbeitsgruppen',
                'kind': 'Mitteilung der UN-Sonderverfahren', 'language': explicit_language(link),
                'pdf_url': download_url(link, source_url), 'relations': relations,
                'relation_reasons': reasons, 'summary': summary, 'recipient': recipient,
                'communication_type': communication_type, 'source_kind': 'OHCHR-Sonderverfahren',
                'tags': [LABELS[key] for key, applies in relations.items() if applies],
            }
            if not base['language']:
                base['language_note'] = 'Dokumentsprache in der Ergebnisliste nicht ausgewiesen; am PDF zu prüfen.'
            records.setdefault(base['pdf_url'], base)
            for reply in cells[3].xpath('.//a[@href]'):
                reply_url = download_url(reply, source_url, reply=True)
                if not reply_url:
                    continue
                reply_date = listing_date(reply.text_content())
                acknowledgement = bool(re.search(r'\bAck\.', reply.text_content(), re.I))
                reply_author = COUNTRIES[code][1] if code in COUNTRIES else recipient
                kind = ('Eingangsbestätigung' if acknowledgement else 'Antwort')
                kind += ' des Adressaten' if code == 'OTH' else ' eines Mitgliedstaats'
                record = dict(base, pdf_url=reply_url, date=reply_date, author=reply_author,
                              kind=kind, language=explicit_language(reply),
                              title=symbol + ' · ' + ('Eingangsbestätigung' if acknowledgement else 'Antwort')
                                    + (' vom ' + reply_date if reply_date else '')
                                    + (' · ' + short_summary(summary) if summary else ''),
                              parent_pdf_url=base['pdf_url'], reply_label=clean(reply.text_content()))
                record.pop('language_note', None)
                if not record['language']:
                    record['language_note'] = 'Sprache der Antwort in der Ergebnisliste nicht ausgewiesen; am PDF zu prüfen.'
                records.setdefault(reply_url, record)
    return list(records.values())
