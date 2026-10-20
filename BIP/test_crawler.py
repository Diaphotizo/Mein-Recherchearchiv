import json
from pathlib import Path
import tempfile
import unittest

from crawler import extract_text, export_item, fetch_collection, normal_url, filename, page_fragment


class TextTests(unittest.TestCase):
    def test_text_boundaries_unicode_and_links(self):
        body, links, images = extract_text('<p>Grüße &amp; <b>Öl</b>: <a href="/quelle">Beleg</a>.</p><p>Zweiter Absatz<br>Neue Zeile.</p><ol start="3"><li>Eins</li><li>Zwei</li></ol><script>EVIL</script><div class="sharedaddy">Teilen</div><figure><img src="/bild.jpg" alt="Bildbeschreibung"><figcaption>Bildunterschrift</figcaption></figure>', 'https://bip-jetzt.de/test/')
        self.assertIn('Grüße & Öl: Beleg [1].', body)
        self.assertIn('\n\nZweiter Absatz\nNeue Zeile.', body)
        self.assertIn('3. Eins', body)
        self.assertIn('4. Zwei', body)
        self.assertNotIn('EVIL', body)
        self.assertNotIn('Teilen', body)
        self.assertIn('Bildunterschrift', body)
        self.assertEqual(links, ['https://bip-jetzt.de/quelle'])
        self.assertEqual(images[0]['url'], 'https://bip-jetzt.de/bild.jpg')

    def test_duplicate_links_and_untrusted_schemes(self):
        body, links, _ = extract_text('<a href="/x">A</a> <a href="/x">B</a><a href="javascript:alert(1)">C</a>', 'https://bip-jetzt.de/')
        self.assertEqual(len(links), 1)
        self.assertIn('A [1] B [1]C', body)

    def test_full_long_article_no_truncation(self):
        fragment = '<p>' + 'Äpfel ' * 40000 + 'ARTIKELENDE</p>'
        body, _, _ = extract_text(fragment, 'https://bip-jetzt.de/')
        self.assertGreater(len(body), 200000)
        self.assertTrue(body.endswith('ARTIKELENDE'))

    def test_comment_tail_and_frontend_fallback(self):
        raw = b'<html><body><nav>MENU</nav><main><p>Before<!-- comment -->after</p></main><footer>FOOTER</footer></body></html>'
        body, _, _ = extract_text(page_fragment(raw, 123), 'https://bip-jetzt.de/')
        self.assertEqual(body, 'Beforeafter')
        with self.assertRaisesRegex(RuntimeError, 'Kein eindeutiger'):
            page_fragment(b'<html><body><p>Unclassified content</p></body></html>', 123)

    def test_export_and_unique_windows_filenames(self):
        item = {'id': 17, 'date': '2020-01-02T03:04:05', 'modified': '2020-01-03T03:04:05', 'type': 'post', 'link': 'https://bip-jetzt.de/test/', 'title': {'rendered': 'A: Titel?'}, 'content': {'rendered': '<p>Vollständiger Text.</p>', 'protected': False}}
        with tempfile.TemporaryDirectory() as temp:
            entry = export_item(item, Path(temp), '2026-09-30T00:00:00+00:00')
            content = (Path(temp) / entry['file']).read_text(encoding='utf-8')
            self.assertIn('Vollständiger Text.', content)
            self.assertIn('Quelle: https://bip-jetzt.de/test/', content)
            self.assertNotEqual(filename(item, 'A: Titel?'), filename(dict(item, id=18), 'A: Titel?'))
        self.assertEqual(normal_url('https://bip-jetzt.de/%C3%A4/#x'), 'https://bip-jetzt.de/ä')

    def test_elementor_template_inside_nested_main(self):
        raw = b'''<html><body><nav>MENU</nav><main id="main">
        <main data-elementor-type="single-page" data-elementor-id="2311"
              class="elementor post-2436 page"><p>Complete landing page.</p></main>
        </main><footer>FOOTER</footer></body></html>'''
        body, _, _ = extract_text(page_fragment(raw, 2436), 'https://bip-jetzt.de/')
        self.assertEqual(body, 'Complete landing page.')
        with self.assertRaisesRegex(RuntimeError, 'Kein eindeutiger'):
            page_fragment(raw, 243)
        ambiguous = raw.replace(b'</body>', b'''<main data-elementor-type="single-page"
            class="post-2436"><p>Other content.</p></main></body>''')
        with self.assertRaisesRegex(RuntimeError, 'Kein eindeutiger'):
            page_fragment(ambiguous, 2436)

    def test_empty_body_is_reported(self):
        item = {'id': 17, 'title': {'rendered': 'Leer'}, 'content': {'rendered': '<p> </p>'}, 'link': 'https://bip-jetzt.de/leer/'}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, 'Kein extrahierbarer Text'):
                export_item(item, Path(temp), 'today')


class PaginationTests(unittest.TestCase):
    def test_all_pages_fetched(self):
        class FakeClient:
            calls = 0
            def get(self, url):
                self.calls += 1
                ids = range(1, 101) if self.calls == 1 else [101]
                return json.dumps([{'id': value} for value in ids]).encode(), {'X-WP-Total': '101', 'X-WP-TotalPages': '2'}
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temp:
            items, count = fetch_collection(client, 'posts', Path(temp))
        self.assertEqual((len(items), count, client.calls), (101, 101, 2))

    def test_missing_items_fail(self):
        class FakeClient:
            def get(self, url):
                return b'[{"id":1}]', {'X-WP-Total': '2', 'X-WP-TotalPages': '1'}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, 'Erwartet 2, erhalten 1'):
                fetch_collection(FakeClient(), 'posts', Path(temp))


if __name__ == '__main__':
    unittest.main()
