"""Local court corpus, full-text search and separate personal annotations."""
from __future__ import annotations
from collections import Counter
from datetime import date,datetime
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from .paths import COURT_DATA, COURT_SOURCE, COURTS
from .storage import read_json as robust_read_json, atomic_json as robust_atomic_json

INDEX_NAME='court_index.sqlite3'
ANNOTATIONS_NAME='annotations.json'
_SAVE_LOCK=threading.RLock()
TOPICS=[
 ('Gaza','Orte',r'\bgaza\b'),
 ('Westbank','Orte',r'\bwest bank\b|\bwestjordanland\b'),
 ('Ostjerusalem','Orte',r'\beast jerusalem\b|\bostjerusalem\b'),
 ('Israel','Orte',r'\bisrael(?:i)?\b'),
 ('Palästina','Orte',r'\bpalestin(?:e|ian|ians)\b'),
 ('Libanon','Orte',r'\bleban(?:on|ese)\b'),
 ('Deutschland','Staaten und Organisationen',r'\bgerman(?:y)?\b|\bbundesrepublik\b'),
 ('UNRWA','Staaten und Organisationen',r'\bunrwa\b'),
 ('Vereinte Nationen','Staaten und Organisationen',r'\bunited nations\b'),
 ('Völkermord','Menschenrechte und Völkerrecht',r'\bgenocid(?:e|al)\b'),
 ('Kriegsverbrechen','Menschenrechte und Völkerrecht',r'\bwar crimes?\b'),
 ('Verbrechen gegen die Menschlichkeit','Menschenrechte und Völkerrecht',r'\bcrimes? against humanity\b'),
 ('Apartheid','Menschenrechte und Völkerrecht',r'\bapartheid\b'),
 ('Folter','Menschenrechte und Völkerrecht',r'\btortur(?:e|ed|ing)\b'),
 ('Sexuelle Gewalt','Menschenrechte und Völkerrecht',r'\bsexual violence\b|\brape\b'),
 ('Verschwindenlassen','Menschenrechte und Völkerrecht',r'\benforced disappearances?\b|\bforced disappearances?\b'),
 ('Vertreibung','Besatzung und Lebensbedingungen',r'\bforcible transfer\b|\bforced displacement\b|\bethnic cleansing\b|\bdeportation\b'),
 ('Besatzung','Besatzung und Lebensbedingungen',r'\boccup(?:ation|ied territor(?:y|ies)|ying power)\b'),
 ('Siedlungen','Besatzung und Lebensbedingungen',r'\bsettlements?\b|\bsettlers?\b'),
 ('Blockade','Besatzung und Lebensbedingungen',r'\bblockade\b|\bsiege\b'),
 ('Aushungerung','Besatzung und Lebensbedingungen',r'\bstarvation\b|\bfamine\b'),
 ('Humanitäre Hilfe','Besatzung und Lebensbedingungen',r'\bhumanitarian (?:aid|assistance|relief)\b'),
 ('Gesundheit','Besatzung und Lebensbedingungen',r'\bhospitals?\b|\bhealth care\b|\bmedical (?:care|facilities)\b'),
 ('Kinder','Bevölkerung',r'\bchildren\b|\bchild rights\b'),
 ('Frauen','Bevölkerung',r'\bwomen\b'),
 ('Flüchtlinge','Bevölkerung',r'\brefugees?\b'),
 ('Gefangene','Bevölkerung',r'\bdetainees?\b|\bprisoners?\b|\badministrative detention\b'),
 ('Geiseln','Bevölkerung',r'\bhostages?\b'),
 ('Wirtschaft','Wirtschaft und Rüstung',r'\beconomic\b|\beconomy\b|\btrade\b'),
 ('Waffenlieferungen','Wirtschaft und Rüstung',r'\barms (?:transfers?|exports?|suppl(?:y|ies))\b|\bweapons? (?:transfers?|exports?|suppl(?:y|ies))\b'),
 ('Sanktionen','Wirtschaft und Rüstung',r'\bsanctions?\b|\bembargo\b'),
 ('Unternehmen','Wirtschaft und Rüstung',r'\bcorporations?\b|\bbusiness enterprises\b'),
 ('Land und Eigentum','Wirtschaft und Rüstung',r'\bconfiscation\b|\bexpropriation\b|\bproperty rights\b'),
 ('Gerichtsbarkeit','Verfahren',r'\bjurisdiction\b'),
 ('Einstweilige Maßnahmen','Verfahren',r'\bprovisional measures\b'),
 ('Haftbefehle','Verfahren',r'\barrest warrants?\b|\bwarrants? of arrest\b'),
 ('Opferbeteiligung','Verfahren',r'\bvictims?.{0,30}participation\b|\bparticipation of victims\b'),
 ('Beweismittel','Verfahren',r'\bevidence\b|\bwitness(?:es)?\b'),
 ('Waffenstillstand','Politik und Diplomatie',r'\bceasefire\b|\bcease-fire\b'),
 ('Selbstbestimmung','Politik und Diplomatie',r'\bself-determination\b|\bself determination\b'),
]
TOPIC_GROUPS={label:group for label,group,_ in TOPICS}
SYNONYMS={
 'westbank':['west bank','Westjordanland'],'westjordanland':['west bank','Westbank'],
 'völkermord':['genocide','genocidal'],'genozid':['genocide','genocidal'],
 'folter':['torture','tortured'],'besatzung':['occupation','occupied'],
 'siedlungen':['settlements'],'deutschland':['Germany','German'],
 'waffenlieferungen':['arms transfers','arms exports','weapons supplies'],
 'wirtschaft':['economic','economy','trade'],'geiseln':['hostages'],
 'aushungerung':['starvation'],'flüchtlinge':['refugees'],
 'kriegsverbrechen':['war crimes'],'ostjerusalem':['east jerusalem'],
 'haftbefehle':['arrest warrants'],'kinder':['children'],
}

def json_read(path,default=None):
    return robust_read_json(path,default)

def atomic_json(path,value):
    return robust_atomic_json(path,value)

def valid_date(value):
    value=str(value or '')[:10]
    try:return value if date.fromisoformat(value).year>1 else ''
    except ValueError:return ''

def valid_checked_at(value):
    if not isinstance(value,str):return None
    try:
        checked=datetime.fromisoformat(value.replace('Z','+00:00'))
        return value if checked.year>1 else None
    except ValueError:return None

def safe_pdf_path(value,source=COURT_SOURCE,courts=COURTS):
    path=Path(value).resolve()
    if path.suffix.lower()!='.pdf':raise ValueError('Nur PDF-Dateien sind erlaubt.')
    for root in (Path(source).resolve(),(Path(courts)/'PDF').resolve()):
        try:path.relative_to(root);return path
        except ValueError:pass
    raise ValueError('PDF-Pfad liegt außerhalb der Gerichtssammlung.')

def query_terms(query):
    return [(m.group(1) if m.group(1) is not None else m.group(2),m.group(1) is not None)
            for m in re.finditer(r'"([^\"]+)"|(\S+)',str(query or '')[:1000])][:24]

def fts_query(query):
    groups=[];highlights=[]
    for term,phrase in query_terms(query):
        tokens=re.findall(r'[^\W_]+',term,flags=re.UNICODE)
        if not tokens:continue
        clean=' '.join(tokens);alternatives=[clean]
        if not phrase:alternatives+=SYNONYMS.get(clean.casefold(),[])
        highlights+=alternatives
        groups.append('('+' OR '.join('"'+x.replace('"','""')+'"'+('' if phrase or ' ' in x else '*') for x in alternatives)+')')
    return ' AND '.join(groups),list(dict.fromkeys(highlights))

def fts_groups(query):
    return [value for term,phrase in query_terms(query)
            if (value:=fts_query('"'+term+'"' if phrase else term)[0])]

def snippet(text,terms,limit=370):
    flattened=re.sub(r'\s+',' ',text).strip()
    if len(flattened)<=limit:return flattened
    positions=[flattened.casefold().find(t.casefold()) for t in terms]
    positions=[p for p in positions if p>=0]
    start=max(0,min(positions)-90) if positions else 0
    return ('…' if start else '')+flattened[start:start+limit]+('…' if start+limit<len(flattened) else '')

class CourtLibrary:
    def __init__(self,data=None,source=None,courts=None):
        self.data=Path(data or COURT_DATA);self.source=Path(source or COURT_SOURCE);self.courts=Path(courts or COURTS)
        self.index=self.data/INDEX_NAME;self.annotations=self.data/ANNOTATIONS_NAME

    def _connect(self):
        if not self.index.exists():return None
        connection=sqlite3.connect(self.index.as_uri()+'?mode=ro',uri=True,timeout=10)
        connection.row_factory=sqlite3.Row
        return connection

    def _all(self,connection):
        annotations=json_read(self.annotations,{})
        output=[]
        for row in connection.execute('SELECT metadata FROM documents'):
            item=json.loads(row[0]);own=annotations.get(item['id'],{})
            item['auto_tags']=item.get('tags',[]);item['tags']=own.get('tags',item['auto_tags'])
            item['notes']=own.get('notes','');item['manual']=own;output.append(item)
        return output

    def state(self):
        connection=self._connect()
        if connection is None:
            return {'total':0,'courts':{},'cases':[],'kinds':{},'sources':[],'tags':[],
                'total_pages':0,'text_pages':0,'no_text_pages':0,'indexed_docs':0,'missing_docs':0,
                'error_docs':0,
                'date_from':'','date_to':'','built_at':None,'source_checked_at':None,'coverage':{},
                'coverage_notes':json_read(self.data/'additions.json',{}).get('coverage_notes',[]),
                'index_status':json_read(self.data/'index_status.json',{}),
                'update_status':json_read(self.data/'update_status.json',{}),'ready':False}
        try:
            items=self._all(connection)
            meta={r['key']:json.loads(r['value']) for r in connection.execute('SELECT key,value FROM meta')}
        finally:connection.close()
        courts=Counter(i['court'] for i in items);kinds=Counter(i['kind'] for i in items)
        sources=Counter(i.get('source','') for i in items if i.get('source'))
        tags=Counter(t for i in items for t in i['tags']);cases={}
        for i in items:
            key=(i['court'],str(i['case_id']))
            cases.setdefault(key,{'court':i['court'],'id':str(i['case_id']),'title':i.get('case_title',''),'count':0})['count']+=1
        dates=sorted(i['date'] for i in items if i['date'])
        additions=json_read(self.data/'additions.json',{}) or {}
        checked=valid_checked_at(additions.get('checked_at')) or valid_checked_at(meta.get('source_checked_at'))
        return {'total':len(items),'courts':dict(courts),'cases':sorted(cases.values(),key=lambda c:(c['court'],c['id'])),
            'kinds':dict(kinds),'sources':[{'name':s,'count':n} for s,n in sorted(sources.items())],
            'tags':[{'label':t,'group':TOPIC_GROUPS.get(t,'Eigene Schlagwörter'),'count':n} for t,n in sorted(tags.items())],
            'total_pages':sum(i['pages'] for i in items),'text_pages':sum(i['text_pages'] for i in items),
            'no_text_pages':sum(i.get('no_text_pages',0) for i in items),'indexed_docs':sum(i['text_pages']>0 for i in items),
            'missing_docs':sum(i['text_status']=='missing' for i in items),'error_docs':sum(i['text_status']=='error' for i in items),
            'date_from':dates[0] if dates else '','date_to':dates[-1] if dates else '',
            'built_at':meta.get('built_at'),'source_checked_at':checked,'coverage':meta.get('coverage',{}),
            'coverage_notes':additions.get('coverage_notes',[]),
            'index_status':json_read(self.data/'index_status.json',{}),'update_status':json_read(self.data/'update_status.json',{}),'ready':True}

    def search(self,params):
        def values(*keys):
            output=[]
            for key in keys:
                value=params.get(key,[]);output+=value if isinstance(value,list) else [value]
            return [str(v) for v in output if v is not None and str(v)!='']
        def first(*keys,default=''):
            vals=values(*keys);return vals[0] if vals else default
        match,terms=fts_query(first('q'));groups=fts_groups(first('q'));connection=self._connect()
        if connection is None:return {'total':0,'page':1,'pages':0,'items':[],'highlight_terms':terms,'ready':False}
        try:
            items=self._all(connection);matches={}
            if match:
                allowed=None
                # AND applies to the whole document; a quoted phrase remains
                # contiguous within a page. Terms may also match metadata.
                for group in groups:
                    found=set()
                    for row in connection.execute("SELECT doc_id,page,snippet(pages_fts,2,'','','…',55) AS excerpt FROM pages_fts WHERE pages_fts MATCH ? ORDER BY rank",(group,)):
                        ident=row['doc_id'];found.add(ident);matches.setdefault(ident,[])
                        if len(matches[ident])<2 and not any(s['page']==row['page'] for s in matches[ident]):
                            matches[ident].append({'page':row['page'],'text':snippet(row['excerpt'],terms)})
                    for row in connection.execute('SELECT doc_id FROM metadata_fts WHERE metadata_fts MATCH ?',(group,)):
                        found.add(row['doc_id']);matches.setdefault(row['doc_id'],[])
                    allowed=found if allowed is None else allowed & found
                matches={ident:snips for ident,snips in matches.items() if ident in allowed}
            requested_tags=values('tag','tags');date_from=valid_date(first('from','date_from'));date_to=valid_date(first('to','date_to'))
            selected=[]
            for item in items:
                if match and item['id'] not in matches:continue
                if first('court') and item['court']!=first('court'):continue
                if first('case','case_id') and str(item['case_id'])!=first('case','case_id'):continue
                if first('kind','type') and item['kind']!=first('kind','type'):continue
                if first('source','author') and first('source','author') not in (item.get('source',''),item.get('author','')):continue
                if requested_tags and not set(requested_tags).issubset(item['tags']):continue
                if date_from and (not item['date'] or item['date']<date_from):continue
                if date_to and (not item['date'] or item['date']>date_to):continue
                if first('germany').lower() in ('1','true','on','yes') and not item['germany_relevance']:continue
                availability=first('text_status','text')
                if availability in ('available','searchable','text','with_text') and not item['text_pages']:continue
                if availability in ('none','no_text','without_text') and item['text_pages']:continue
                if availability in ('complete','partial','missing','error') and item['text_status']!=availability:continue
                item['snippets']=matches.get(item['id'],[]);selected.append(item)
            sort=first('sort',default='newest')
            if sort=='oldest':selected.sort(key=lambda i:(not bool(i['date']),i['date'],i['title']))
            elif sort in ('title','alphabetical'):selected.sort(key=lambda i:i['title'].casefold())
            elif sort in ('relevance','relevant') and match:
                order={ident:n for n,ident in enumerate(matches)};selected.sort(key=lambda i:order.get(i['id'],10**9))
            else:selected.sort(key=lambda i:(i['date'],i['title']),reverse=True)
            total=len(selected);pages=math.ceil(total/20)
            try:page=max(1,min(int(first('page',default='1')),max(1,pages)))
            except ValueError:page=1
            selected=selected[(page-1)*20:page*20]
            for item in selected:
                if not item['snippets']:
                    row=connection.execute('SELECT page,text FROM pages WHERE doc_id=? AND length(text)>0 ORDER BY page LIMIT 1',(item['id'],)).fetchone()
                    if row:item['snippets']=[{'page':row['page'],'text':snippet(row['text'],terms)}]
                for key in ('file_path','tag_evidence','empty_pages'):item.pop(key,None)
            return {'total':total,'page':page,'pages':pages,'items':selected,'highlight_terms':terms,'ready':True}
        finally:connection.close()

    def get(self,ident,page=1,q=''):
        connection=self._connect()
        if connection is None:raise KeyError(ident)
        try:
            item=next((i for i in self._all(connection) if i['id']==ident),None)
            if item is None:raise KeyError(ident)
            number=max(1,min(int(page),max(1,item['pages'])))
            row=connection.execute('SELECT text FROM pages WHERE doc_id=? AND page=?',(ident,number)).fetchone()
            match,terms=fts_query(q);hits=[];match_count=0
            if match:
                page_match=' OR '.join('('+group+')' for group in fts_groups(q))
                match_count=connection.execute('SELECT count(*) FROM pages_fts WHERE doc_id=? AND pages_fts MATCH ?',(ident,page_match)).fetchone()[0]
                for r in connection.execute("SELECT page,snippet(pages_fts,2,'','','…',55) AS excerpt FROM pages_fts WHERE doc_id=? AND pages_fts MATCH ? ORDER BY page LIMIT 200",(ident,page_match)):
                    hits.append({'page':r['page'],'text':snippet(r['excerpt'],terms)})
            item.pop('file_path',None)
            return {'document':item,'page':{'number':number,'text':row['text'] if row else '',
                'has_text':bool(row and re.search(r'\w',row['text']))},'matches':hits,'matches_total':match_count,'pages':item['pages'],'highlight_terms':terms}
        finally:connection.close()

    def pdf_path(self,ident):
        connection=self._connect()
        if connection is None:raise KeyError(ident)
        try:
            row=connection.execute('SELECT metadata FROM documents WHERE id=?',(ident,)).fetchone()
            if row is None:raise KeyError(ident)
            path=safe_pdf_path(json.loads(row[0])['file_path'],self.source,self.courts)
            if not path.is_file():raise FileNotFoundError(path)
            return path
        finally:connection.close()

    def save(self,ident,payload):
        document=self.get(ident)['document'];tags=payload.get('tags',document['tags']);notes=payload.get('notes',document.get('notes',''))
        if not isinstance(tags,list) or len(tags)>100 or any(not isinstance(t,str) or not t.strip() or len(t)>100 for t in tags):raise ValueError('Ungültige Schlagwortliste.')
        if not isinstance(notes,str) or len(notes)>50000:raise ValueError('Notiz ist zu lang.')
        with _SAVE_LOCK:
            annotations=json_read(self.annotations,{})
            annotations[ident]={'tags':list(dict.fromkeys(t.strip() for t in tags)),'notes':notes}
            atomic_json(self.annotations,annotations)
        return self.get(ident)['document']
