"""Loopback-only BIP search application; no external services or account needed."""
from __future__ import annotations
import argparse
import json
import mimetypes
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs

from .build import ROOT,DATA,read_json,status
from .library import Library

APP = ROOT/'recherche'/'web'
TOKEN = secrets.token_urlsafe(32)
LIBRARY = None
UPDATE_LOCK = threading.Lock()
APP_ID = 'bip-recherche-local-v1'


def update():
    if not UPDATE_LOCK.acquire(blocking=False): return False
    state = read_json(DATA/'status.json',{})
    status('running','Aktualisierung wird gestartet …')
    def worker():
        try:
            with (DATA/'update.log').open('w',encoding='utf-8') as log:
                proc = subprocess.run([sys.executable,'-m','recherche.build','--refresh'],cwd=ROOT,
                    stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if proc.returncode and read_json(DATA/'status.json',{}).get('phase')!='error':
                    status('error','Aktualisierung fehlgeschlagen. Einzelheiten: recherche/data/update.log')
        finally: UPDATE_LOCK.release()
    threading.Thread(target=worker,daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = 'BIP-Recherche/1'
    def log_message(self,*args): pass

    def send_bytes(self,data,mime='application/json; charset=utf-8',code=200,extra=None):
        self.send_response(code)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'self'; frame-ancestors 'none'")
        for k,v in (extra or {}).items(): self.send_header(k,v)
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): pass

    def json(self,value,code=200):
        self.send_bytes(json.dumps(value,ensure_ascii=False).encode(),code=code)

    def host_ok(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.host_ok(): self.json({'error':'Ungültiger Host.'},403); return
        try:
            path = urlsplit(self.path).path
            params = parse_qs(urlsplit(self.path).query)
            if path=='/api/health': self.json({'app':APP_ID}); return
            if path=='/api/state': self.json(dict(LIBRARY.state(),csrf=TOKEN)); return
            if path=='/api/search': self.json(LIBRARY.search(params)); return
            if path=='/api/status': self.json(read_json(DATA/'status.json',{})); return
            if path=='/api/article':
                self.json(LIBRARY.get(params['id'][0])); return
            if path=='/api/pdf':
                article = LIBRARY.get(params['id'][0])
                relative = article['pdf']
                if 'version' in params:
                    versions = {v['fingerprint']:v['pdf'] for v in article['versions']}
                    relative = versions[params['version'][0]]
                filename = (ROOT/relative).resolve()
                if not filename.is_relative_to((ROOT/'PDF').resolve()): raise ValueError('Ungültiger Pfad')
                self.send_bytes(filename.read_bytes(),'application/pdf',extra={'Content-Disposition':f'inline; filename="BIP-{article["id"]}.pdf"'});return
            assets = {'/':'index.html','/app.js':'app.js','/app.css':'app.css','/favicon.svg':'favicon.svg'}
            if path in assets:
                file = APP/assets[path]
                self.send_bytes(file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'application/octet-stream');return
            self.json({'error':'Nicht gefunden.'},404)
        except (KeyError,ValueError): self.json({'error':'Ungültige Anfrage oder Dokument nicht gefunden.'},400)
        except Exception as exc: self.json({'error':str(exc)},500)

    def do_POST(self):
        expected_origin = {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
        if not self.host_ok() or self.headers.get('X-BIP-Token')!=TOKEN or self.headers.get('Origin') not in expected_origin:
            self.json({'error':'Anfrage nicht autorisiert.'},403);return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0<=size<=100000: raise ValueError('Anfrage zu groß')
            payload = json.loads(self.rfile.read(size) or b'{}')
            path = urlsplit(self.path).path
            if path=='/api/update':
                started = update()
                self.json({'started':started},202 if started else 409);return
            if path=='/api/annotations':
                self.json(LIBRARY.save(payload['id'],payload));return
            self.json({'error':'Nicht gefunden.'},404)
        except (ValueError,KeyError) as exc: self.json({'error':str(exc)},400)
        except Exception as exc: self.json({'error':str(exc)},500)


def main():
    global LIBRARY
    portal = ROOT.parent/'Recherche'
    if (portal/'research_portal'/'server.py').is_file():
        sys.path.insert(0,str(portal))
        from research_portal.server import main as portal_main
        portal_main()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8769)
    args=parser.parse_args()
    LIBRARY=Library()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.daemon_threads=True
    print(f'BIP-Recherche: http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()


if __name__=='__main__': main()
