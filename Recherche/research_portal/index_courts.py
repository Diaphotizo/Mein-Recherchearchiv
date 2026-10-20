"""Incremental, process-isolated PDF text extraction and atomic SQLite FTS index."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from .paths import COURT_DATA,COURT_SOURCE,COURTS
from .courts import INDEX_NAME,TOPICS,atomic_json,json_read,safe_pdf_path,valid_date
from .storage import require_indexed_ids

CACHE_VERSION=1

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')

def make_schema(connection):
    connection.executescript('''
    PRAGMA journal_mode=DELETE;
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE documents(id TEXT PRIMARY KEY,metadata TEXT NOT NULL);
    CREATE TABLE pages(doc_id TEXT NOT NULL,page INTEGER NOT NULL,text TEXT NOT NULL,PRIMARY KEY(doc_id,page));
    CREATE VIRTUAL TABLE pages_fts USING fts5(doc_id UNINDEXED,page UNINDEXED,text,tokenize='unicode61 remove_diacritics 2');
    CREATE VIRTUAL TABLE metadata_fts USING fts5(doc_id UNINDEXED,text,tokenize='unicode61 remove_diacritics 2');
    ''')

def kind_for(record):
    explicit=record.get('document_type') or ''
    value=(str(record.get('type',''))+' '+str(record.get('title',''))+' '+str(explicit)).casefold()
    for label,pattern in [
        ('Gutachten',r'advisory opinion'),('Urteil',r'judgment|judgement'),
        ('Beschluss / Entscheidung',r'\border\b|\bdecision\b'),
        ('Anhörung / Protokoll',r'verbatim|transcript|oral (?:statement|proceeding)|hearing'),
        ('Antrag',r'\bapplication\b|\brequest\b'),
        ('Schriftliche Stellungnahme',r'written (?:statement|observation|reply|comments)|submission|memorial|pleading'),
        ('Anlage',r'\bannex|\bappendix|\battachment'),
        ('Korrespondenz',r'\bletter\b|correspondence'),
        ('Wissenschaftlicher Beitrag',r'journal article|law review')]:
        if re.search(pattern,value):return label
    return 'Verfahrensdokument'

def string_value(value):
    if value is None:return ''
    if isinstance(value,list):return '; '.join(string_value(v) for v in value)
    if isinstance(value,dict):return str(value.get('name') or value.get('title') or value.get('label') or '')
    return str(value)

def record_metadata(record,source=COURT_SOURCE,courts=COURTS):
    value=record.get('file_path') or str(Path(source)/record.get('file',''))
    path=safe_pdf_path(value,source,courts)
    checksum=str(record.get('sha256') or '').lower()
    if not re.fullmatch(r'[a-f0-9]{64}',checksum):
        if path.is_file():
            with path.open('rb') as f:checksum=hashlib.file_digest(f,'sha256').hexdigest()
        else:checksum=hashlib.sha256(str(path).encode()).hexdigest()
    author=string_value(record.get('author') or record.get('authors'))
    court=str(record.get('court') or '')
    source_name=author or string_value(record.get('source')) or court
    return {'id':checksum,'sha256':checksum,'title':str(record.get('title') or record.get('document_id') or path.stem),
        'date':valid_date(record.get('date') or record.get('publication_date')),'original_date':str(record.get('date') or ''),
        'court':court,'case_id':str(record.get('case_id') or record.get('source_case_number') or ''),
        'case_title':str(record.get('case_title') or record.get('case_name') or ''),
        'document_id':str(record.get('document_id') or record.get('case_number') or ''),
        'kind':kind_for(record),'source':source_name,'author':author,
        'pages':int(record.get('pages') or 0),'url':record.get('record_url') or record.get('source_url') or record.get('url') or '',
        'pdf_url':record.get('pdf_url') or record.get('url') or '',
        'language':record.get('language_label') or record.get('language') or '',
        'germany_relevance':bool(record.get('germany_relevance')),'germany_note':record.get('germany_note',''),
        'file_path':str(path),'bytes':int(record.get('bytes') or 0),'source_checked_at':record.get('checked_at') or record.get('access_date'),
        'acquisition_note':record.get('acquisition_note',''),'provided_by_user':bool(record.get('provided_by_user'))}

def extract_job(job):
    """Each worker owns its PDFium instance; no PDFium calls run on threads."""
    path=Path(job['path']);cache=Path(job['cache'])
    if not path.is_file():return {'id':job['id'],'error':'PDF-Datei fehlt.','missing':True}
    try:
        info=path.stat();signature=[info.st_size,info.st_mtime_ns]
        if cache.exists():
            try:
                with gzip.open(cache,'rt',encoding='utf-8') as f:cached=json.load(f)
                if cached.get('version')==CACHE_VERSION and cached.get('signature')==signature and cached.get('sha256')==job['id']:
                    return {'id':job['id'],'cache':str(cache),'reused':True}
            except (OSError,ValueError,EOFError):pass
        with path.open('rb') as f:checksum=hashlib.file_digest(f,'sha256').hexdigest()
        if checksum!=job['id']:return {'id':job['id'],'error':'PDF-Prüfsumme stimmt nicht mit dem Quellenkatalog überein.'}
        import pypdfium2 as pdfium
        document=pdfium.PdfDocument(str(path))
        texts=[];errors=[]
        try:
            for number in range(len(document)):
                page=None;textpage=None
                try:
                    page=document[number];textpage=page.get_textpage()
                    text=textpage.get_text_bounded().replace('\r\n','\n').replace('\x00','').strip()
                    texts.append(text)
                except Exception as exc:
                    texts.append('');errors.append({'page':number+1,'error':str(exc)[:300]})
                finally:
                    if textpage is not None:textpage.close()
                    if page is not None:page.close()
        finally:document.close()
        result={'version':CACHE_VERSION,'sha256':checksum,'signature':signature,'pages':texts,'errors':errors,'extracted_at':now()}
        cache.parent.mkdir(parents=True,exist_ok=True)
        temp=cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.tmp')
        with gzip.open(temp,'wt',encoding='utf-8',compresslevel=3) as f:json.dump(result,f,ensure_ascii=False)
        temp.replace(cache)
        return {'id':job['id'],'cache':str(cache),'reused':False}
    except Exception as exc:return {'id':job['id'],'error':str(exc)[:500]}

def metadata_search_text(metadata):
    """Index source addresses and the PDF name without exposing local folders."""
    fields=['title','court','case_id','case_title','document_id','kind','source','author','url','pdf_url']
    filename=str(metadata.get('file_path') or '').replace('\\','/').rsplit('/',1)[-1]
    return ' '.join([str(metadata.get(key) or '') for key in fields]+[filename])


def add_document(connection,metadata,extracted):
    texts=extracted.get('pages',[])
    errors=extracted.get('errors',[])
    error_pages={e['page'] for e in errors}
    if texts:metadata['pages']=len(texts)
    empty=[n for n,text in enumerate(texts,1) if not re.search(r'\w',text)]
    sparse=[n for n,text in enumerate(texts,1) if 0<len(re.findall(r'[^\W\d_]',text))<40]
    text_pages=sum(bool(re.search(r'\w',text)) for text in texts)
    evidence={};compiled=[(label,re.compile(pattern,re.IGNORECASE)) for label,_,pattern in TOPICS]
    for number,text in enumerate(texts,1):
        for label,pattern in compiled:
            if pattern.search(text):evidence.setdefault(label,[]).append(number)
    metadata.update({'text_pages':text_pages,'no_text_pages':len(empty),'empty_pages':empty,
        'sparse_pages':sparse,'extraction_errors':errors,'tag_evidence':evidence,'tags':list(evidence)})
    if extracted.get('missing'):metadata['text_status']='missing'
    elif extracted.get('error'):metadata['text_status']='error'
    elif errors:metadata['text_status']='partial' if text_pages else 'error'
    elif not text_pages:metadata['text_status']='none'
    elif empty:metadata['text_status']='partial'
    else:metadata['text_status']='complete'
    metadata['text_error']=extracted.get('error','')
    if not texts and metadata['pages']:metadata['unread_pages']=metadata['pages']
    connection.execute('INSERT INTO documents VALUES (?,?)',(metadata['id'],json.dumps(metadata,ensure_ascii=False)))
    page_rows=[(metadata['id'],n,text) for n,text in enumerate(texts,1)]
    connection.executemany('INSERT INTO pages VALUES (?,?,?)',page_rows)
    connection.executemany('INSERT INTO pages_fts(doc_id,page,text) VALUES (?,?,?)',page_rows)
    searchable=metadata_search_text(metadata)
    connection.execute('INSERT INTO metadata_fts(doc_id,text) VALUES (?,?)',(metadata['id'],searchable))
    return metadata

def build_index(workers=3):
    COURT_DATA.mkdir(parents=True,exist_ok=True)
    lock=open(COURT_DATA/'index.lock','a+b')
    if os.name=='nt':
        import msvcrt
        lock.seek(0)
        if not lock.read(1):lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:lock.close();raise RuntimeError('Der ICC/ICJ-Index wird bereits aktualisiert.')
    stamp=now();temporary=COURT_DATA/('court_index.'+uuid.uuid4().hex+'.building.sqlite3')
    connection=None
    def progress(phase,message,**fields):
        value={'phase':phase,'message':message,'started_at':stamp,'time':now(),**fields}
        try:
            atomic_json(COURT_DATA/'index_status.json',value)
        except PermissionError:
            if phase not in {'running','validating'}:
                raise
            # Dropbox can temporarily hold a status file while syncing.
            # Progress reporting must not discard a valid staged index.
            print('Fortschrittsanzeige wird beim nächsten Schritt erneut geschrieben.',flush=True)
        print(message,flush=True)
    try:
        original=json_read(COURT_SOURCE/'Verzeichnis.json',{'documents':[]})
        if not isinstance(original,dict) or not isinstance(original.get('documents'),list):raise ValueError('Originalkatalog ist ungültig.')
        additions=json_read(COURT_DATA/'additions.json',{}) or {}
        records=original['documents']+additions.get('documents',[])
        metadata={}
        for record in records:
            item=record_metadata(record,source=COURT_SOURCE,courts=COURTS)
            # Byte-identical copies keep one stable document identity.
            metadata.setdefault(item['id'],item)
        if not (COURT_SOURCE/'Verzeichnis.json').is_file() or not (COURT_DATA/'additions.json').is_file() or not records:
            require_indexed_ids(COURT_DATA/INDEX_NAME,metadata)
        if not records and any(COURTS.rglob('*.pdf')):
            raise RuntimeError('PDF-Bestand ohne Quellenkatalog; vorhandene Dateien und Suche bleiben erhalten.')
        connection=sqlite3.connect(temporary)
        make_schema(connection)
        jobs=[{'id':i['id'],'path':i['file_path'],'cache':str(COURT_DATA/'text_cache'/f"{i['id']}.json.gz")} for i in metadata.values()]
        total=len(jobs);done=pages=text_pages=reused=failed=0;last_report=time.monotonic()
        progress('running',f'Volltexte werden erfasst: 0/{total} Dokumente',processed=0,total=total)
        with ProcessPoolExecutor(max_workers=max(1,min(int(workers),3))) as pool:
            futures={pool.submit(extract_job,job):job for job in jobs}
            for future in as_completed(futures):
                job=futures[future]
                try:result=future.result()
                except Exception as exc:result={'id':job['id'],'error':str(exc)[:500]}
                if result.get('cache'):
                    with gzip.open(result['cache'],'rt',encoding='utf-8') as f:extracted=json.load(f)
                else:extracted=result
                item=add_document(connection,metadata[job['id']],extracted)
                done+=1;pages+=item['pages'];text_pages+=item['text_pages'];reused+=int(result.get('reused',False))
                failed+=int(item['text_status'] in ('error','missing'))
                if done%30==0:connection.commit()
                if done%25==0 or done==total or time.monotonic()-last_report>20:
                    progress('running',f'{done}/{total} Dokumente · {pages:,} Seiten verarbeitet',processed=done,total=total,pages=pages,text_pages=text_pages,reused=reused,failed=failed)
                    last_report=time.monotonic()
        meta={'built_at':now(),'source_checked_at':additions.get('checked_at') or original.get('coverage',{}).get('checked_at'),
              'coverage':original.get('coverage',{}),'schema':1,'cache_version':CACHE_VERSION}
        connection.executemany('INSERT INTO meta VALUES (?,?)',[(k,json.dumps(v,ensure_ascii=False)) for k,v in meta.items()])
        connection.commit()
        progress('validating','Volltextindex wird geprüft …',processed=done,total=total,pages=pages,text_pages=text_pages,failed=failed)
        if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('SQLite-Integritätsprüfung fehlgeschlagen.')
        if connection.execute('SELECT count(*) FROM documents').fetchone()[0]!=total:raise RuntimeError('Dokumentzahl stimmt nicht.')
        connection.execute("INSERT INTO pages_fts(pages_fts) VALUES ('optimize')")
        connection.commit();connection.close();connection=None
        destination=COURT_DATA/INDEX_NAME
        for attempt in range(30):
            try:temporary.replace(destination);break
            except PermissionError:
                if attempt==29:raise
                time.sleep(.5)
        summary={'total':total,'pages':pages,'text_pages':text_pages,'failed':failed,'reused':reused,'built_at':meta['built_at']}
        progress('complete',f'{total} Dokumente mit {pages:,} Seiten durchsuchbar aufbereitet.',processed=done,**summary)
        return summary
    except Exception as exc:
        progress('error',str(exc));raise
    finally:
        if connection is not None:connection.close()
        temporary.unlink(missing_ok=True)
        lock.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args()
    build_index(args.workers)
