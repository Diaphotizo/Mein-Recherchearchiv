import unittest
from .un_discovery import ocha_links, parse_ocha, discover_ocha


class OchaDiscoveryTests(unittest.TestCase):
    def test_only_official_publication_links(self):
        body = b'<a href="/content/gaza">Gaza</a><a href="/content/gaza">copy</a><a href="https://other.example/content/foo">outside</a>'
        self.assertEqual(ocha_links(body, 'https://www.ochaopt.org/publications'), ['https://www.ochaopt.org/content/gaza'])

    def test_dates_languages_and_country_evidence(self):
        body = b'''<h1>Gaza snapshot (7 October 2026)</h1><time datetime="2026-10-09T12:00:00Z"></time>
        <nav>Germany Israel</nav><div class="document"><a href="/sites/default/files/gaza.pdf"></a>
        <a href="/sites/default/files/gaza_ar.pdf">Arabic</a><a href="https://other.example/a.pdf">Reference</a></div>
        <aside><a href="/sites/default/files/unrelated.pdf">Other publication</a></aside>'''
        rows = parse_ocha(body, 'https://www.ochaopt.org/content/gaza')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['date'], '2026-10-09')
        self.assertEqual(rows[0]['relations'], {'germany': False, 'israel': False, 'palestine': True})
        self.assertEqual(rows[0]['language'], '')

    def test_failed_detail_keeps_good_documents(self):
        listing = b'<a href="/content/gaza">Gaza</a><a href="/content/broken">Broken</a>'
        def fetch(url):
            if url.endswith('/publications'): return listing
            if url.endswith('/broken'): raise OSError('source unavailable')
            return b'<h1>Gaza</h1><div class="document"><a href="/sites/default/files/gaza.pdf"></a></div>'
        result = discover_ocha(fetch, {'max_documents': 2})
        self.assertEqual(len(result['documents']), 1)
        self.assertEqual(len(result['errors']), 1)


if __name__ == '__main__':
    unittest.main()
