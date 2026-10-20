import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from recherche.library import Library
from recherche.taxonomy import classify,query_groups
from recherche.build import contributors_for,paragraphs_from,build,write_json


def article(ident,title,body,author=None,tags=None,date='2026-01-02T12:00:00'):
    return {'id':ident,'title':title,'date':date,'modified':date,'kind':'Artikel','number':'',
            'type':'post','url':'https://bip-jetzt.de/'+str(ident)+'/','pages':1,'pdf':'PDF/test.pdf',
            'contributors':[{'name':author,'role':'Autor','evidence':'Signatur','paragraph':0}] if author else [],
            'paragraphs':[{'text':p,'heading':False,'section':''} for p in body],
            'tags':tags or [],'tag_evidence':{t:[0] for t in tags or []}}


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.catalog={'articles':[
            article(1,'Westjordanland und Haft',['Im Westjordanland wird über Folter berichtet.','Ein anderer Absatz beschreibt die Wirtschaft.'],'Norman Paech',['Westbank / Westjordanland','Folter']),
            article(2,'Gaza und Wirtschaft',['Die Wirtschaft in Gaza verändert sich.'],'Shir Hever',['Gaza','Wirtschaft'],date='2025-01-02T12:00:00'),
            article(3,'Zitat und Redaktion',['Shir Hever wird hier nur zitiert.'])],
            'archived':[],'taxonomy':[{'label':'Folter','group':'Themen'}],
            'built_at':'2026-01-02','source_checked_at':'2026-01-02'}
        write_json(self.root/'catalog.json',self.catalog)
        self.lib=Library(self.root/'catalog.json',self.root/'own.json')
    def tearDown(self):self.temp.cleanup()

    def test_synonyms_find_body_not_only_title(self):
        self.assertEqual(self.lib.search({'q':['Westbank Wirtschaft']})['items'][0]['id'],1)
        self.assertEqual(self.lib.search({'q':['"anderer Absatz"']})['total'],1)
        self.assertEqual(self.lib.search({'q':['"nicht vorhandene Phrase"']})['total'],0)

    def test_all_filters_intersect(self):
        params={'tag':['Westbank / Westjordanland','Folter'],'author':['Norman Paech'],
                'role':['Autor'],'from':['2026-01-01'],'to':['2026-01-31']}
        self.assertEqual(self.lib.search(params)['total'],1)
        self.assertEqual(self.lib.search(dict(params,to=['2025-12-31']))['total'],0)
        self.assertEqual(self.lib.search({'tag':['Folter','Gaza']})['total'],0)

    def test_mentions_are_not_authors(self):
        self.assertEqual(self.lib.search({'author':['Shir Hever']})['total'],1)
        self.assertEqual(self.lib.search({'author':['__unknown__']})['total'],1)

    def test_manual_tags_survive_new_catalog(self):
        self.lib.save(1,{'tags':['Folter','Eigener Schwerpunkt'],'contributors':[{'name':'Eigene Redaktion','role':'Redaktion'}]})
        revised=copy.deepcopy(self.catalog)
        revised['articles'][0]['tags'].append('Menschenrechte')
        revised['articles'][0]['tag_evidence']['Menschenrechte']=[0]
        write_json(self.root/'catalog.json',revised)
        updated=self.lib.get(1)
        self.assertIn('Eigener Schwerpunkt',updated['tags'])
        self.assertIn('Menschenrechte',updated['tags'])
        self.assertNotIn('Westbank / Westjordanland',updated['tags'])
        self.assertTrue(any(p['name']=='Eigene Redaktion' for p in updated['contributors']))

    def test_failed_refresh_keeps_catalog(self):
        before=(self.root/'catalog.json').read_bytes()
        with patch('recherche.build.ROOT',self.root),patch('recherche.build.DATA',self.root),patch('recherche.build.CATALOG',self.root/'catalog.json'),patch('recherche.build.crawler.run',return_value=1):
            with self.assertRaisesRegex(RuntimeError,'Quellabgleich'):build(refresh=True)
        self.assertEqual((self.root/'catalog.json').read_bytes(),before)


class ExtractionTests(unittest.TestCase):
    def test_bold_inline_link_stays_in_paragraph(self):
        ps,_,_=paragraphs_from('<p>Der Journalist von <a href="/x"><strong>GazaPress</strong></a> erhielt den Preis.</p>','https://bip-jetzt.de/')
        self.assertEqual(len(ps),1)
        self.assertFalse(ps[0]['heading'])
        self.assertIn('erhielt den Preis.',ps[0]['text'])

    def test_byline_inside_paragraph(self):
        pars=[{'text':'Eine Einleitung.','heading':False,'section':''},{'text':'UNSER MÄDCHEN\nvon Sima Kadmon, Yediot Ahronot','heading':False,'section':''}]
        people=contributors_for({'id':407,'title':{'rendered':'Artikel'}},pars)
        self.assertEqual(people[0]['name'],'Sima Kadmon')
        self.assertEqual(people[0]['role'],'Autor')

    def test_editor_is_not_inferred_author(self):
        pars=[{'text':'Das Redaktionsteam von BIP-Aktuell besteht aus dem Vorstand und Dr. Shir Hever.','heading':False,'section':''}]
        people=contributors_for({'id':99999,'title':{'rendered':'Artikel'}},pars)
        self.assertTrue(any(p['name']=='Shir Hever' and p['role']=='Redaktion' for p in people))
        self.assertFalse(any(p['role']=='Autor' for p in people))

    def test_transparent_topic_evidence(self):
        paras=[{'text':'Eine beiläufige Folter-Erwähnung.','heading':False,'section':''}]
        self.assertNotIn('Folter',classify('Ein anderes Thema',paras))
        self.assertIn('Folter',classify('Folter und Haftbedingungen',paras))
        self.assertEqual(classify('Folter und Haftbedingungen',paras)['Folter'],[0])

    def test_no_loss_between_html_and_paragraphs(self):
        ps,links,_=paragraphs_from('<h2>Titel</h2><p>Über <a href="/x">Folter</a> und Haft.<br>Zweite Zeile.</p><p>\uf0a7 Liste</p>','https://bip-jetzt.de/')
        self.assertTrue(ps[0]['heading'])
        self.assertEqual(ps[1]['section'],'Titel')
        self.assertIn('Zweite Zeile.',' '.join(p['text'] for p in ps))
        self.assertEqual(links,['https://bip-jetzt.de/x'])
        self.assertIn('• Liste',' '.join(p['text'] for p in ps))


if __name__=='__main__':unittest.main()
