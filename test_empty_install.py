"""Offline checks for a fresh checkout: no archive, private data or network.

Run from the package root: python -m unittest test_empty_install
"""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'BIP'), str(ROOT / 'Recherche')]

import crawler
from recherche import build as bip_build
from recherche.library import Library
from research_portal.courts import CourtLibrary
from research_portal.un_library import UNLibrary
from research_portal import index_courts, index_un, update_courts, update_un
from research_portal.test_update_un import FakeClient, pdf_bytes, row
from pypdf import PdfReader


class EmptyInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp_base = (ROOT / '.test-tmp').resolve()
        self.temp_base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='research-empty-install-', dir=self.temp_base)
        self.root = Path(self.temp.name).resolve()
        self.root.relative_to(self.temp_base)

    def tearDown(self):
        self.root.relative_to(self.temp_base)
        for attempt in range(8):
            try:
                self.temp.cleanup()
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.15 * (attempt + 1))

    def test_all_collections_start_and_search_without_catalogs_or_directories(self):
        bip = Library(self.root / 'BIP/data/catalog.json')
        courts = CourtLibrary(data=self.root / 'ICC-ICJ/data',
                              source=self.root / 'ICC-ICJ/Bestand',
                              courts=self.root / 'ICC-ICJ')
        un = UNLibrary(root=self.root / 'UN')
        for library in (bip, courts, un):
            with self.subTest(library=type(library).__name__):
                state = library.state()
                self.assertEqual(state['total'], 0)
                self.assertEqual((state['date_from'], state['date_to']), ('', ''))
                self.assertEqual(library.search({'q': ['Gaza']})['items'], [])
        self.assertFalse((self.root / 'BIP/data/catalog.json').exists())
        self.assertTrue(bip.state()['tags'])

    def test_fresh_bip_refresh_generates_searchable_pdf_without_system_fonts(self):
        root = self.root / 'BIP'
        data = root / 'recherche/data'
        library = Library(data / 'catalog.json')
        item = {'id': 1, 'date': '2026-01-02T12:00:00',
                'modified': '2026-01-02T12:00:00', 'type': 'post',
                'link': 'https://bip-jetzt.de/test/', 'slug': 'test', 'author': 1,
                'title': {'rendered': 'Über Gaza und die Wirtschaft'},
                'content': {'rendered': '<p>Eine öffentliche Quelle über Gaza und die Wirtschaft.</p>'}}

        def collected(args):
            source = args.output
            entry = crawler.export_item(item, source, '2026-01-02T12:00:00Z')
            crawler.save_json(source / 'index.json', [entry])
            crawler.save_json(source / 'quellen/posts-1.json', [item])
            crawler.save_json(source / 'crawl_report.json',
                              {'complete': True, 'finished_at': '2026-01-02T12:00:00Z'})
            return 0

        with patch.multiple(bip_build, ROOT=root, DATA=data, CATALOG=data / 'catalog.json'), \
                patch.object(crawler, 'run', side_effect=collected), \
                patch.object(bip_build, 'system_font_families', return_value=[]), \
                redirect_stdout(io.StringIO()):
            for path in bip_build.font_files():
                self.assertTrue(path.is_file())
            bip_build.build(refresh=True)
        state = library.state()
        self.assertEqual(state['total'], 1)
        result = library.search({'q': ['Wirtschaft']})
        self.assertEqual(result['total'], 1)
        article = library.get(1)
        pdf = root / article['pdf']
        text = ''.join(page.extract_text() or '' for page in PdfReader(pdf).pages)
        self.assertIn('öffentliche Quelle', text)
        self.assertIn('Über Gaza', text)
        self.assertTrue(Path(article['source_file']).is_file())

    def test_failed_first_bip_refresh_leaves_empty_library_available(self):
        root = self.root / 'BIP'
        data = root / 'recherche/data'
        library = Library(data / 'catalog.json')
        with patch.multiple(bip_build, ROOT=root, DATA=data, CATALOG=data / 'catalog.json'), \
                patch.object(crawler, 'run', return_value=1), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'Quellabgleich'):
                bip_build.build(refresh=True)
        self.assertEqual(library.state()['total'], 0)
        self.assertEqual(library.search({})['total'], 0)
        self.assertFalse((data / 'catalog.json').exists())

    def court_paths(self, stack):
        courts = self.root / 'ICC-ICJ'
        paths = {'COURTS': courts, 'COURT_DATA': courts / 'data',
                 'COURT_SOURCE': courts / 'Bestand'}
        stack.enter_context(patch.multiple(update_courts, **paths))
        stack.enter_context(patch.multiple(index_courts, **paths))
        return courts

    def test_empty_indexes_are_valid_without_seed_catalogs(self):
        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            courts = self.court_paths(stack)
            court_state = index_courts.build_index(workers=1)
            un_root = self.root / 'UN'
            un_state = index_un.build_index(workers=1, root=un_root)
        self.assertEqual(court_state['total'], 0)
        self.assertEqual(un_state['total'], 0)
        self.assertTrue(CourtLibrary(courts / 'data', courts / 'Bestand', courts).state()['ready'])
        self.assertTrue(UNLibrary(root=un_root).state()['ready'])

    def test_fresh_courts_update_reaches_registers_and_indexes_first_pdf(self):
        content = pdf_bytes('The public court document describes humanitarian aid in Gaza.')
        sha = hashlib.sha256(content).hexdigest()
        document = {'court': 'ICJ', 'case_id': '192', 'title': 'Public decision on Gaza',
                    'document_id': 'TEST/192/1', 'date': '2026-01-02', 'language': 'en',
                    'url': 'https://www.icj-cij.org/sites/default/files/test-en.pdf'}
        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            courts = self.court_paths(stack)
            destination = courts / 'PDF' / (sha + '.pdf')
            saved = {**document, 'file_path': str(destination), 'sha256': sha,
                     'bytes': len(content), 'pages': 1}
            def downloaded(*unused):
                destination.parent.mkdir(parents=True)
                destination.write_bytes(content)
                return saved
            stack.enter_context(patch.object(update_courts, 'collect_icj',
                                            return_value=([document], [{'status': 'ok'}], [], [], [])))
            stack.enter_context(patch.object(update_courts, 'collect_lt',
                                            return_value=([], [{'status': 'ok'}], [], [], [])))
            stack.enter_context(patch.object(update_courts, 'download_pdf', side_effect=downloaded))
            result = update_courts.run_update(client=Mock(),
                                              rebuild=lambda: index_courts.build_index(workers=1))
        self.assertEqual(result['phase'], 'complete')
        self.assertEqual(result['new'], 1)
        self.assertFalse((courts / 'Bestand/Verzeichnis.json').exists())
        library = CourtLibrary(courts / 'data', courts / 'Bestand', courts)
        self.assertEqual(library.search({'q': ['"humanitarian aid"']})['total'], 1)

    def test_failed_first_courts_update_keeps_empty_search_available(self):
        failed = ([], [], [{'error': 'Source unavailable'}], [], [])
        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            courts = self.court_paths(stack)
            stack.enter_context(patch.object(update_courts, 'collect_icj', return_value=failed))
            stack.enter_context(patch.object(update_courts, 'collect_lt', return_value=failed))
            result = update_courts.run_update(client=Mock(), rebuild=Mock())
        self.assertEqual(result['phase'], 'error')
        library = CourtLibrary(courts / 'data', courts / 'Bestand', courts)
        self.assertEqual(library.state()['total'], 0)
        self.assertEqual(library.search({})['total'], 0)

    def test_fresh_un_update_downloads_and_indexes_first_pdf(self):
        root = self.root / 'UN'
        content = pdf_bytes('The official public report discusses Germany and human rights.')
        update_un.atomic_json(root / 'data/sources.json', {'documents': [row()]})
        with redirect_stdout(io.StringIO()):
            result = update_un.run_update(client=FakeClient({row()['pdf_url']: content}), root=root,
                                         rebuild=lambda: index_un.build_index(workers=1, root=root))
        self.assertEqual(result['phase'], 'complete')
        self.assertEqual(result['new'], 1)
        library = UNLibrary(root=root)
        self.assertEqual(library.search({'q': ['"human rights"']})['total'], 1)
        self.assertEqual(library.state()['total'], 1)

    def test_failed_first_un_update_keeps_empty_search_available(self):
        root = self.root / 'UN'
        update_un.atomic_json(root / 'data/sources.json', {'documents': [row()]})
        with redirect_stdout(io.StringIO()):
            result = update_un.run_update(client=FakeClient({row()['pdf_url']: b'not a PDF'}),
                                         root=root, rebuild=Mock())
        self.assertEqual(result['phase'], 'error')
        self.assertEqual(UNLibrary(root=root).search({})['total'], 0)
        self.assertFalse((root / 'data/catalog.json').exists())

    def test_standard_library_sqlite_supports_required_fulltext_search(self):
        with sqlite3.connect(':memory:') as connection:
            index_courts.make_schema(connection)
            connection.execute("INSERT INTO metadata_fts VALUES ('test','UN sources')")
            self.assertEqual(connection.execute(
                "SELECT doc_id FROM metadata_fts WHERE metadata_fts MATCH 'sources'").fetchone()[0], 'test')

    def test_missing_or_empty_catalog_cannot_replace_existing_indexes(self):
        for collection in ('UN', 'ICC-ICJ'):
            with self.subTest(collection=collection), ExitStack() as stack, redirect_stdout(io.StringIO()):
                root = self.root / collection
                data = root / 'data'
                data.mkdir(parents=True)
                index = data / ('un_index.sqlite3' if collection == 'UN' else 'court_index.sqlite3')
                connection = sqlite3.connect(index)
                index_courts.make_schema(connection)
                connection.execute('INSERT INTO documents VALUES (?,?)', ('a' * 64, '{}'))
                connection.commit()
                connection.close()
                before = index.read_bytes()
                if collection == 'UN':
                    with self.assertRaisesRegex(RuntimeError, 'Quellenkatalog'):
                        index_un.build_index(workers=1, root=root)
                    result = update_un.run_update(client=FakeClient({}), root=root, rebuild=Mock())
                    self.assertEqual(result['phase'], 'error')
                else:
                    self.court_paths(stack)
                    with self.assertRaisesRegex(RuntimeError, 'Quellenkatalog'):
                        index_courts.build_index(workers=1)
                    with self.assertRaisesRegex(RuntimeError, 'Quellenkatalog'):
                        update_courts.run_update(client=Mock(), rebuild=Mock())
                self.assertEqual(index.read_bytes(), before)

    def test_bip_missing_catalog_with_archived_pdf_stops_before_crawling(self):
        root = self.root / 'BIP'
        data = root / 'recherche/data'
        pdf = root / 'PDF/earlier.pdf'
        pdf.parent.mkdir(parents=True)
        original = pdf_bytes('Earlier archived publication')
        pdf.write_bytes(original)
        with patch.multiple(bip_build, ROOT=root, DATA=data, CATALOG=data / 'catalog.json'), \
                patch.object(crawler, 'run') as collect, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'Quellenkatalog'):
                bip_build.build(refresh=True)
        collect.assert_not_called()
        self.assertEqual(pdf.read_bytes(), original)
        self.assertFalse((data / 'catalog.json').exists())


if __name__ == '__main__':
    unittest.main()
