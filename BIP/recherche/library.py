"""Local search and user metadata. Source texts are never modified here."""
from collections import Counter
import copy
from pathlib import Path
import re
import threading

from .build import DATA, CATALOG, read_json, write_json
from .taxonomy import TAGS, normalize, query_groups

ROLES = ['Autor', 'Autor eines Teiltexts', 'Redaktion', 'Gesprächsgast']


class Library:
    def __init__(self, catalog_path=CATALOG, annotations_path=None):
        self.catalog_path = Path(catalog_path)
        self.annotations_path = Path(annotations_path or self.catalog_path.parent/'eigene_angaben.json')
        self.lock = threading.RLock()
        self.signature = None
        self.refresh()

    def refresh(self):
        signature = (self.catalog_path.stat().st_mtime_ns if self.catalog_path.exists() else 0,
                     self.annotations_path.stat().st_mtime_ns if self.annotations_path.exists() else 0)
        with self.lock:
            if self.signature == signature: return
            self.catalog = read_json(self.catalog_path, {
                'articles': [], 'archived': [],
                'taxonomy': [{'label': tag['label'], 'group': tag['group']} for tag in TAGS],
                'built_at': None, 'source_checked_at': None})
            self.annotations = read_json(self.annotations_path,{})
            self.articles = {}
            for archived, entries in [(False,self.catalog['articles']),(True,self.catalog.get('archived',[]))]:
                for raw in entries:
                    a = copy.deepcopy(raw)
                    personal = self.annotations.get(str(a['id']),{})
                    a['tags'] = sorted((set(a['tags']) | set(personal.get('add_tags',[]))) - set(personal.get('remove_tags',[])))
                    a['contributors'].extend(personal.get('contributors',[]))
                    a['manual'] = personal
                    a['archived'] = archived
                    a['_title'] = normalize(a['title'])
                    a['_paragraphs'] = [normalize(p['text']) for p in a['paragraphs']]
                    a['_search'] = a['_title']+' '+normalize(' '.join(p['name'] for p in a['contributors']))+' '+' '.join(a['_paragraphs'])
                    self.articles[a['id']] = a
            self.signature = signature

    def state(self):
        self.refresh()
        current = [a for a in self.articles.values() if not a['archived']]
        names,tags,kinds,roles = Counter(),Counter(),Counter(),Counter()
        for a in current:
            names.update({p['name'] for p in a['contributors']})
            roles.update({p['role'] for p in a['contributors']})
            tags.update(a['tags'])
            kinds.update([a['kind']])
        taxonomy = copy.deepcopy(self.catalog.get('taxonomy', []))
        known = {t['label'] for t in taxonomy}
        taxonomy.extend({'label':t,'group':'Eigene Schlagwörter'} for t in tags if t not in known)
        return {'total':len(current),'articles':sum(a['type']=='post' for a in current),
            'pages':sum(a['type']=='page' for a in current),'pdf_pages':sum(a['pages'] for a in current),
            'archived':len(self.catalog.get('archived',[])),
            'built_at':self.catalog.get('built_at'),'source_checked_at':self.catalog.get('source_checked_at'),
            'date_from':min((a['date'][:10] for a in current if a.get('date')),default=''),
            'date_to':max((a['date'][:10] for a in current if a.get('date')),default=''),
            'authors':[{'name':n,'count':c} for n,c in sorted(names.items())],
            'tags':[dict(t,count=tags[t['label']]) for t in taxonomy],
            'kinds':dict(kinds),'roles':list(roles),'changes':self.catalog.get('changes',{})}

    def get(self,ident):
        self.refresh()
        a = self.articles[int(ident)]
        return {k:v for k,v in a.items() if not k.startswith('_')}

    def save(self,ident,payload):
        with self.lock:
            self.refresh()
            ident = int(ident)
            a = self.articles[ident]
            tags = payload.get('tags',[])
            people = payload.get('contributors',[])
            if (not isinstance(tags,list) or len(tags)>120 or
                any(not isinstance(t,str) or not 1<=len(t.strip())<=100 for t in tags)):
                raise ValueError('Ungültige Schlagwortliste.')
            if not isinstance(people,list) or len(people)>30:
                raise ValueError('Ungültige Personenliste.')
            for p in people:
                if not isinstance(p,dict) or not isinstance(p.get('name'),str) or not 1<=len(p['name'].strip())<=100 or p.get('role') not in ROLES:
                    raise ValueError('Name oder Rolle ungültig.')
            tags = {t.strip() for t in tags}
            base = set(a['tag_evidence'])
            clean_people = [{'name':p['name'].strip(),'role':p['role'],'evidence':'Eigene ergänzte Angabe','paragraph':None,'manual':True} for p in people]
            self.annotations[str(ident)] = {
                'add_tags':sorted(tags-base),'remove_tags':sorted(base-tags),'contributors':clean_people}
            write_json(self.annotations_path,self.annotations)
            self.signature = None
            self.refresh()
            return self.get(ident)

    def search(self,params):
        self.refresh()
        one = lambda k,default='': params.get(k,[default])[0]
        q = one('q').strip()
        groups = query_groups(q)
        selected = set(params.get('tag',[]))
        author,role,kind = one('author'),one('role'),one('kind')
        date_from,date_to = one('from'),one('to')
        scope,sort = one('scope','posts'),one('sort','relevance' if q else 'newest')
        page = max(1,int(one('page','1')))
        limit = 20
        results = []
        for a in self.articles.values():
            if (scope=='archived') != a['archived']: continue
            if scope=='posts' and a['type']!='post': continue
            if scope=='pages' and a['type']!='page': continue
            if kind and a['kind']!=kind: continue
            if not selected.issubset(a['tags']): continue
            if date_from and a['date'][:10]<date_from: continue
            if date_to and a['date'][:10]>date_to: continue
            if author=='__unknown__':
                if any(p['role'].startswith('Autor') for p in a['contributors']): continue
            elif author or role:
                if not any((not author or p['name']==author) and (not role or p['role']==role) for p in a['contributors']): continue
            if any(not any(word in a['_search'] for word in group) for group in groups): continue
            title_score = sum(any(w in a['_title'] for w in group) for group in groups)*30
            paragraph_scores = []
            evidence_ids = {n for t in selected for n in a['tag_evidence'].get(t,[])}
            for i,text in enumerate(a['_paragraphs']):
                score = sum(any(w in text for w in group) for group in groups)*5
                score += 3 if i in evidence_ids else 0
                if a['paragraphs'][i]['heading'] and score: score += 1
                paragraph_scores.append((score,i))
            lead = next((i for i,p in enumerate(a['paragraphs']) if not p['heading'] and len(p['text'])>150),0)
            best = sorted(paragraph_scores,reverse=True)[:2] if groups or selected else [(0,lead)]
            snippets = []
            for score,i in best:
                if snippets and score==0: continue
                text = a['paragraphs'][i]['text']
                positions = [a['_paragraphs'][i].find(w) for group in groups for w in group if w in a['_paragraphs'][i]]
                start = max(0,min(positions)-85) if positions else 0
                if start: start = max(0,text.rfind(' ',0,start)+1)
                excerpt = text[start:start+360]
                if start: excerpt = '… '+excerpt
                if start+360<len(text): excerpt += ' …'
                section = a['paragraphs'][i]['section']
                if normalize(section)==normalize(text): section=''
                snippets.append({'paragraph':i,'text':excerpt,'section':section})
            result = {k:a[k] for k in ['id','title','date','modified','kind','number','contributors','tags','pages','url']}
            result['snippets'] = snippets
            result['score'] = title_score + max((s for s,_ in best),default=0)
            results.append(result)
        if sort=='oldest': results.sort(key=lambda a:(a['date'],a['id']))
        elif sort=='title': results.sort(key=lambda a:normalize(a['title']))
        elif sort=='relevance': results.sort(key=lambda a:(a['score'],a['date']),reverse=True)
        else: results.sort(key=lambda a:(a['date'],a['id']),reverse=True)
        total = len(results)
        pages = max(1,(total+limit-1)//limit)
        page = min(page,pages)
        return {'total':total,'page':page,'pages':pages,'items':results[(page-1)*limit:page*limit],
                'highlight_terms':[w for group in groups for w in group]}
