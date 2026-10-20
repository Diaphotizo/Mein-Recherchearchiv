import unittest

from .un_german import discover_german, infer_authorship, is_un_document, parse_document, parse_listing


SOURCE = 'https://www.un.org/german/de/dokumentenbibliothek?search_api_fulltext=Israel'
DETAIL = 'https://www.un.org/german/de/media/7145'
PDF = 'https://www.un.org/german/sites/default/files/2022-10/sc15069.pdf'


def listing(title='Seegrenze zwischen Libanon und Israel', tags='Naher Osten, Israel, Libanon', next_page=False):
    pager = '<nav class="uw-paginator"><a href="?search_api_fulltext=Israel&amp;page=1" aria-label="Zur nächsten Seite">Weiter</a></nav>' if next_page else ''
    return f'''<html lang="de"><nav>Deutschland <a href="/german/de/media/99">Andere Datei</a></nav><main id="content">
      <table><thead><tr><th id="view-document-symbol">Nummer</th></tr></thead><tbody>
      <tr><td>SC/15069</td><td><a href="/german/de/media/7145">{title}</a></td>
      <td><time datetime="2022-10-19T12:00:00Z">2022-10-19</time></td><td>Sicherheitsrat</td>
      <td>Presseerklärung</td><td>{tags}</td></tr></tbody></table>{pager}</main></html>'''.encode()


def detail(extra=''):
    return f'''<html><nav><a href="/german/sites/default/files/organigramm.pdf">Organigramm</a></nav>
      <main id="content"><h1>Seegrenze zwischen Libanon und Israel</h1>
      <div class="field--name-field-media-document"><a href="{PDF}">Datei herunterladen</a>{extra}</div>
      </main><footer><a href="/german/sites/default/files/footer.pdf">Andere PDF</a></footer></html>'''.encode()


class GermanDiscoveryTests(unittest.TestCase):
    def test_real_table_metadata_and_relations(self):
        result = parse_listing(listing(), SOURCE)['documents'][0]
        self.assertEqual(result['symbol'], 'SC/15069')
        self.assertEqual(result['date'], '2022-10-19')
        self.assertEqual(result['body'], 'Sicherheitsrat')
        self.assertEqual(result['kind'], 'Presseerklärung')
        self.assertTrue(result['relations']['israel'])
        self.assertFalse(result['relations']['germany'])
        self.assertEqual(result['language'], 'de')

    def test_german_language_and_navigation_are_not_germany_relation(self):
        self.assertEqual(parse_listing(listing('Deutschsprachiger Bericht über Sudan', 'Sudan'), SOURCE)['documents'], [])
        germany = parse_listing(listing('Bericht über Deutschland', 'Menschenrechte'), SOURCE)['documents'][0]
        self.assertTrue(germany['relations']['germany'])
        self.assertFalse(germany['relations']['israel'])

    def test_palestine_subject_can_qualify_alone(self):
        record = parse_listing(listing('Menschenrechte', 'Palästina'), SOURCE)['documents'][0]
        self.assertTrue(record['relations']['palestine'])
        self.assertFalse(record['relations']['israel'])

    def test_detail_only_uses_own_pdf(self):
        result = parse_document(detail(), DETAIL, parse_listing(listing(), SOURCE)['documents'][0])
        self.assertEqual(result['pdf_url'], PDF)
        self.assertEqual(result['url'], DETAIL)

    def test_ambiguous_missing_and_offsite_pdfs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '2 unterschiedliche'):
            parse_document(detail('<a href="/german/sites/default/files/other.pdf">Other</a>'), DETAIL)
        with self.assertRaisesRegex(ValueError, 'Kein eindeutiger'):
            parse_document(b'<main id="content"><h1>Title</h1><a href="https://example.org/evil.pdf">PDF</a></main>', DETAIL)

    def test_bounded_discovery_uses_injected_fetch_and_reports_more_pages(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return listing(next_page=True) if url == SOURCE else detail()
        result = discover_german(fetch, {'urls': [SOURCE], 'max_pages': 1})
        self.assertEqual(calls, [SOURCE, DETAIL])
        self.assertEqual(len(result['documents']), 1)
        self.assertEqual(result['errors'], [])
        self.assertTrue(any('weitere Seiten' in note for note in result['coverage_notes']))

    def test_detail_failure_is_visible_and_does_not_invent_pdf(self):
        def fetch(url):
            if url == SOURCE:
                return listing()
            raise ValueError('HTTP 403')
        result = discover_german(fetch, {'urls': [SOURCE]})
        self.assertEqual(result['documents'], [])
        self.assertEqual(result['errors'][0]['url'], DETAIL)
        self.assertIn('HTTP 403', result['errors'][0]['error'])

    def test_unrecognized_layout_is_not_silent_success(self):
        result = discover_german(lambda _: b'<main>Something changed</main>', {'urls': [SOURCE]})
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['documents'], [])

    def test_ignored_tag_filter_is_visible(self):
        result = parse_listing(listing(), SOURCE + '&uw_search_media_tags%5B%5D=256')
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(len(result['documents']), 1)

    def test_document_limit_and_duplicate_detail_cache(self):
        calls = []
        source2 = SOURCE.replace('Israel', 'Palastina')
        content = listing().decode().replace('</tbody>', '<tr><td>A/1</td><td><a href="/german/de/media/99">Israel</a></td><td>2024-01-01</td><td>Organ</td><td>Bericht</td><td>Israel</td></tr></tbody>').encode()
        def fetch(url):
            calls.append(url)
            return content if url in (SOURCE, source2) else detail()
        result = discover_german(fetch, {'urls': [SOURCE, source2], 'max_documents': 1})
        self.assertEqual(calls.count(DETAIL), 1)
        self.assertNotIn('https://www.un.org/german/de/media/99', calls)
        self.assertTrue(any('höchstens 1 deutsche' in note for note in result['coverage_notes']))

    def test_german_state_report_is_not_a_un_body_authored_report(self):
        original = {'title': '15. Bericht der Bundesrepublik Deutschland nach Artikel 9 des Übereinkommens', 'author': '', 'kind': 'Sonstiges'}
        result = infer_authorship(original)
        self.assertEqual(result['author'], 'Bundesrepublik Deutschland')
        self.assertEqual(result['kind'], 'Staatlicher Bericht')
        self.assertEqual(original['author'], '')
        self.assertIn(original['title'], result['metadata_note'])

    def test_german_upr_response_takes_priority_over_working_group_prefix(self):
        result = infer_authorship({'title': 'Bericht der Arbeitsgruppe für die Allgemeine regelmäßige Überprüfung – Deutschland (Addendum: Auffassungen zu den Schlussfolgerungen und/oder Empfehlungen; freiwillige Verpflichtungen und Antworten des überprüften Staates)', 'symbol': 'A/HRC/39/9/Add.1', 'body': 'Menschenrechtsrat'})
        self.assertEqual(result['author'], 'Bundesrepublik Deutschland')
        self.assertEqual(result['kind'], 'Staatliche Antwort im UPR-Verfahren')
        self.assertTrue(is_un_document(result))

    def test_working_group_report_and_ambiguous_addendum(self):
        source = {'title': 'Bericht der Arbeitsgruppe für die Allgemeine regelmäßige Überprüfung – Deutschland', 'symbol': 'A/HRC/55/10'}
        result = infer_authorship(source)
        self.assertEqual(result['author'], 'Arbeitsgruppe für die Allgemeine regelmäßige Überprüfung (UPR)')
        ambiguous = infer_authorship(dict(source, symbol='A/HRC/55/10/Add.1'))
        self.assertFalse(ambiguous.get('author'))

    def test_named_special_rapporteur_and_concluding_committee(self):
        result = infer_authorship({'title': 'Bericht des Sonderberichterstatters über das Recht auf Entwicklung, Surya Deva, über seinen Besuch in Deutschland'})
        self.assertEqual(result['author'], 'Surya Deva · UN-Sonderberichterstatter über das Recht auf Entwicklung')
        committee = infer_authorship({'title': 'Abschließende Bemerkungen des Ausschusses für die Rechte des Kindes: Deutschland'})
        self.assertEqual(committee['author'], 'UN-Ausschuss für die Rechte des Kindes')
        self.assertEqual(committee['kind'], 'Abschließende Bemerkungen')

    def test_existing_and_unknown_authorship_are_preserved(self):
        original = {'title': 'Bericht der Bundesrepublik Deutschland', 'author': 'Explizite Quellenangabe', 'kind': 'Vorhandene Kategorie'}
        self.assertEqual(infer_authorship(original), original)
        unknown = {'title': 'Menschenrechte in Deutschland', 'body': 'Menschenrechtsrat', 'author': ''}
        self.assertEqual(infer_authorship(unknown), unknown)
        foreign_reply = {'title': 'Bericht der Arbeitsgruppe für die Allgemeine regelmäßige Überprüfung – Israel (Addendum: Antworten des überprüften Staates)', 'author': ''}
        self.assertEqual(infer_authorship(foreign_reply), foreign_reply)

    def test_national_laws_excluded_but_un_examination_retained(self):
        self.assertFalse(is_un_document({'title': 'Gesetz über den Beitritt der Bundesrepublik Deutschland zu der Konvention', 'symbol': 'DBGBL/729'}))
        self.assertFalse(is_un_document({'title': 'Gesetz über den Beitritt der Bundesrepublik Deutschland', 'kind': 'Sonstiges'}))
        self.assertFalse(is_un_document({'title': 'Beitritt der Bundesrepublik Deutschland', 'kind': 'Gesetz'}))
        self.assertTrue(is_un_document({'title': 'Bericht über das Gesetz zur Regelung von Migration in Deutschland', 'kind': 'Bericht'}))
        self.assertTrue(is_un_document({'title': 'Gesetz zur Regulierung – Bewertung des UN-Sonderberichterstatters', 'kind': 'Bericht'}))
        self.assertEqual(parse_listing(listing('Gesetz über den Beitritt der Bundesrepublik Deutschland', 'Deutschland'), SOURCE)['documents'], [])

    def test_listing_applies_explicit_authorship(self):
        result = parse_listing(listing('Bericht der Bundesrepublik Deutschland', 'Deutschland'), SOURCE)['documents'][0]
        self.assertEqual(result['author'], 'Bundesrepublik Deutschland')
        self.assertEqual(result['kind'], 'Staatlicher Bericht')

    def test_next_page_keeps_search_filter_when_link_omits_it(self):
        content = listing(next_page=True).replace(b'?search_api_fulltext=Israel&amp;page=1', b'?block_config_key=example&amp;page=1')
        next_url = parse_listing(content, SOURCE)['next_url']
        self.assertIn('search_api_fulltext=Israel', next_url)
        self.assertIn('page=1', next_url)
        self.assertIn('block_config_key=example', next_url)

    def test_repeated_results_stop_historical_pagination(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return detail() if '/media/' in url else listing(next_page=True)
        result = discover_german(fetch, {'urls': [SOURCE], 'max_pages': 4})
        self.assertEqual(len([url for url in calls if '/dokumentenbibliothek?' in url]), 2)
        self.assertEqual(len(result['documents']), 1)
        self.assertTrue(any('wiederholt' in error['error'] for error in result['errors']))

    def test_excluded_known_detail_does_not_consume_new_document_budget(self):
        calls = []
        content = listing().replace(b'</tbody>', b'<tr><td>A/1</td><td><a href="/german/de/media/99">Israel</a></td><td>2024-01-01</td><td>Organ</td><td>Bericht</td><td>Israel</td></tr></tbody>')
        def fetch(url):
            calls.append(url)
            return content if url == SOURCE else detail()
        result = discover_german(fetch, {'urls': [SOURCE], 'exclude_urls': [DETAIL], 'max_documents': 1})
        self.assertNotIn(DETAIL, calls)
        self.assertIn('https://www.un.org/german/de/media/99', calls)
        self.assertEqual(len(result['documents']), 1)

    def test_per_source_limit_allows_later_sources_to_contribute(self):
        source2 = SOURCE.replace('Israel', 'Palastina')
        calls = []
        first = listing().replace(b'</tbody>', b'<tr><td>A/1</td><td><a href="/german/de/media/98">Israel</a></td><td>2024-01-01</td><td>Organ</td><td>Bericht</td><td>Israel</td></tr></tbody>')
        def fetch(url):
            calls.append(url)
            if url == SOURCE: return first
            if url == source2: return listing().replace(b'/media/7145', b'/media/99')
            return detail().replace(b'sc15069.pdf', b'other.pdf') if url.endswith('/99') else detail()
        result = discover_german(fetch, {'urls': [SOURCE, source2], 'max_documents': 2, 'max_documents_per_source': 1})
        self.assertEqual(len(result['documents']), 2)
        self.assertNotIn('https://www.un.org/german/de/media/98', calls)
        self.assertIn('https://www.un.org/german/de/media/99', calls)

    def test_ignored_fulltext_search_is_visible(self):
        content = listing().replace(b'<main id="content">', b'<main id="content"><input name="search_api_fulltext" value="">')
        self.assertTrue(any('Suchtext' in warning for warning in parse_listing(content, SOURCE)['warnings']))

    def test_explicit_german_genitive_is_a_country_relation(self):
        title = 'Abschließende Bemerkungen zu dem kombinierten dreiundzwanzigsten bis sechsundzwanzigsten Bericht Deutschlands'
        result = parse_listing(listing(title, 'Menschenrechte'), SOURCE)['documents'][0]
        self.assertTrue(result['relations']['germany'])
        self.assertIn(title, result['relation_reasons']['germany'][0])



if __name__ == '__main__':
    unittest.main()
