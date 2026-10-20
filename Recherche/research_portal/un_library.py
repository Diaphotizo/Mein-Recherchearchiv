"""Independent UN collection using the portal's common full-text reader."""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path

from .paths import BASE
from .courts import (CourtLibrary, TOPIC_GROUPS, fts_query, fts_groups,
                     json_read, snippet, valid_date, valid_checked_at)

UN_ROOT = BASE / 'UN'
UN_DATA = UN_ROOT / 'data'
INDEX_NAME = 'un_index.sqlite3'
RELATION_KEYS = ('germany', 'israel', 'palestine')


def languages_for(record):
    """A bilingual PDF participates in both language filters, but counts once."""
    values = record.get('languages') or []
    if isinstance(values, str):
        values = [values]
    values = list(values) + [record.get('language') or '']
    result = set()
    aliases = {'english': 'en', 'englisch': 'en', 'eng': 'en',
               'german': 'de', 'deutsch': 'de', 'ger': 'de', 'deu': 'de'}
    for value in values:
        value = str(value).strip().casefold()
        value = aliases.get(value, value)
        if value in ('en', 'de'):
            result.add(value)
        elif value in ('en-de', 'de-en', 'english / german'):
            result.update(('en', 'de'))
    return sorted(result)


def relations_for(value):
    """Country relations are source evidence, never incidental full-text mentions."""
    value = value if isinstance(value, dict) else {}
    result = {key: value.get(key) is True or value.get(key) == 1 for key in RELATION_KEYS}
    result['both'] = result['germany'] and result['israel']
    result['either'] = result['germany'] or result['israel']
    return result


def safe_path(value, root=UN_ROOT, kind='PDF'):
    if kind not in ('PDF', 'TXT'):
        raise ValueError('Unbekanntes Dateiformat.')
    allowed = (Path(root) / kind).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    if path.suffix.lower() != '.' + kind.lower():
        raise ValueError(f'Nur {kind}-Dateien sind erlaubt.')
    try:
        path.relative_to(allowed)
    except ValueError:
        raise ValueError('Dateipfad liegt außerhalb der UN-Sammlung.') from None
    return path


class UNLibrary(CourtLibrary):
    """Shares reader/annotation behavior, but owns every catalog and archive path."""
    def __init__(self, data=None, root=None):
        self.root = Path(root or UN_ROOT).resolve()
        self.data = Path(data or self.root / 'data').resolve()
        self.index = self.data / INDEX_NAME
        self.annotations = self.data / 'annotations.json'

    def state(self):
        catalog = json_read(self.data / 'catalog.json', {}) or {}
        connection = self._connect()
        meta = {}
        if connection is None:
            items = []
        else:
            try:
                items = self._all(connection)
                meta = {r['key']: json.loads(r['value']) for r in connection.execute('SELECT key,value FROM meta')}
            finally:
                connection.close()
        relations = [relations_for(i.get('relations')) for i in items]
        countries = {key: sum(r[key] for r in relations) for key in (*RELATION_KEYS, 'both', 'either')}
        bodies = Counter(i.get('body', '') for i in items if i.get('body'))
        kinds = Counter(i['kind'] for i in items)
        languages = Counter(code for item in items for code in languages_for(item))
        tags = Counter(t for i in items for t in i['tags'])
        dates = sorted(i['date'] for i in items if i['date'])
        return {
            'total': len(items), 'countries': countries,
            'bodies': [{'name': name, 'count': count} for name, count in sorted(bodies.items())],
            'kinds': dict(sorted(kinds.items())), 'languages': dict(sorted(languages.items())),
            'tags': [{'label': tag, 'group': TOPIC_GROUPS.get(tag, 'Weitere Schlagwörter'), 'count': count}
                     for tag, count in sorted(tags.items())],
            'total_pages': sum(i['pages'] for i in items),
            'text_pages': sum(i['text_pages'] for i in items),
            'no_text_pages': sum(i.get('no_text_pages', 0) for i in items),
            'indexed_docs': sum(i['text_pages'] > 0 for i in items),
            'missing_docs': sum(i['text_status'] == 'missing' for i in items),
            'error_docs': sum(i['text_status'] == 'error' for i in items),
            'date_from': dates[0] if dates else '', 'date_to': dates[-1] if dates else '',
            'built_at': meta.get('built_at'),
            'source_checked_at': valid_checked_at(catalog.get('checked_at')) or valid_checked_at(meta.get('source_checked_at')),
            'last_source_attempt_at': valid_checked_at(catalog.get('last_attempt_at')),
            'source_check_partial': bool(catalog.get('errors') or catalog.get('index_pending')),
            'coverage_notes': catalog.get('coverage_notes', meta.get('coverage_notes', [])),
            'index_status': json_read(self.data / 'index_status.json', {}),
            'update_status': json_read(self.data / 'update_status.json', {}),
            'ready': connection is not None,
        }

    def search(self, params):
        def values(*keys):
            result = []
            for key in keys:
                value = params.get(key, [])
                result.extend(value if isinstance(value, list) else [value])
            return [str(v) for v in result if v is not None and str(v)]

        def first(*keys, default=''):
            result = values(*keys)
            return result[0] if result else default

        match, terms = fts_query(first('q'))
        groups = fts_groups(first('q'))
        connection = self._connect()
        if connection is None:
            return {'total': 0, 'page': 1, 'pages': 0, 'items': [], 'highlight_terms': terms, 'ready': False}
        try:
            items = self._all(connection)
            matches = {}
            if match:
                allowed = None
                for group in groups:
                    found = set()
                    for row in connection.execute("SELECT doc_id,page,snippet(pages_fts,2,'','','…',55) AS excerpt FROM pages_fts WHERE pages_fts MATCH ? ORDER BY rank", (group,)):
                        ident = row['doc_id']
                        found.add(ident)
                        matches.setdefault(ident, [])
                        if len(matches[ident]) < 2 and not any(s['page'] == row['page'] for s in matches[ident]):
                            matches[ident].append({'page': row['page'], 'text': snippet(row['excerpt'], terms)})
                    for row in connection.execute('SELECT doc_id FROM metadata_fts WHERE metadata_fts MATCH ?', (group,)):
                        found.add(row['doc_id'])
                        matches.setdefault(row['doc_id'], [])
                    allowed = found if allowed is None else allowed & found
                matches = {ident: excerpts for ident, excerpts in matches.items() if ident in allowed}
            requested_tags = set(values('tag', 'tags'))
            date_from = valid_date(first('from', 'date_from'))
            date_to = valid_date(first('to', 'date_to'))
            relation = first('relation', default='all')
            if relation not in ('all', 'germany', 'israel', 'both', 'either', 'palestine'):
                raise ValueError('Unbekannter Länderbezug.')
            selected = []
            for item in items:
                if match and item['id'] not in matches:
                    continue
                item['relations'] = relations_for(item.get('relations'))
                if relation != 'all' and not item['relations'][relation]:
                    continue
                if any(first(key) and item.get(key) != first(key) for key in ('body', 'kind')):
                    continue
                requested_language = first('language')
                document_languages = languages_for(item)
                if requested_language and not (requested_language in document_languages
                        or requested_language == 'en-de' and len(document_languages) == 2):
                    continue
                if requested_tags and not requested_tags.issubset(item['tags']):
                    continue
                if date_from and (not item['date'] or item['date'] < date_from):
                    continue
                if date_to and (not item['date'] or item['date'] > date_to):
                    continue
                availability = first('text_status')
                if availability in ('searchable', 'with_text') and not item['text_pages']:
                    continue
                if availability in ('no_text', 'without_text') and item['text_pages']:
                    continue
                item['snippets'] = matches.get(item['id'], [])
                selected.append(item)
            sort = first('sort', default='newest')
            if sort == 'oldest':
                selected.sort(key=lambda i: (not bool(i['date']), i['date'], i['title']))
            elif sort in ('title', 'alphabetical'):
                selected.sort(key=lambda i: i['title'].casefold())
            elif sort in ('relevance', 'relevant') and match:
                order = {ident: n for n, ident in enumerate(matches)}
                selected.sort(key=lambda i: order.get(i['id'], 10**9))
            else:
                selected.sort(key=lambda i: (i['date'], i['title']), reverse=True)
            total = len(selected)
            pages = math.ceil(total / 20)
            try:
                page = max(1, min(int(first('page', default='1')), max(1, pages)))
            except ValueError:
                page = 1
            selected = selected[(page - 1) * 20:page * 20]
            for item in selected:
                if not item['snippets']:
                    row = connection.execute('SELECT page,text FROM pages WHERE doc_id=? AND length(text)>0 ORDER BY page LIMIT 1', (item['id'],)).fetchone()
                    if row:
                        item['snippets'] = [{'page': row['page'], 'text': snippet(row['text'], terms)}]
                for key in ('file_path', 'txt_path', 'tag_evidence', 'empty_pages'):
                    item.pop(key, None)
            return {'total': total, 'page': page, 'pages': pages, 'items': selected, 'highlight_terms': terms, 'ready': True}
        finally:
            connection.close()

    def get(self, ident, page=1, q=''):
        value = super().get(ident, page, q)
        value['document'].pop('txt_path', None)
        value['document']['relations'] = relations_for(value['document'].get('relations'))
        return value

    def pdf_path(self, ident):
        connection = self._connect()
        if connection is None:
            raise KeyError(ident)
        try:
            row = connection.execute('SELECT metadata FROM documents WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            path = safe_path(json.loads(row[0])['file_path'], self.root)
            if not path.is_file():
                raise FileNotFoundError(path)
            return path
        finally:
            connection.close()

    def txt_path(self, ident):
        # Only a known document ID can address a text file.
        self.get(ident)
        path = safe_path(self.root / 'TXT' / (ident + '.txt'), self.root, 'TXT')
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
