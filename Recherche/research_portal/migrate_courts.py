"""Copy the original courts inventory and its PDFs into the independent archive.

Original files remain untouched. A matching SHA-256 is required for every PDF;
the inventory is published only after every referenced PDF has been verified.
Repeated runs reuse verified destination files and report any conflicting file.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sys
import time
import uuid

from .paths import COURTS, COURT_SOURCE


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for attempt in range(10):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(min(.1 * 2 ** attempt, 2))
    finally:
        temporary.unlink(missing_ok=True)


def contained_pdf(root, relative):
    value = str(relative or '')
    windows = PureWindowsPath(value)
    if not value or windows.is_absolute() or windows.drive or '..' in windows.parts:
        raise ValueError('PDF-Pfad ist nicht relativ oder enthält Elternverweise: ' + value)
    path = (root / Path(*windows.parts)).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != '.pdf':
        raise ValueError('Ungültiger PDF-Pfad im Verzeichnis: ' + value)
    return path


def copy_verified(source, destination, expected):
    if destination.exists():
        if not destination.is_file() or digest(destination) != expected:
            raise ValueError('Vorhandene Zieldatei weicht von der erwarteten Prüfsumme ab: ' + str(destination))
        return 'reused', destination.stat().st_size
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.copying')
    checksum = hashlib.sha256()
    size = 0
    try:
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            while block := incoming.read(4 * 1024 * 1024):
                checksum.update(block)
                outgoing.write(block)
                size += len(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if checksum.hexdigest() != expected:
            raise ValueError('Original-PDF stimmt nicht mit der Katalog-Prüfsumme überein: ' + str(source))
        if digest(temporary) != expected:
            raise ValueError('Kopie stimmt nicht mit der Katalog-Prüfsumme überein: ' + str(destination))
        shutil.copystat(source, temporary)
        # Windows rename does not overwrite an existing destination.
        temporary.rename(destination)
        return 'copied', size
    finally:
        temporary.unlink(missing_ok=True)


def migrate(source=None, destination=None):
    source = Path(source or COURT_SOURCE).resolve()
    destination = Path(destination or COURTS / 'Bestand').resolve()
    if source == destination or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Quell- und Zielordner müssen getrennt sein.')
    started = now()
    started_clock = time.monotonic()
    report_path = destination / 'Migrationsbericht.json'
    report = {'phase': 'running', 'started_at': started, 'source': str(source),
              'destination': str(destination), 'copied': 0, 'reused': 0, 'verified_bytes': 0,
              'documents': [], 'references': [], 'errors': []}
    try:
        inventory_path = source / 'Verzeichnis.json'
        inventory_bytes = inventory_path.read_bytes()
        inventory_hash = hashlib.sha256(inventory_bytes).hexdigest()
        inventory = json.loads(inventory_bytes.decode('utf-8-sig'))
        documents = inventory.get('documents')
        if not isinstance(documents, list) or not documents:
            raise ValueError('Originalverzeichnis fehlt oder enthält keine Dokumente.')
        report.update(total=len(documents), inventory_sha256=inventory_hash)
        planned = []
        seen = {}
        for item in documents:
            relative = item.get('file')
            incoming = contained_pdf(source, relative)
            outgoing = contained_pdf(destination, relative)
            checksum = str(item.get('sha256', '')).lower()
            if not re.fullmatch(r'[a-f0-9]{64}', checksum):
                raise ValueError('Fehlende/ungültige SHA-256 für ' + str(relative))
            if not incoming.is_file():
                raise FileNotFoundError('Referenziertes Original-PDF fehlt: ' + str(incoming))
            if outgoing in seen and seen[outgoing] != checksum:
                raise ValueError('Katalog enthält widersprüchliche Zieldateien: ' + str(relative))
            seen[outgoing] = checksum
            planned.append((relative, incoming, outgoing, checksum))
        destination.mkdir(parents=True, exist_ok=True)
        required = sum(incoming.stat().st_size for _, incoming, outgoing, _ in planned if not outgoing.exists())
        if shutil.disk_usage(destination).free < required + 128 * 1024 * 1024:
            raise OSError('Nicht genügend freier Speicher für die geprüfte Kopie.')
        target_inventory = destination / 'Verzeichnis.json'
        if target_inventory.exists() and digest(target_inventory) != inventory_hash:
            raise ValueError('Im Ziel liegt bereits ein abweichendes Verzeichnis; es wurde nicht überschrieben.')
        atomic_json(report_path, report)
        for number, (relative, incoming, outgoing, checksum) in enumerate(planned, 1):
            try:
                action, size = copy_verified(incoming, outgoing, checksum)
                report[action] += 1
                report['verified_bytes'] += size
                report['documents'].append({'file': relative, 'sha256': checksum, 'bytes': size, 'action': action})
            except Exception as exc:
                report['errors'].append({'file': relative, 'error': str(exc)})
                raise
            report['processed'] = number
            if number % 25 == 0 or number == len(planned):
                report['updated_at'] = now()
                atomic_json(report_path, report)
                print(f'{number}/{len(planned)} PDFs geprüft; {report["copied"]} kopiert, {report["reused"]} wiederverwendet.', flush=True)
        # This exact prior review is consumed by the updater to preserve manually
        # rejected language/identity matches. No application launchers are copied.
        review_relative = Path('Quellenpruefung') / 'lt_manifest.json'
        review_source = source / review_relative
        if review_source.exists():
            checksum = digest(review_source)
            action, size = copy_verified(review_source, destination / review_relative, checksum)
            report['references'].append({'file': review_relative.as_posix(), 'sha256': checksum,
                                         'bytes': size, 'action': action,
                                         'purpose': 'Manuelle Sprach- und Identitätsausschlüsse des öffentlichen ICC-Registers'})
        if digest(inventory_path) != inventory_hash:
            raise ValueError('Originalverzeichnis wurde während der Übernahme verändert; Zielverzeichnis nicht veröffentlicht.')
        if not target_inventory.exists():
            temporary = target_inventory.with_name('Verzeichnis.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_bytes(inventory_bytes)
                if digest(temporary) != inventory_hash:
                    raise ValueError('Prüfsumme der Verzeichniskopie stimmt nicht.')
                temporary.rename(target_inventory)
            finally:
                temporary.unlink(missing_ok=True)
        report.update(phase='complete', finished_at=now(), elapsed_seconds=round(time.monotonic() - started_clock, 2),
                      inventory_published=True)
        atomic_json(report_path, report)
        print(f'Übernahme abgeschlossen: {len(planned)} PDFs; {report["verified_bytes"]:,} Bytes; {destination}', flush=True)
        return report
    except Exception as exc:
        report.update(phase='error', finished_at=now(), elapsed_seconds=round(time.monotonic() - started_clock, 2), error=str(exc))
        if destination.exists():
            atomic_json(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=COURT_SOURCE)
    parser.add_argument('--destination', type=Path, default=COURTS / 'Bestand')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    migrate(args.source, args.destination)


if __name__ == '__main__':
    main()
