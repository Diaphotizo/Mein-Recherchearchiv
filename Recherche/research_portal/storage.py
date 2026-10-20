"""Small JSON files with retries for transient Windows/Dropbox locks."""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

def require_indexed_ids(index, identifiers):
    """A missing source catalog must not silently discard an existing index."""
    index = Path(index)
    if not index.is_file():
        return
    connection = sqlite3.connect(index.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        old = {row[0] for row in connection.execute('SELECT id FROM documents')}
    finally:
        connection.close()
    if old - set(identifiers):
        raise RuntimeError('Quellenkatalog fehlt oder ist unvollständig; vorhandener Suchbestand bleibt erhalten.')

def read_json(path, default=None):
    path=Path(path)
    for attempt in range(8):
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            return default
        except (PermissionError,json.JSONDecodeError):
            if attempt==7:raise
            time.sleep(.03*(attempt+1))

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    content=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(content,encoding='utf-8')
    try:
        for attempt in range(8):
            try:
                temporary.replace(path);return
            except PermissionError:
                if attempt==7:
                    # Status is disposable progress, not archive/catalog data.
                    # Dropbox placeholders may deny rename while allowing write.
                    if path.name not in {'index_status.json','update_status.json'}:raise
                    with path.open('w',encoding='utf-8') as handle:
                        handle.write(content);handle.flush();os.fsync(handle.fileno())
                    return
                time.sleep(min(.1*2**attempt,2))
    finally:
        temporary.unlink(missing_ok=True)
