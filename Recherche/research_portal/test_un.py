"""UN scope, document search, personal annotations and archive isolation."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from .paths import PORTAL
from .un_library import UNLibrary, INDEX_NAME, relations_for, safe_path
from .index_un import add_document, build_index, make_schema, normalize_language, record_metadata


class UNTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='un-backend-test-', dir=PORTAL)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        (self.root / 'PDF').mkdir()
        (self.root / 'TXT').mkdir()
        self.ids = [letter * 64 for letter in 'abcd']
        self.build_fixture()
        self.library = UNLibrary(root=self.root)

    def tearDown(self):
        self.root.resolve().relative_to(PORTAL.resolve())
        for attempt in range(8):
            try:
                self.temp.cleanup()
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.15 * (attempt + 1))

    def build_fixture(self):
        destination = self.data / INDEX_NAME
        if destination.exists():
            destination.unlink()
        connection = sqlite3.connect(destination)
        make_schema(connection)
        fixtures = [
            ({'germany': True}, 'de', '2025-02-01', ['Bericht über Deutschland. Folter und Menschenrechte.']),
            ({'israel': True, 'palestine': True}, 'en', '2025-03-01', ['Humanitarian aid in Gaza.', 'Germany is mentioned as a participant, not a subject.']),
            ({'germany': True, 'israel': True}, 'en', '2025-01-01', ['German submission concerning Israel.']),
            ({'palestine': True}, 'en', '', ['']),
        ]
        for number, (relations, language, date, pages) in enumerate(fixtures):
            item = {
                'id': self.ids[number], 'title': f'UN document {number + 1}', 'date': date,
                'symbol': 'A/RES/79/1' if number < 2 else f'TEST/{number}',
                'body': 'General Assembly' if number < 3 else 'UNRWA',
                'author': 'Germany' if number == 2 else 'United Nations', 'kind': 'Bericht',
                'language': language, 'pages': len(pages), 'relations': relations_for(relations),
                'relation_reasons': {key: ['Explizites Sachthema'] for key, value in relations.items() if value},
                'url': f'https://www.un.org/record/{number + 1}',
                'pdf_url': f'https://www.un.org/archive/{12300 + number}.pdf',
                'file_path': str(self.root / 'PDF' / f'{12300 + number}.pdf'), 'tags': [],
            }
            (self.root / 'PDF' / f'{12300 + number}.pdf').write_bytes(b'%PDF-1.7 fixture')
            add_document(connection, item, {'pages': pages})
        connection.execute('INSERT INTO meta VALUES (?,?)', ('built_at', json.dumps('2026-10-13')))
        connection.commit()
        connection.close()

    def test_four_country_modes_and_additional_topic(self):
        expected = {'germany': {'a', 'c'}, 'israel': {'b', 'c'}, 'both': {'c'},
                    'either': {'a', 'b', 'c'}, 'palestine': {'b', 'd'}, 'all': {'a', 'b', 'c', 'd'}}
        for relation, letters in expected.items():
            with self.subTest(relation=relation):
                found = self.library.search({'relation': [relation]})
                self.assertEqual({item['id'][0] for item in found['items']}, letters)
        state = self.library.state()
        self.assertEqual(state['countries'], {'germany': 2, 'israel': 2, 'both': 1, 'either': 3, 'palestine': 2})
        # Incidental Germany full-text occurrence is searchable, but does not
        # turn an Israel document into a substantive Germany document.
        self.assertEqual(self.library.search({'q': ['participant'], 'relation': ['germany']})['total'], 0)

    def test_language_variants_share_symbol_but_remain_separate(self):
        self.assertEqual(self.library.search({'q': ['A/RES/79/1']})['total'], 2)
        german = self.library.search({'q': ['A/RES/79/1'], 'language': ['de']})
        self.assertEqual([i['id'] for i in german['items']], [self.ids[0]])
        self.assertEqual(self.library.state()['languages'], {'de': 1, 'en': 3})
        self.assertEqual(normalize_language('English'), 'en')
        self.assertEqual(normalize_language('Deutsch'), 'de')
        with self.assertRaises(ValueError):
            normalize_language('French')

    def test_url_filename_and_phrases_respect_scope_filters(self):
        for query in ('12301.pdf', 'https://www.un.org/archive/12301.pdf', '"humanitarian aid"', 'Gaza participant'):
            with self.subTest(query=query):
                found = self.library.search({'q': [query], 'relation': ['israel']})
                self.assertEqual([i['id'] for i in found['items']], [self.ids[1]])
                self.assertEqual(self.library.search({'q': [query], 'relation': ['both']})['total'], 0)
        self.assertEqual(self.library.search({'q': ['"aid humanitarian"']})['total'], 0)
        document = self.library.get(self.ids[1], q='Gaza participant')
        self.assertEqual([row['page'] for row in document['matches']], [1, 2])

    def test_filters_and_tags_combine(self):
        found = self.library.search({'relation': ['either'], 'body': ['General Assembly'], 'kind': ['Bericht'],
                                     'language': ['en'], 'from': ['2025-02-01'], 'tag': ['Gaza', 'Humanitäre Hilfe']})
        self.assertEqual([i['id'] for i in found['items']], [self.ids[1]])
        german = self.library.search({'tag': ['Folter'], 'language': ['de']})
        self.assertEqual([i['id'] for i in german['items']], [self.ids[0]])

    def test_notes_and_manual_tags_survive_rebuild(self):
        self.library.save(self.ids[1], {'tags': ['Mein UN-Dossier'], 'notes': 'Querverweis für meine Recherche.'})
        before = self.library.annotations.read_bytes()
        self.build_fixture()
        self.assertEqual(self.library.annotations.read_bytes(), before)
        document = self.library.get(self.ids[1])['document']
        self.assertEqual(document['tags'], ['Mein UN-Dossier'])
        self.assertEqual(document['notes'], 'Querverweis für meine Recherche.')
        self.assertIn('Gaza', document['auto_tags'])
        self.assertEqual(self.library.search({'tag': ['Mein UN-Dossier']})['total'], 1)

    def test_pdf_and_text_stay_inside_own_collection(self):
        pdf = self.library.pdf_path(self.ids[0])
        self.assertEqual(pdf.parent, self.root / 'PDF')
        for malicious in (self.root / 'outside.pdf', self.root / 'PDF' / '..' / 'other.pdf', self.root / 'TXT' / 'private.txt'):
            with self.assertRaises(ValueError):
                safe_path(malicious, self.root)
        with self.assertRaises(KeyError):
            self.library.txt_path('../private')
        with closing(sqlite3.connect(self.data / INDEX_NAME)) as connection:
            raw = json.loads(connection.execute('SELECT metadata FROM documents WHERE id=?', (self.ids[0],)).fetchone()[0])
            raw['file_path'] = str(self.root / 'outside.pdf')
            connection.execute('UPDATE documents SET metadata=? WHERE id=?', (json.dumps(raw), self.ids[0]))
            connection.commit()
        with self.assertRaises(ValueError):
            self.library.pdf_path(self.ids[0])

    def test_no_text_and_unknown_dates_remain_explicit(self):
        self.assertEqual(self.library.state()['no_text_pages'], 1)
        self.assertEqual(self.library.search({'text_status': ['no_text']})['total'], 1)
        self.assertEqual(self.library.search({'to': ['2026-01-01']})['total'], 3)
        document = self.library.get(self.ids[3])
        self.assertFalse(document['page']['has_text'])
        self.assertEqual(document['document']['date'], '')

    def test_source_state_distinguishes_latest_partial_from_previous_full_check(self):
        path = self.data / 'catalog.json'
        cases = [
            ({'last_attempt_at': '2026-10-13T12:00:00Z', 'errors': [{'error': 'One source unavailable'}]}, None, True),
            ({'checked_at': '2026-10-01T09:00:00Z', 'last_attempt_at': '2026-10-13T12:00:00Z',
              'errors': [{'error': 'One source unavailable'}]}, '2026-10-01T09:00:00Z', True),
            ({'checked_at': '2026-10-13T12:00:00Z', 'last_attempt_at': '2026-10-13T12:00:00Z',
              'errors': [], 'index_pending': False}, '2026-10-13T12:00:00Z', False),
            ({'checked_at': '2026-10-13T12:00:00Z', 'last_attempt_at': '2026-10-13T12:00:00Z',
              'errors': [], 'index_pending': True}, '2026-10-13T12:00:00Z', True),
        ]
        for catalog, complete_at, partial in cases:
            with self.subTest(catalog=catalog):
                path.write_text(json.dumps(catalog), encoding='utf-8')
                state = self.library.state()
                self.assertEqual(state['last_source_attempt_at'], '2026-10-13T12:00:00Z')
                self.assertEqual(state['source_checked_at'], complete_at)
                self.assertEqual(state['source_check_partial'], partial)
                self.assertEqual(state['total'], 4)
        path.write_text(json.dumps({'last_attempt_at': 'invalid', 'errors': []}), encoding='utf-8')
        self.assertIsNone(self.library.state()['last_source_attempt_at'])

    def test_failed_build_preserves_live_index_and_annotations(self):
        self.library.save(self.ids[0], {'notes': 'Bleibt erhalten.'})
        previous = (self.data / INDEX_NAME).read_bytes()
        annotations = self.library.annotations.read_bytes()
        (self.data / 'catalog.json').write_text(json.dumps({'documents': [{
            'id': 'f' * 64, 'file_path': str(self.root / 'PDF' / 'missing.pdf'),
            'title': 'Unavailable document', 'language': 'en', 'relations': {'israel': True},
        }]}), encoding='utf-8')
        with self.assertRaises(RuntimeError):
            build_index(workers=1, root=self.root)
        self.assertEqual((self.data / INDEX_NAME).read_bytes(), previous)
        self.assertEqual(self.library.annotations.read_bytes(), annotations)
        self.assertEqual(self.library.state()['total'], 4)
        self.assertEqual(json.loads((self.data / 'index_status.json').read_text(encoding='utf-8'))['phase'], 'error')

    def test_successful_build_uses_real_pdf_cache_and_exports_txt(self):
        from reportlab.pdfgen.canvas import Canvas
        pdf = self.root / 'PDF' / 'new.pdf'
        canvas = Canvas(str(pdf))
        canvas.drawString(72, 750, 'Germany and Israel: humanitarian aid in Gaza.')
        canvas.showPage()
        canvas.save()
        ident = hashlib.sha256(pdf.read_bytes()).hexdigest()
        record = {'id': ident, 'file_path': str(pdf), 'title': 'Verified test PDF', 'symbol': 'TEST/1',
                  'language': 'en', 'relations': {'germany': True, 'israel': True}, 'date': '2025-10-01'}
        (self.data / 'catalog.json').write_text(json.dumps({'documents': [record], 'checked_at': '2026-10-13T10:00:00Z'}), encoding='utf-8')
        first = build_index(workers=1, root=self.root)
        self.assertEqual(first['text_pages'], 1)
        self.library.save(ident, {'notes': 'Meine Notiz'})
        second = build_index(workers=1, root=self.root)
        self.assertEqual(second['reused'], 1)
        self.assertEqual(self.library.get(ident)['document']['notes'], 'Meine Notiz')
        text = self.library.txt_path(ident).read_text(encoding='utf-8')
        self.assertIn('--- Seite 1 ---', text)
        self.assertIn('humanitarian aid', text)
        self.assertEqual(self.library.search({'relation': ['both'], 'q': ['Gaza']})['total'], 1)

    def test_identical_pdf_aliases_remain_searchable_in_both_languages(self):
        from reportlab.pdfgen.canvas import Canvas
        pdf = self.root / 'PDF' / 'bilingual.pdf'
        canvas = Canvas(str(pdf))
        canvas.drawString(72, 750, 'Public report / Oeffentlicher Bericht.')
        canvas.showPage()
        canvas.save()
        ident = hashlib.sha256(pdf.read_bytes()).hexdigest()
        first = {'sha256': ident, 'file_path': str(pdf), 'title': 'Primary report',
            'language': 'en', 'languages': ['en', 'de'], 'relations': {'germany': True},
            'pdf_url': 'https://www.un.org/primary.pdf', 'url': 'https://www.un.org/primary-record',
            'pdf_urls': ['https://www.un.org/alias-87510.pdf'],
            'source_urls': ['https://www.un.org/alternate-record-87510'],
            'titles': ['Separate discovered title'], 'symbols': ['ALT/SYMBOL/87510']}
        second = {'sha256': ident, 'file_path': str(pdf), 'title': 'Zweiter Quellenname',
            'language': 'de', 'relations': {'israel': True}, 'symbol': 'DE/SYMBOL/93120',
            'pdf_url': 'https://www.un.org/german-93120.pdf', 'url': 'https://www.un.org/german-record-93120'}
        (self.data / 'catalog.json').write_text(json.dumps({'documents': [first, second]}), encoding='utf-8')
        built = build_index(workers=1, root=self.root)
        self.assertEqual(built['total'], 1)
        self.assertEqual(self.library.state()['languages'], {'de': 1, 'en': 1})
        document = self.library.get(ident)['document']
        self.assertEqual(document['language'], 'en-de')
        self.assertEqual(document['languages'], ['de', 'en'])
        queries = ['alias-87510.pdf', 'https://www.un.org/alias-87510.pdf',
            'alternate-record-87510', '"Separate discovered title"', 'ALT/SYMBOL/87510',
            'german-93120.pdf', 'german-record-93120', '"Zweiter Quellenname"', 'DE/SYMBOL/93120']
        for language in ('en', 'de'):
            for query in queries:
                with self.subTest(language=language, query=query):
                    found = self.library.search({'language': [language], 'q': [query], 'relation': ['both']})
                    self.assertEqual([item['id'] for item in found['items']], [ident])


if __name__ == '__main__':
    unittest.main()
