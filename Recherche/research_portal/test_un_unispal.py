"""UNISPAL discovery tests use saved responses and small deterministic fixtures."""
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

from .un_unispal import discover_unispal, core_record, parse_document, record_from_page


def record(ident=1, title='UN report on Gaza', extra_classes=None, content=''):
    return {'id': ident, 'link': f'https://www.un.org/unispal/document/test-{ident}/',
            'date': '2026-10-09T12:30:00', 'modified': '2026-10-10T12:30:00',
            'title': {'rendered': title}, 'content': {'rendered': content},
            'class_list': ['document-category-report', 'document-source-general-assembly',
                           'entity-united-nations-system', 'document-language-english', *(extra_classes or [])]}


def document_html(attachments=None, date='02/09/2026', body='General Assembly'):
    attachments = attachments or '<a class="pdf-link" href="https://www.un.org/unispal/wp-content/uploads/n2620995.pdf">PDF</a>'
    return f'''<html><body><article><p>Cited document:
      <a class="pdf-link" href="https://www.un.org/unispal/cited.pdf">Do not import citation</a></p></article>
      <div class="fusion-sharing-box">
      <strong>Document symbol: </strong><a>A/81/35</a><br>
      <strong>Download Document Files: </strong>{attachments}<br>
      <strong>Document Type: </strong><a>Annual report</a>, <a>Report</a><br>
      <strong>Document Sources: </strong><a>{body}</a><br>
      <strong>Publication Date: </strong>{date}<br></div>
      <div class="fusion-sharing-box fusion-theme-sharing-box"><a href="https://social.test/">Share</a></div>
      </body></html>'''.encode()


def complete_page(title='Report on Palestine'):
    """Small structural fixture: no private saved pages or copyrighted body text."""
    classes = ' '.join(['document', *record()['class_list']])
    return document_html().replace(b'<article>', (
        f'<article class="{classes}"><h2 class="entry-title fusion-post-title">{title}</h2>'
    ).encode())


class UNISPALTests(unittest.TestCase):
    def test_register_and_attached_report(self):
        rows = json.dumps([record()]).encode()
        report = complete_page()
        requests = []
        def fetch(url):
            requests.append(url)
            return rows if '/wp-json/' in url else report
        result = discover_unispal(fetch, {'max_documents': 1})
        self.assertEqual(len(result['documents']), 1)
        item = result['documents'][0]
        self.assertEqual(item['symbol'], 'A/81/35')
        self.assertEqual(item['date'], '2026-09-02')
        self.assertEqual(item['language'], '')
        self.assertIn('PDF-Inhaltsprüfung', item['metadata_note'])
        self.assertEqual(item['body'], 'General Assembly')
        self.assertEqual(item['kind'], 'Bericht')
        self.assertTrue(item['relations']['palestine'])
        self.assertTrue(item['pdf_url'].endswith('/n2620995.pdf'))
        self.assertEqual(len(requests), 2)
        self.assertTrue(any('Kein vollständiger historischer Bestand' in note for note in result['coverage_notes']))

    def test_only_un_core_documents_with_source_evidence(self):
        self.assertTrue(core_record(record()))
        for extra in ('document-category-non-un-document', 'document-category-ngo-action-news',
                      'document-category-press-release', 'document-category-news-item'):
            self.assertFalse(core_record(record(extra_classes=[extra])))
        unknown = record()
        unknown['class_list'].remove('document-source-general-assembly')
        unknown['class_list'].append('document-source-an-ngo')
        self.assertFalse(core_record(unknown))
        unhosted = record()
        unhosted['class_list'].remove('entity-united-nations-system')
        self.assertFalse(core_record(unhosted))
        self.assertFalse(core_record(record(title='Human rights report (Non-UN Document)')))

    def test_citations_and_other_languages_are_not_imported(self):
        attachments = '''<a class="pdf-link" href="https://www.un.org/archive/report-en.pdf">English</a>
          <a class="pdf-link" href="https://www.un.org/archive/report-de.pdf">Deutsch</a>
          <a class="pdf-link" href="https://www.un.org/archive/report-fr.pdf">French</a>
          <a href="https://www.un.org/archive/not-an-attachment.pdf">Other link</a>'''
        docs, errors = parse_document(record(), document_html(attachments))
        self.assertEqual({doc['language'] for doc in docs}, {'en', 'de'})
        self.assertEqual(len(docs), 2)
        self.assertFalse(errors)
        self.assertTrue(all('cited' not in doc['pdf_url'] for doc in docs))

    def test_date_is_document_date_not_wordpress_upload_date(self):
        doc = parse_document(record(), document_html(date='17/04/2024'))[0][0]
        self.assertEqual(doc['date'], '2024-04-17')
        doc = parse_document(record(), document_html(date=''))[0][0]
        self.assertEqual(doc['date'], '')
        self.assertIn('Kein belegtes Publikationsdatum', doc['metadata_note'])

    def test_country_lists_do_not_create_germany_or_israel_relation(self):
        row = record(content='<p>Participants: France, Germany, Israel, Brazil and Jordan.</p>',
                     extra_classes=['country-germany', 'country-israel'])
        doc = parse_document(row, document_html())[0][0]
        self.assertFalse(doc['relations']['germany'])
        self.assertFalse(doc['relations']['israel'])
        self.assertTrue(doc['relations']['palestine'])
        row['content']['rendered'] = '<p>Germany submitted a detailed statement on humanitarian aid. Israeli authorities restricted access.</p>'
        doc = parse_document(row, document_html())[0][0]
        self.assertTrue(doc['relations']['germany'])
        self.assertTrue(doc['relations']['israel'])
        self.assertIn('Germany submitted', doc['relation_reasons']['germany'][0])
        self.assertIn(row['link'], doc['relation_reasons']['israel'][0])

    def test_unresolved_language_is_explicit_for_pdf_validation(self):
        row = record(extra_classes=['document-language-french'])
        docs, errors = parse_document(row, document_html('<a class="pdf-link" href="https://www.un.org/archive/report.pdf">PDF</a>'))
        self.assertEqual(docs[0]['language'], '')
        self.assertIn('PDF-Inhalt muss geprüft', docs[0]['metadata_note'])
        self.assertFalse(errors)

    def test_record_english_taxonomy_does_not_authorize_russian_resolution_attachment(self):
        # The source record labels resolution 242 English, but this exact
        # unlabelled attachment was visually verified to contain Russian.
        source = record(title='Security Council resolution 242 (1967)')
        attachments = '<a class="pdf-link" href="https://www.un.org/unispal/wp-content/uploads/1967/11/doc.pdf">PDF</a>'
        documents, errors = parse_document(source, document_html(attachments, date='22/11/1967', body='Security Council'))
        self.assertFalse(errors)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]['language'], '')
        self.assertIn('PDF-Inhaltsprüfung erforderlich', documents[0]['metadata_note'])

    def test_german_record_taxonomy_also_requires_attachment_language_evidence(self):
        source = record()
        source['class_list'].remove('document-language-english')
        source['class_list'].append('document-language-german')
        attachments = '<a class="pdf-link" href="https://www.un.org/archive/unlabelled.pdf">PDF</a>'
        documents, errors = parse_document(source, document_html(attachments))
        self.assertFalse(errors)
        self.assertEqual(documents[0]['language'], '')
        # A concrete language label on the same attachment remains usable.
        documents, errors = parse_document(source, document_html(attachments.replace('>PDF<', '>Deutsch<')))
        self.assertEqual(documents[0]['language'], 'de')
        self.assertFalse(errors)

    def test_bounds_are_visible_and_preserve_partial_results(self):
        rows = [record(1), record(2), record(3)]
        calls = []
        def fetch(url):
            calls.append(url)
            if '/wp-json/' in url:
                page = parse_qs(urlsplit(url).query)['page'][0]
                if page == '2':
                    raise OSError('Register unavailable')
                return json.dumps(rows).encode()
            return document_html()
        result = discover_unispal(fetch, {'per_page': 3, 'max_pages': 2, 'max_documents': 1})
        self.assertEqual(len(result['documents']), 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('Register unavailable', result['errors'][0]['error'])
        self.assertEqual(len(calls), 3)
        self.assertTrue(any('2 geeignete Dokumentseiten' in note for note in result['coverage_notes']))

    def test_missing_or_untrusted_attachment_reported_not_guessed(self):
        docs, errors = parse_document(record(), document_html('<a href="https://www.un.org/cited.pdf">Citation</a>'))
        self.assertFalse(docs)
        self.assertTrue(errors)
        docs, errors = parse_document(record(), document_html('<a class="pdf-link" href="https://un.org.evil.test/private.pdf">PDF</a>'))
        self.assertFalse(docs)
        self.assertTrue(errors)
        docs, errors = parse_document(record(), document_html('<a class="pdf-link" href="https://www.unrwa.org/reports/report-en.pdf">English</a>'))
        self.assertEqual(len(docs), 1)
        self.assertFalse(errors)

    def test_historical_queries_keep_web_dates_separate_and_deduplicate(self):
        row = record(title='Germany and Palestine historical report')
        row['date'] = '2017-03-12T12:00:00'
        calls = []
        def fetch(url):
            calls.append(url)
            return json.dumps([row]).encode() if '/wp-json/' in url else document_html(date='29/11/1947')
        result = discover_unispal(fetch, {'max_documents': 10, 'queries': [
            {'label': 'Historisch', 'search': 'Germany', 'order': 'asc', 'before': '2020-12-31', 'after': '2010-01-01'},
            {'search': 'Palestine', 'order': 'desc'},
        ]})
        query = parse_qs(urlsplit(calls[0]).query)
        self.assertEqual(query['search'], ['Germany'])
        self.assertEqual(query['order'], ['asc'])
        self.assertEqual(query['before'], ['2020-12-31T23:59:59'])
        self.assertEqual(query['after'], ['2010-01-01T00:00:00'])
        self.assertEqual(len(result['documents']), 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result['documents'][0]['date'], '1947-11-29')
        self.assertTrue(any('nicht auf das Datum des Dokuments' in note for note in result['coverage_notes']))

    def test_explicit_historical_page_uses_verified_document_metadata(self):
        report = complete_page()
        calls = []
        url = 'https://www.un.org/unispal/document/ceirpp-report-2sep2026/'
        def fetch(target):
            calls.append(target)
            return report
        result = discover_unispal(fetch, {'record_urls': [url, url], 'queries': []})
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result['documents']), 1)
        self.assertEqual(result['documents'][0]['symbol'], 'A/81/35')
        self.assertEqual(result['documents'][0]['date'], '2026-09-02')

    def test_historical_181_page_accepts_article_h2_document_title(self):
        content = complete_page('Resolution 181: partition').replace(
            b'02/09/2026', b'29/11/1947').replace(
            b'n2620995.pdf', b'ARES181.pdf')
        url = 'https://www.un.org/unispal/document/auto-insert-185393/'
        result = discover_unispal(lambda target: content, {'record_urls': [url], 'queries': []})
        self.assertFalse(result['errors'])
        self.assertEqual(len(result['documents']), 1)
        item = result['documents'][0]
        self.assertIn('181', item['title'])
        self.assertIn('partition', item['title'])
        self.assertEqual(item['date'], '1947-11-29')
        self.assertTrue(item['pdf_url'].endswith('/ARES181.pdf'))

    def test_article_title_precedes_unrelated_outer_headings(self):
        classes = ' '.join(['document', *record()['class_list']])
        url = record()['link']
        content = f'<html><h1>Question of Palestine</h1><article class="{classes}"><h2 class="entry-title fusion-post-title">Actual resolution</h2></article><h2>Subscribe</h2></html>'
        self.assertEqual(record_from_page(url, content)['title']['rendered'], 'Actual resolution')
        with self.assertRaisesRegex(ValueError, 'Dokumenttitel fehlt'):
            record_from_page(url, content.replace('entry-title fusion-post-title', 'unrelated'))

    def test_legacy_language_suffixes_excluded_even_when_english_listed(self):
        row = record(extra_classes=['document-language-french', 'document-language-chinese', 'document-language-spanish'])
        attachments = ''.join(f'<a class="pdf-link" href="https://www.un.org/archive/{filename}">PDF</a>' for filename in
                              ('nr024106-C.pdf', 'nr024130-S.pdf', 'AAC25SR343f.pdf', 'doc.pdf'))
        documents, errors = parse_document(row, document_html(attachments))
        self.assertEqual([doc['pdf_url'].rsplit('/', 1)[-1] for doc in documents], ['doc.pdf'])
        self.assertEqual(documents[0]['language'], '')
        self.assertFalse(errors)

    def test_bad_history_filters_do_not_trigger_network(self):
        def fail_fetch(url):raise AssertionError('Network must not be called')
        for options in ({'order': 'random'}, {'before': 'yesterday'}, {'after': '2026-02-30'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                discover_unispal(fail_fetch, options)

    def test_french_prepositions_do_not_claim_german_or_english(self):
        row = record(extra_classes=['document-language-french'])
        document, errors = parse_document(row, document_html('<a class="pdf-link" href="https://www.un.org/archive/rapport-de-la-situation-en-palestine.pdf">Rapport</a>'))
        self.assertEqual(document[0]['language'], '')
        self.assertFalse(errors)

    def test_gaps_do_not_invent_pdf_or_follow_ods(self):
        for host in ('docs.un.org', 'undocs.org', ''):
            row = record()
            content = document_html(attachments='<span>No downloadable PDF</span>')
            if host:
                content = content.replace(b'<article>', (
                    f'<article><a href="https://{host}/A/81/35">Original</a>'
                ).encode())
            else:
                content = content.replace(b'<strong>Document symbol:', (
                    '<strong>URL Source: </strong>https://dppa.un.org/report<br>'
                    '<strong>Document symbol:'
                ).encode())
            documents, errors = parse_document(row, content)
            self.assertFalse(documents)
            self.assertTrue(errors)
            if host:
                self.assertIn('ODS', errors[0]['error'])
            else:
                self.assertTrue(errors[0]['original_source'].startswith('https://dppa.un.org/'))

    def test_pre_un_primary_text_is_not_admitted_by_later_un_taxonomy(self):
        with self.assertRaisesRegex(ValueError, 'Vorläuferquelle'):
            parse_document(record(title='League of Nations report'), document_html(date='01/12/1930'))
        row = record()
        row['date'] = '1930-12-01T00:00:00'
        documents, errors = parse_document(row, document_html(date='29/11/1947'))
        self.assertEqual(documents[0]['date'], '1947-11-29')
        self.assertFalse(errors)


if __name__ == '__main__':
    unittest.main()
