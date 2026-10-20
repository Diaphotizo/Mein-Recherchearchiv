"""Serve independent research collections from one loopback-only application."""
import argparse
import hashlib
import json
import mimetypes
import sys
import threading
import subprocess
import re
import os
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs,urlsplit
from .paths import BIP, PORTAL, COURT_DATA
from .registry import COLLECTIONS
from .storage import read_json, atomic_json

sys.path.insert(0,str(BIP))
from recherche import server as bip_server
from recherche.library import Library
from .courts import CourtLibrary
from .un_library import UNLibrary, UN_DATA

WEB = PORTAL/'web'
COURT_LIBRARY = None
COURT_UPDATE = threading.Lock()
UN_LIBRARY = None
UN_UPDATE = threading.Lock()


def archive_root_fingerprint():
    """Distinguish installed copies without exposing a local filesystem path."""
    normalized = os.path.normcase(str(PORTAL.parent.resolve()))
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def health_payload():
    return {'app':bip_server.APP_ID,'portal':True,'collections':['bip','courts','un'],
            'archive_root_fingerprint':archive_root_fingerprint()}


def read_status():
    file=COURT_DATA/'update_status.json'
    update=read_json(file,{'phase':'idle','message':''})
    index=COURT_DATA/'index_status.json'
    if index.exists():
        index_state=read_json(index,{})
        update['index']=index_state
        if index_state.get('phase') in {'running','validating'} and update.get('phase')!='running':
            update={**index_state,'index':index_state,'stage':'index','phase':'running'}
    return update


def start_court_update():
    if read_status().get('phase')=='running':
        return False
    if not COURT_UPDATE.acquire(False):
        return False
    from .update_courts import atomic_json
    COURT_DATA.mkdir(parents=True,exist_ok=True)
    atomic_json(COURT_DATA/'update_status.json', {'phase':'running','message':'Aktualisierung wird gestartet …','time':datetime.now(timezone.utc).isoformat()})
    def worker():
        try:
            with (COURT_DATA/'update.log').open('w',encoding='utf-8') as log:
                result=subprocess.run([sys.executable,'-m','research_portal.update_courts'],cwd=PORTAL,
                    stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if read_status().get('phase')=='running':
                raise RuntimeError('Der Abgleich wurde unerwartet beendet. Der bisherige Suchbestand bleibt verfügbar.')
        except Exception as exc:
            atomic_json(COURT_DATA/'update_status.json', {'phase':'error','message':str(exc),'time':datetime.now(timezone.utc).isoformat(),'errors':[{'source':'Aktualisierung','error':str(exc)}]})
        finally:
            COURT_UPDATE.release()
    threading.Thread(target=worker,daemon=True).start()
    return True


def read_un_status():
    update=read_json(UN_DATA/'update_status.json',{'phase':'idle','message':''}) or {}
    index=read_json(UN_DATA/'index_status.json',{}) or {}
    update['index']=index
    if index.get('phase') in {'running','validating'} and update.get('phase')!='running':
        return {**index,'index':index,'stage':'index','phase':'running'}
    return update


def clear_interrupted_un_index():
    """Only mark a stale index operation if no indexer holds its OS lock."""
    index_file=UN_DATA/'index_status.json'
    previous=read_json(index_file,{}) or {}
    if previous.get('phase') not in {'running','validating'}:
        return
    UN_DATA.mkdir(parents=True,exist_ok=True)
    with (UN_DATA/'index.lock').open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0');handle.flush()
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:return
        else:
            import fcntl
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:return
        try:
            previous.update(phase='error',message='Die vorige UN-Indexierung wurde unterbrochen. Der bisherige Suchbestand bleibt verfügbar.')
            atomic_json(index_file,previous)
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)


def recover_un_update():
    from .update_un import update_lock
    try:
        with update_lock():
            previous=read_json(UN_DATA/'update_status.json',{}) or {}
            if previous.get('phase')=='running':
                previous.update(phase='error',message='Der vorige UN-Abgleich wurde unterbrochen. Der bisherige Suchbestand ist verfügbar; die Aktualisierung kann erneut gestartet werden.')
                atomic_json(UN_DATA/'update_status.json',previous)
            clear_interrupted_un_index()
    except RuntimeError:
        # Another server or a command-line updater still owns the operation.
        pass


def start_un_update():
    if read_un_status().get('phase')=='running' or not UN_UPDATE.acquire(False):
        return False
    try:
        from .update_un import update_lock
        UN_DATA.mkdir(parents=True,exist_ok=True)
        try:
            with update_lock():
                atomic_json(UN_DATA/'update_status.json',{'phase':'running','message':'UN-Aktualisierung wird gestartet …','time':datetime.now(timezone.utc).isoformat()})
        except RuntimeError:
            UN_UPDATE.release()
            return False
        def worker():
            try:
                with (UN_DATA/'update.log').open('w',encoding='utf-8') as log:
                    result=subprocess.run([sys.executable,'-B','-m','research_portal.update_un'],cwd=PORTAL,
                        stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                state=read_json(UN_DATA/'update_status.json',{}) or {}
                if state.get('phase') not in {'complete','partial','error'}:
                    raise RuntimeError('Der UN-Abgleich wurde unerwartet beendet. Der bisherige Suchbestand bleibt verfügbar.')
                if result.returncode and state.get('phase')=='complete':
                    raise RuntimeError('Der UN-Abgleich meldete einen Fehler. Einzelheiten stehen im Aktualisierungsprotokoll; der bisherige Suchbestand bleibt verfügbar.')
                clear_interrupted_un_index()
            except Exception as exc:
                try:
                    with update_lock():
                        try:clear_interrupted_un_index()
                        except OSError:pass
                        atomic_json(UN_DATA/'update_status.json',{'phase':'error','message':str(exc),'time':datetime.now(timezone.utc).isoformat(),
                            'errors':[{'source':'UN-Aktualisierung','error':str(exc)}]})
                except RuntimeError:
                    # A separately launched updater won the lock after dispatch.
                    # Its live status must not be overwritten by this worker.
                    pass
            finally:
                UN_UPDATE.release()
        threading.Thread(target=worker,daemon=True).start()
    except Exception:
        UN_UPDATE.release()
        raise
    return True


class Handler(bip_server.Handler):
    server_version='Recherche-Archiv/2'
    def send_pdf(self,file,filename='Gerichtsdokument.pdf'):
        size=file.stat().st_size
        start,end=0,size-1
        value=self.headers.get('Range','')
        code=200
        if value:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
            if not match or (not match[1] and not match[2]):
                self.send_error(416);return
            if not match[1]:
                start=max(0,size-int(match[2]))
            else:
                start=int(match[1])
                if match[2]:end=min(end,int(match[2]))
            if start>=size or end<start:
                self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.end_headers();return
            code=206
        self.send_response(code)
        self.send_header('Content-Type','application/pdf')
        self.send_header('Content-Length',str(end-start+1))
        self.send_header('Accept-Ranges','bytes')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Disposition',f'inline; filename="{filename}"')
        if code==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        with file.open('rb') as source:
            source.seek(start);remaining=end-start+1
            while remaining:
                chunk=source.read(min(1024*256,remaining))
                if not chunk:break
                try:self.wfile.write(chunk)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):break
                remaining-=len(chunk)
    def do_GET(self):
        if not self.host_ok():
            self.json({'error':'Ungültiger Host.'},403); return
        path=urlsplit(self.path).path
        params=parse_qs(urlsplit(self.path).query)
        try:
            if path=='/api/health':
                self.json(health_payload()); return
            if path=='/api/collections':
                self.json({'collections':COLLECTIONS}); return
            if path=='/api/courts/state':
                self.json(dict(COURT_LIBRARY.state(),csrf=bip_server.TOKEN)); return
            if path=='/api/courts/search':
                self.json(COURT_LIBRARY.search(params)); return
            if path=='/api/courts/document':
                self.json(COURT_LIBRARY.get(params['id'][0],page=int(params.get('page',['1'])[0]),q=params.get('q',[''])[0])); return
            if path=='/api/courts/status':
                self.json(read_status()); return
            if path=='/api/courts/pdf':
                file=COURT_LIBRARY.pdf_path(params['id'][0])
                self.send_pdf(file); return
            if path=='/api/un/state':
                self.json(dict(UN_LIBRARY.state(),csrf=bip_server.TOKEN));return
            if path=='/api/un/search':
                self.json(UN_LIBRARY.search(params));return
            if path=='/api/un/document':
                self.json(UN_LIBRARY.get(params['id'][0],page=int(params.get('page',['1'])[0]),q=params.get('q',[''])[0]));return
            if path=='/api/un/status':
                self.json(read_un_status());return
            if path=='/api/un/pdf':
                self.send_pdf(UN_LIBRARY.pdf_path(params['id'][0]),'UN-Dokument.pdf');return
            if path=='/api/un/txt':
                self.send_bytes(UN_LIBRARY.txt_path(params['id'][0]).read_bytes(),'text/plain; charset=utf-8',
                    extra={'Content-Disposition':'attachment; filename="UN-Dokument.txt"'});return
            assets={'/icc-icj/':'courts.html','/icc-icj':'courts.html','/portal/courts.js':'courts.js',
                    '/un/':'un.html','/un':'un.html','/portal/un.js':'un.js','/portal/un.css':'un.css','/portal/un.svg':'un.svg',
                    '/portal/portal.css':'portal.css','/portal/collections.js':'collections.js','/portal/courts.svg':'courts.svg'}
            if path in assets:
                file=WEB/assets[path]
                self.send_bytes(file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'application/octet-stream');return
            super().do_GET()
        except (KeyError,ValueError) as exc:
            self.json({'error':str(exc)},400)
        except Exception as exc:
            self.json({'error':str(exc)},500)

    def do_POST(self):
        path=urlsplit(self.path).path
        if path!='/api/shutdown' and not path.startswith(('/api/courts/','/api/un/')):
            super().do_POST(); return
        if (not self.host_ok() or self.headers.get('X-BIP-Token')!=bip_server.TOKEN
            or self.headers.get('Origin') not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}):
            self.json({'error':'Anfrage nicht autorisiert.'},403);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<=size<=100000: raise ValueError('Anfrage zu groß')
            payload=json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(payload,dict):raise ValueError('Ungültige Anfrage.')
            if path=='/api/shutdown':
                if payload.get('archive_root_fingerprint')!=archive_root_fingerprint():
                    self.json({'error':'Diese Anfrage gehört zu einem anderen Archiv.'},403);return
                locks=[]
                stopping=False
                try:
                    for lock in (bip_server.UPDATE_LOCK,COURT_UPDATE,UN_UPDATE):
                        if not lock.acquire(False):
                            self.json({'error':'Eine Aktualisierung läuft noch.'},409);return
                        locks.append(lock)
                    bip_status=read_json(bip_server.DATA/'status.json',{}) or {}
                    if (bip_status.get('phase')=='running'
                        or read_status().get('phase')=='running'
                        or read_un_status().get('phase')=='running'):
                        self.json({'error':'Eine Aktualisierung oder Indexierung läuft noch.'},409);return
                    self.json({'stopping':True})
                    stopping=True
                    # shutdown() must run outside this request's server thread.
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                finally:
                    # Keep update locks until process exit after successful shutdown;
                    # another simultaneous request must not start a refresh now.
                    if not stopping:
                        for lock in reversed(locks):lock.release()
            if path=='/api/courts/update':
                started=start_court_update()
                self.json({'started':started} if started else {'started':False,'error':'Eine Aktualisierung oder Indexierung läuft bereits.'},202 if started else 409); return
            if path=='/api/courts/annotations':
                self.json(COURT_LIBRARY.save(payload['id'],payload)); return
            if path=='/api/un/update':
                started=start_un_update()
                self.json({'started':started} if started else {'started':False,'error':'Eine UN-Aktualisierung oder Indexierung läuft bereits.'},202 if started else 409);return
            if path=='/api/un/annotations':
                self.json(UN_LIBRARY.save(payload['id'],payload));return
            self.json({'error':'Nicht gefunden.'},404)
        except (KeyError,ValueError) as exc:
            self.json({'error':str(exc)},400)
        except Exception as exc:
            self.json({'error':str(exc)},500)


def main():
    global COURT_LIBRARY,UN_LIBRARY
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8771)
    args=parser.parse_args()
    bip_server.LIBRARY=Library()
    COURT_LIBRARY=CourtLibrary()
    UN_LIBRARY=UNLibrary()
    from .update_courts import update_lock, atomic_json
    # An interrupted child must not leave the refresh button disabled forever.
    # A live updater holds this OS lock, including across server restarts.
    try:
        with update_lock():
            previous_file=COURT_DATA/'update_status.json'
            previous=json.loads(previous_file.read_text(encoding='utf-8')) if previous_file.exists() else {}
            if previous.get('phase')=='running':
                previous.update(phase='error',message='Der vorige Abgleich wurde unterbrochen. Der bisherige Suchbestand ist verfügbar; die Aktualisierung kann erneut gestartet werden.')
                atomic_json(previous_file,previous)
    except RuntimeError:
        pass
    recover_un_update()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.daemon_threads=True
    print(f'Recherche-Archiv: http://127.0.0.1:{args.port}',flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__=='__main__':
    main()
