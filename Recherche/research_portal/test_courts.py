"""Behavior tests for court filtering, quotations, annotations and safe PDFs."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from .paths import COURT_DATA
from .courts import CourtLibrary,INDEX_NAME,safe_pdf_path,valid_date,fts_query
from .index_courts import make_schema,add_document,metadata_search_text
from .reindex_metadata import rebuild_metadata

class CourtTests(unittest.TestCase):
    def setUp(self):
        self.temp_base=Path(__file__).resolve().parents[1]/'.test-tmp'
        self.temp_base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='backend-test-',dir=self.temp_base)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.data.mkdir()
        self.source=self.root/'original';self.source.mkdir();self.courts=self.root/'courts';self.courts.mkdir()
        self.ids=['a'*64,'b'*64,'c'*64]
        self.build()
        self.library=CourtLibrary(self.data,self.source,self.courts)

    def tearDown(self):
        # Dropbox briefly holds handles to newly written test directories.
        self.root.resolve().relative_to(self.temp_base.resolve())
        for attempt in range(8):
            try:self.temp.cleanup();return
            except PermissionError:
                if attempt==7:raise
                time.sleep(.15*(attempt+1))

    def build(self):
        path=self.data/INDEX_NAME
        path.unlink(missing_ok=True)
        con=sqlite3.connect(path);make_schema(con)
        fixtures=[
            ('ICJ','192','2024-05-24','Germany',True,['The court considered humanitarian aid in Gaza.','Witnesses reported torture and detention.']),
            ('ICC','ICC-01-18','2024-06-01','Prosecutor',False,['Humanitarian international aid was discussed in the West Bank.']),
            ('ICJ','192','0001-01-01','Germany',True,[''])]
        for n,(court,case,date,source,germany,pages) in enumerate(fixtures):
            ident=self.ids[n]
            metadata={'id':ident,'title':f'Document {n+1}','date':valid_date(date),'court':court,'case_id':case,
                'case_title':'Proceedings','document_id':f'TEST/{n+1}','kind':'Beschluss / Entscheidung','source':source,
                'author':'','pages':len(pages),'url':'https://example.test/source','germany_relevance':germany,
                'pdf_url':f'https://example.test/case/192/{11707+n}.pdf',
                'file_path':str(self.source/f'{11707+n}.pdf')}
            add_document(con,metadata,{'pages':pages})
        con.execute('INSERT INTO meta VALUES (?,?)',('built_at',json.dumps('2026-10-13')))
        con.commit();con.close()

    def test_exact_phrase_excludes_intervening_words(self):
        result=self.library.search({'q':['"humanitarian aid"']})
        self.assertEqual([i['id'] for i in result['items']],[self.ids[0]])
        self.assertEqual(result['items'][0]['snippets'][0]['page'],1)

    def test_filter_intersection(self):
        result=self.library.search({'court':['ICJ'],'case':['192'],'germany':['1'],
            'date_from':['2024-01-01'],'source':['Germany'],'tag':['Gaza','Folter']})
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['id'],self.ids[0])
        self.assertEqual(self.library.search({'court':['ICC'],'tag':['Folter']})['total'],0)

    def test_source_url_and_filename_search_respects_filters(self):
        for query in ('11707','11707.pdf','https://example.test/case/192/11707.pdf'):
            with self.subTest(query=query):
                found=self.library.search({'q':[query],'court':['ICJ'],'case':['192'],'tag':['Folter']})
                self.assertEqual([i['id'] for i in found['items']],[self.ids[0]])
                self.assertEqual(self.library.search({'q':[query],'court':['ICC']})['total'],0)
        searchable=metadata_search_text({'file_path':r'C:\PrivateUser\Archive\11707.pdf'})
        self.assertIn('11707.pdf',searchable)
        self.assertNotIn('PrivateUser',searchable)

    def test_metadata_migration_preserves_corpus_and_annotations(self):
        self.library.save(self.ids[0],{'tags':['Dossier'],'notes':'Keep this.'})
        annotations=self.library.annotations.read_bytes()
        with closing(sqlite3.connect(self.data/INDEX_NAME)) as con:
            before={table:con.execute(f'SELECT * FROM {table}').fetchall()
                    for table in ('meta','documents','pages','pages_fts')}
            con.execute('UPDATE metadata_fts SET text=?',('legacy entry',))
            con.commit()
        self.assertEqual(self.library.search({'q':['11707']})['total'],0)
        for _ in range(2):self.assertEqual(rebuild_metadata(self.data),3)
        self.assertEqual(self.library.search({'q':['11707.pdf']})['total'],1)
        self.assertEqual(self.library.annotations.read_bytes(),annotations)
        with closing(sqlite3.connect(self.data/INDEX_NAME)) as con:
            for table,rows in before.items():
                self.assertEqual(con.execute(f'SELECT * FROM {table}').fetchall(),rows)

    def test_metadata_migration_refuses_active_update(self):
        (self.data/'update_status.json').write_text('{"phase":"running"}',encoding='utf-8')
        with self.assertRaises(RuntimeError):rebuild_metadata(self.data)
        self.assertEqual(self.library.search({'q':['11707']})['total'],1)

    def test_unknown_date_is_not_ancient_date(self):
        self.assertEqual(valid_date('0001-01-01'),'')
        self.assertEqual(valid_date('not-a-date'),'')
        self.assertEqual(self.library.get(self.ids[2])['document']['date'],'')
        self.assertEqual(self.library.search({'date_to':['2024-12-31']})['total'],2)

    def test_annotations_survive_index_replacement(self):
        self.library.save(self.ids[0],{'tags':['Eigenes Dossier'],'notes':'My research note.'})
        annotation_bytes=self.library.annotations.read_bytes()
        self.build()
        self.assertEqual(self.library.annotations.read_bytes(),annotation_bytes)
        document=self.library.get(self.ids[0])['document']
        self.assertEqual(document['tags'],['Eigenes Dossier'])
        self.assertIn('Gaza',document['auto_tags'])
        self.assertEqual(document['notes'],'My research note.')
        self.assertEqual(self.library.search({'tag':['Eigenes Dossier']})['total'],1)

    def test_safe_pdf_allowlist(self):
        accepted=self.source/'file.pdf'
        self.assertEqual(safe_pdf_path(accepted,self.source,self.courts),accepted.resolve())
        self.assertEqual(safe_pdf_path(self.courts/'PDF'/'new.pdf',self.source,self.courts),(self.courts/'PDF'/'new.pdf').resolve())
        with self.assertRaises(ValueError):safe_pdf_path(self.source/'..'/'outside.pdf',self.source,self.courts)
        with self.assertRaises(ValueError):safe_pdf_path(self.source/'private.txt',self.source,self.courts)
        con=sqlite3.connect(self.data/INDEX_NAME)
        raw=json.loads(con.execute('SELECT metadata FROM documents WHERE id=?',(self.ids[0],)).fetchone()[0])
        raw['file_path']=str(self.root/'outside.pdf')
        con.execute('UPDATE documents SET metadata=? WHERE id=?',(json.dumps(raw),self.ids[0]));con.commit();con.close()
        with self.assertRaises(ValueError):self.library.pdf_path(self.ids[0])

    def test_evidence_and_missing_text_pages(self):
        item=self.library.get(self.ids[0],page=2)['document']
        self.assertEqual(item['tag_evidence']['Folter'],[2])
        self.assertEqual(self.library.get(self.ids[2])['document']['text_status'],'none')
        self.assertEqual(self.library.search({'text_status':['searchable']})['total'],2)
        self.assertEqual(self.library.search({'text_status':['no_text']})['total'],1)
        self.assertEqual(self.library.state()['no_text_pages'],1)

    def test_synonyms_and_literal_query_operators(self):
        self.assertEqual(self.library.search({'q':['Folter']})['total'],1)
        self.assertEqual(self.library.search({'q':['Westbank']})['total'],1)
        for query in ('"','*','title: OR NOT','(test)'):
            self.library.search({'q':[query]})

    def test_document_wide_and_across_pages(self):
        found=self.library.search({'q':['Gaza Folter']})
        self.assertEqual(found['total'],1)
        self.assertEqual(found['items'][0]['id'],self.ids[0])
        detail=self.library.get(self.ids[0],q='Gaza Folter')
        self.assertEqual([hit['page'] for hit in detail['matches']],[1,2])

    def test_freshness_from_completed_source_check(self):
        (self.data/'additions.json').write_text(json.dumps({'checked_at':'2026-10-13T14:30:00+00:00',
            'last_partial_check_at':'2026-10-14T14:30:00+00:00','coverage_notes':['Registerabgleich']}),encoding='utf-8')
        state=self.library.state()
        self.assertEqual(state['source_checked_at'],'2026-10-13T14:30:00+00:00')
        self.assertEqual(state['coverage_notes'],['Registerabgleich'])

if __name__=='__main__':unittest.main()
