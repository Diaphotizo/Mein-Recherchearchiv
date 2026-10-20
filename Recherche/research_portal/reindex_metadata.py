"""Refresh only metadata search terms; keep documents, extracted pages and notes."""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import sqlite3

from .courts import INDEX_NAME, json_read
from .index_courts import metadata_search_text
from .paths import COURT_DATA


def ensure_idle(data):
    for name in ('update_status.json','index_status.json'):
        status=json_read(data/name,{}) or {}
        if status.get('phase') in {'running','validating'}:
            raise RuntimeError('Die Sammlung wird gerade aktualisiert. Bitte danach erneut versuchen.')


@contextmanager
def exclusive_file_lock(path):
    """Use the same files and OS locks as the updater and the PDF indexer."""
    with path.open('a+b') as handle:
        if os.name=='nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):handle.write(b'0');handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)


def rebuild_metadata(data=COURT_DATA):
    data=Path(data)
    ensure_idle(data)
    with ExitStack() as locks:
        # Match the updater's lock order: update first, then index.
        for name in ('update.lock','index.lock'):
            locks.enter_context(exclusive_file_lock(data/name))
        ensure_idle(data)
        connection=sqlite3.connect((data/INDEX_NAME).as_uri()+'?mode=rw',uri=True,timeout=20)
        try:
            connection.execute('BEGIN IMMEDIATE')
            rows=[(ident,metadata_search_text(json.loads(raw)))
                  for ident,raw in connection.execute('SELECT id,metadata FROM documents')]
            connection.execute('DELETE FROM metadata_fts')
            connection.executemany('INSERT INTO metadata_fts(doc_id,text) VALUES (?,?)',rows)
            ensure_idle(data)
            connection.commit()
            return len(rows)
        except BaseException:
            connection.rollback()
            raise
        finally:connection.close()


if __name__=='__main__':
    print(json.dumps({'metadata_entries':rebuild_metadata(),'pdf_text_unchanged':True}))
