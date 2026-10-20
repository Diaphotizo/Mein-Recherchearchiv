import unittest

from .un_spcomm import listing_date, parse_communications


SOURCE = 'https://spcommreports.ohchr.org/TmSearch/Mandates?m=22'


def row(symbol='DEU 3/2024', recipient='Germany', summary='Arms supplied by Germany to Israel for use in Gaza.',
        mandates='food', replies='', date='17 May 2024', gid='29071', language=''):
    return f'''<tr><td><ul>{date}</ul><ul>{recipient}</ul><ul>JAL</ul><ul>
      <a href="/TMResultsBase/DownLoadPublicCommunicationFile?gId={gid}" {language}>{symbol}</a>
      </ul></td><td>{mandates}</td><td>{summary}<br>Alleged victims: -<a>More details...</a></td>
      <td>{replies}</td></tr>'''


def parse(rows):
    return parse_communications(('<html lang="en"><table id="table-results">' + rows + '</table></html>').encode(), SOURCE)


class CommunicationParserTests(unittest.TestCase):
    def test_country_and_summary_relations_with_exact_dates(self):
        result = parse(row())[0]
        self.assertEqual(result['symbol'], 'DEU 3/2024')
        self.assertEqual(result['date'], '2024-05-17')
        self.assertEqual(result['relations'], {'germany': True, 'israel': True, 'palestine': True})
        self.assertIn('Adressat laut OHCHR: Germany', result['relation_reasons']['germany'][0])
        self.assertIn('Arms supplied by Germany to Israel', result['relation_reasons']['israel'][0])
        self.assertEqual(result['pdf_url'], 'https://spcommreports.ohchr.org/TMResultsBase/DownLoadPublicCommunicationFile?gId=29071')
        self.assertNotIn('Alleged victims', result['title'])

    def test_mandate_country_does_not_establish_relation(self):
        self.assertEqual(parse(row('OTH 2/2024', 'Corporation', 'Mining conditions in Peru.', 'Germany Israel Palestinian territories')), [])

    def test_summary_inclusion_and_palestine_only_exclusion(self):
        included = parse(row('USA 1/2025', 'United States of America', 'Arms supplied to Israel.'))
        self.assertTrue(included[0]['relations']['israel'])
        self.assertFalse(included[0]['relations']['germany'])
        self.assertEqual(parse(row('OTH 2/2024', 'Corporation', 'Children in Gaza.')), [])

    def test_state_replies_have_own_dates_authors_and_unknown_language(self):
        result = parse(row(replies='<a href="/TMResultsBase/DownLoadFile?gId=38541">16 Jul 2024</a>'))
        self.assertEqual(len(result), 2)
        original, reply = result
        self.assertEqual(original['author'], 'UN-Sonderberichterstatter / Arbeitsgruppen')
        self.assertEqual(reply['author'], 'Bundesrepublik Deutschland')
        self.assertEqual(reply['kind'], 'Antwort eines Mitgliedstaats')
        self.assertEqual(reply['date'], '2024-07-16')
        self.assertEqual(reply['language'], '')
        self.assertEqual(original['language'], '')  # English page/summary proves neither PDF's language.
        self.assertEqual(reply['relations'], original['relations'])

    def test_israel_reply_and_explicit_german_language(self):
        result = parse(row('ISR 9/2026', 'Israel', 'Restrictions on assembly.', replies='<a href="/TMResultsBase/DownLoadFile?gId=2" hreflang="de">01 Aug 2026</a>', language='hreflang="en"'))
        self.assertEqual(result[0]['language'], 'en')
        self.assertEqual(result[1]['language'], 'de')
        self.assertEqual(result[1]['author'], 'Israel')

    def test_company_reply_is_not_attributed_to_state_or_un(self):
        result = parse(row('OTH 6/2024', 'Example Company', 'Arms supplied to Israel.', replies='<a href="/TMResultsBase/DownLoadFile?gId=2">01 Jun 2024 (Ack.)</a>'))
        self.assertEqual(result[1]['author'], 'Example Company')
        self.assertEqual(result[1]['kind'], 'Eingangsbestätigung des Adressaten')

    def test_only_actual_same_host_download_urls_are_returned(self):
        replies = '<a href="https://other.example/TMResultsBase/DownLoadFile?gId=1">16 Jul 2024</a><a href="javascript:alert(1)">English</a>'
        self.assertEqual(len(parse(row(replies=replies))), 1)
        self.assertEqual(parse(row(gid='not-a-number')), [])

    def test_invalid_dates_remain_unknown_and_duplicates_are_removed(self):
        self.assertEqual(listing_date('2024'), '')
        self.assertEqual(listing_date('31 Feb 2024'), '')
        result = parse(row(date='unknown') + row(date='unknown'))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['date'], '')

    def test_country_lists_and_distribution_lists_do_not_qualify(self):
        summaries = [
            'Countries: Germany, Israel and France.',
            'Germany, Israel and France.',
            'Recipients: Germany; Israel; France.',
            'This communication was also sent to Germany, Israel and France.',
            'The report contains a list of countries including Germany, Israel and France.',
        ]
        for summary in summaries:
            with self.subTest(summary=summary):
                self.assertEqual(parse(row('OTH 1/2024', 'Organisation', summary)), [])

    def test_list_exclusion_preserves_direct_addressee(self):
        result = parse(row('DEU 3/2024', 'Germany', 'Recipients: Germany, Israel and France.'))[0]
        self.assertTrue(result['relations']['germany'])
        self.assertFalse(result['relations']['israel'])
        self.assertEqual(len(result['relation_reasons']['germany']), 1)
        self.assertIn('Adressat laut OHCHR', result['relation_reasons']['germany'][0])

    def test_country_actor_prose_and_other_substantive_occurrence_remain(self):
        summaries = [
            'Arms supplied by Germany to Israel for use in Gaza.',
            'Countries including Germany and Israel have adopted the measures under discussion.',
            'The report concerns Germany and Israel and their responsibilities under international law.',
            'Recipients: Germany, Israel and France. Germany supplied arms to Israel.',
        ]
        for summary in summaries:
            with self.subTest(summary=summary):
                result = parse(row('OTH 1/2024', 'Organisation', summary))[0]
                self.assertTrue(result['relations']['germany'])
                self.assertTrue(result['relations']['israel'])


if __name__ == '__main__':
    unittest.main()
