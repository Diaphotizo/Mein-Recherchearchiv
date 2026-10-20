"""Build the independent UN full-text index and page-labelled text exports."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .courts import TOPICS, atomic_json, json_read, valid_date
from .index_courts import CACHE_VERSION, extract_job, make_schema, now, string_value
from .un_library import INDEX_NAME, UN_ROOT, relations_for, safe_path, languages_for
from .storage import require_indexed_ids

GERMAN_TOPICS = {
    'Deutschland': r'\bdeutsch(?:land|e[nsrm]?)\b|\bbundesrepublik\b',
    'Israel': r'\bisrael(?:isch\w*)?\b', 'Palästina': r'\bpalästin\w*\b',
    'Westbank': r'\bwestjordanland\b', 'Ostjerusalem': r'\bostjerusalem\b',
    'Vereinte Nationen': r'\bvereinte[nr]? nationen\b', 'Völkermord': r'\bvölkermord\b|\bgenozid\b',
    'Folter': r'\bfolter\w*\b', 'Kriegsverbrechen': r'\bkriegsverbrechen\b',
    'Verbrechen gegen die Menschlichkeit': r'\bverbrechen gegen die menschlichkeit\b',
    'Vertreibung': r'\bvertreib\w*\b', 'Besatzung': r'\bbesatz\w*\b|\bbesetzte[nrsm]? gebiet\w*\b',
    'Siedlungen': r'\bsiedlung\w*\b', 'Humanitäre Hilfe': r'\bhumanitäre[rns]? hilfe\b',
    'Gesundheit': r'\bgesundheit\w*\b|\bkrankenh(?:aus|äuser)\b',
    'Kinder': r'\bkinder\w*\b', 'Frauen': r'\bfrauen\b', 'Flüchtlinge': r'\bflüchtling\w*\b',
    'Wirtschaft': r'\bwirtschaft\w*\b', 'Waffenlieferungen': r'\bwaffenliefer\w*\b|\brüstungsexport\w*\b',
    'Waffenstillstand': r'\bwaffenstillstand\w*\b', 'Selbstbestimmung': r'\bselbstbestimmung\w*\b',
}


def normalize_language(value):
    value = string_value(value).strip().casefold()
    aliases = {'eng': 'en', 'english': 'en', 'englisch': 'en', 'deu': 'de', 'ger': 'de',
               'german': 'de', 'deutsch': 'de', 'english / german': 'en-de'}
    value = aliases.get(value, value)
    if value not in ('en', 'de', 'en-de'):
        raise ValueError(f'Nicht unterstützte UN-Dokumentsprache: {value or "unbekannt"}')
    return value


def string_values(value):
    if not isinstance(value, list):
        value = [value]
    return list(dict.fromkeys(string_value(item).strip() for item in value if string_value(item).strip()))


def record_metadata(record, root=UN_ROOT):
    path = safe_path(record.get('file_path') or record.get('file') or '', root)
    checksum = str(record.get('sha256') or record.get('id') or '').lower()
    if not re.fullmatch(r'[a-f0-9]{64}', checksum):
        with path.open('rb') as source:
            checksum = hashlib.file_digest(source, 'sha256').hexdigest()
    reasons = record.get('relation_reasons') or {}
    if not isinstance(reasons, dict):
        reasons = {}
    reasons = {key: [str(v) for v in (value if isinstance(value, list) else [value])]
               for key, value in reasons.items() if key in ('germany', 'israel', 'palestine')}
    language_values = string_values(record.get('languages')) + string_values(record.get('language') or record.get('language_code'))
    if not language_values:
        normalize_language('')
    for value in language_values:
        normalize_language(value)
    languages = languages_for({'languages': language_values})
    metadata = {
        'id': checksum, 'sha256': checksum, 'file_path': str(path),
        'title': string_value(record.get('title') or record.get('symbol') or path.stem),
        'date': valid_date(record.get('date') or record.get('publication_date')),
        'publication_year': str(record.get('publication_year') or '') if re.fullmatch(r'[12]\d{3}', str(record.get('publication_year') or '')) else '',
        'original_date': str(record.get('date') or ''),
        'symbol': string_value(record.get('symbol') or record.get('document_id')),
        'body': string_value(record.get('body') or record.get('organ') or record.get('source')),
        'author': string_value(record.get('author') or record.get('authors')),
        'kind': string_value(record.get('kind') or record.get('document_type') or 'UN-Dokument'),
        'language': 'en-de' if len(languages) == 2 else languages[0], 'languages': languages,
        'url': string_value(record.get('url') or record.get('record_url') or record.get('source_url')),
        'pdf_url': string_value(record.get('pdf_url') or record.get('url')),
        'pages': int(record.get('pages') or 0), 'bytes': int(record.get('bytes') or 0),
        'relations': relations_for(record.get('relations')), 'relation_reasons': reasons,
        'tags': list(dict.fromkeys(str(t) for t in record.get('tags', []) if str(t))),
        'source_checked_at': record.get('checked_at'), 'acquisition_note': record.get('acquisition_note', ''),
    }
    for field, singular in (('pdf_urls', 'pdf_url'), ('source_urls', 'url'), ('titles', 'title'), ('symbols', 'symbol')):
        metadata[field] = string_values([metadata[singular]] + string_values(record.get(field)))
    return metadata


def metadata_search_text(metadata):
    keys = ('title', 'symbol', 'body', 'author', 'kind', 'language', 'url', 'pdf_url')
    filename = str(metadata.get('file_path') or '').replace('\\', '/').rsplit('/', 1)[-1]
    aliases = [value for field in ('pdf_urls', 'source_urls', 'titles', 'symbols', 'languages')
               for value in string_values(metadata.get(field))]
    return ' '.join([string_value(metadata.get(key)) for key in keys] + aliases + [filename] + metadata.get('tags', []))


def add_document(connection, metadata, extracted):
    texts = extracted.get('pages', [])
    errors = extracted.get('errors', [])
    if texts:
        metadata['pages'] = len(texts)
    empty = [n for n, text in enumerate(texts, 1) if not re.search(r'\w', text)]
    text_pages = len(texts) - len(empty)
    evidence = {}
    compiled = [(label, re.compile(pattern + ('|' + GERMAN_TOPICS[label] if label in GERMAN_TOPICS else ''), re.I))
                for label, _, pattern in TOPICS]
    for number, text in enumerate(texts, 1):
        for label, pattern in compiled:
            if pattern.search(text):
                evidence.setdefault(label, []).append(number)
    metadata.update({'text_pages': text_pages, 'no_text_pages': len(empty), 'empty_pages': empty,
                     'extraction_errors': errors, 'tag_evidence': evidence,
                     'tags': list(dict.fromkeys(metadata.get('tags', []) + list(evidence)))})
    if extracted.get('missing'):
        metadata['text_status'] = 'missing'
    elif extracted.get('error'):
        metadata['text_status'] = 'error'
    elif errors:
        metadata['text_status'] = 'partial' if text_pages else 'error'
    elif not text_pages:
        metadata['text_status'] = 'none'
    elif empty:
        metadata['text_status'] = 'partial'
    else:
        metadata['text_status'] = 'complete'
    metadata['text_error'] = extracted.get('error', '')
    connection.execute('INSERT INTO documents VALUES (?,?)', (metadata['id'], json.dumps(metadata, ensure_ascii=False)))
    rows = [(metadata['id'], n, text) for n, text in enumerate(texts, 1)]
    connection.executemany('INSERT INTO pages VALUES (?,?,?)', rows)
    connection.executemany('INSERT INTO pages_fts(doc_id,page,text) VALUES (?,?,?)', rows)
    connection.execute('INSERT INTO metadata_fts(doc_id,text) VALUES (?,?)', (metadata['id'], metadata_search_text(metadata)))
    return metadata


def write_text(root, metadata, pages):
    path = safe_path(Path(root) / 'TXT' / (metadata['id'] + '.txt'), root, 'TXT')
    path.parent.mkdir(parents=True, exist_ok=True)
    text = '\n'.join([metadata['title'], metadata['symbol'], metadata['date'],
                      f"Sprache: {metadata['language']}", f"Quelle: {metadata['url']}", f"PDF: {metadata['pdf_url']}"])
    for number, content in enumerate(pages, 1):
        text += f'\n\n--- Seite {number} ---\n\n{content or "[Kein auslesbarer Text auf dieser Seite.]"}'
    text += '\n'
    if path.is_file() and path.read_text(encoding='utf-8') == text:
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    try:
        replace_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def replace_file(source, destination):
    for attempt in range(30):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(.2)


def build_index(workers=3, root=None):
    root = Path(root or UN_ROOT).resolve()
    data = root / 'data'
    data.mkdir(parents=True, exist_ok=True)
    lock = open(data / 'index.lock', 'a+b')
    if os.name == 'nt':
        import msvcrt
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock.close()
            raise RuntimeError('Der UN-Index wird bereits aktualisiert.')
    stamp = now()
    temporary = data / ('un_index.' + uuid.uuid4().hex + '.building.sqlite3')
    connection = None

    def progress(phase, message, **fields):
        try:
            atomic_json(data / 'index_status.json', {'phase': phase, 'message': message, 'started_at': stamp, 'time': now(), **fields})
        except PermissionError:
            if phase not in ('running', 'validating'):
                raise
        print(message, flush=True)

    try:
        catalog = json_read(data / 'catalog.json', {'documents': []})
        if not isinstance(catalog, dict) or not isinstance(catalog.get('documents'), list):
            raise ValueError('Der UN-Quellenkatalog fehlt oder ist ungültig.')
        if not catalog['documents']:
            require_indexed_ids(data / INDEX_NAME, [])
            if any((root / 'PDF').rglob('*.pdf')):
                raise RuntimeError('PDF-Bestand ohne UN-Quellenkatalog; vorhandene Suche bleibt erhalten.')
        metadata = {}
        for record in catalog['documents']:
            item = record_metadata(record, root)
            # Identical PDFs can be registered by several UN sources.
            if item['id'] in metadata:
                old = metadata[item['id']]
                old['relations'] = relations_for({key: old['relations'][key] or item['relations'][key]
                                                  for key in ('germany', 'israel', 'palestine')})
                for key, reasons in item['relation_reasons'].items():
                    old['relation_reasons'][key] = list(dict.fromkeys(old['relation_reasons'].get(key, []) + reasons))
                old['tags'] = list(dict.fromkeys(old['tags'] + item['tags']))
                for field in ('pdf_urls', 'source_urls', 'titles', 'symbols', 'languages'):
                    old[field] = list(dict.fromkeys(old.get(field, []) + item.get(field, [])))
                old['languages'] = languages_for(old)
                old['language'] = 'en-de' if len(old['languages']) == 2 else old['languages'][0]
            else:
                metadata[item['id']] = item
        connection = sqlite3.connect(temporary)
        make_schema(connection)
        jobs = [{'id': item['id'], 'path': item['file_path'], 'cache': str(data / 'text_cache' / (item['id'] + '.json.gz'))}
                for item in metadata.values()]
        total = len(jobs)
        done = pages = text_pages = reused = 0
        progress('running', f'UN-Volltexte werden erfasst: 0/{total} Dokumente', processed=0, total=total)
        with ProcessPoolExecutor(max_workers=max(1, min(int(workers), 3))) as pool:
            futures = {pool.submit(extract_job, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                result = future.result()
                if result.get('cache'):
                    with gzip.open(result['cache'], 'rt', encoding='utf-8') as source:
                        extracted = json.load(source)
                else:
                    extracted = result
                # Never replace a usable index with an unreadable or missing PDF.
                # Scans without a text layer remain valid and explicitly marked.
                if extracted.get('error') or extracted.get('missing') or extracted.get('errors'):
                    raise RuntimeError(f"UN-PDF {job['id'][:12]} konnte nicht vollständig gelesen werden: " + str(extracted.get('error') or extracted.get('errors') or 'Datei fehlt'))
                item = add_document(connection, metadata[job['id']], extracted)
                write_text(root, item, extracted.get('pages', []))
                done += 1
                pages += item['pages']
                text_pages += item['text_pages']
                reused += int(result.get('reused', False))
                if done % 20 == 0 or done == total:
                    progress('running', f'{done}/{total} UN-Dokumente · {pages:,} Seiten verarbeitet',
                             processed=done, total=total, pages=pages, text_pages=text_pages, reused=reused)
        meta = {'built_at': now(), 'source_checked_at': catalog.get('checked_at'),
                'coverage_notes': catalog.get('coverage_notes', []), 'schema': 1, 'cache_version': CACHE_VERSION}
        connection.executemany('INSERT INTO meta VALUES (?,?)', [(key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()])
        connection.commit()
        progress('validating', 'UN-Volltextindex wird geprüft …', processed=done, total=total, pages=pages, text_pages=text_pages)
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Die UN-Indexprüfung ist fehlgeschlagen.')
        if connection.execute('SELECT count(*) FROM documents').fetchone()[0] != total:
            raise RuntimeError('Die UN-Dokumentzahl stimmt nicht.')
        connection.execute("INSERT INTO pages_fts(pages_fts) VALUES ('optimize')")
        connection.commit()
        connection.close()
        connection = None
        replace_file(temporary, data / INDEX_NAME)
        summary = {'total': total, 'pages': pages, 'text_pages': text_pages, 'failed': 0, 'reused': reused, 'built_at': meta['built_at']}
        progress('complete', f'{total} UN-Dokumente mit {pages:,} Seiten aufbereitet.', processed=done, **summary)
        return summary
    except Exception as exc:
        progress('error', str(exc))
        raise
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    build_index(args.workers)
