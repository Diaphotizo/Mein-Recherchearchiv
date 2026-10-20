#!/usr/bin/env python3
"""Export public bip-jetzt.de posts and pages as UTF-8 text. Requires lxml."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import time
import unicodedata
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, unquote
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

from lxml import html

BASE = 'https://bip-jetzt.de/'
AGENT = 'BIPTextArchive/1.0 (public article export)'
FIELDS = 'id,date,date_gmt,modified,modified_gmt,slug,link,title,content,type,status,author,categories,tags'
BLOCKS = set('p div section article header footer aside h1 h2 h3 h4 h5 h6 blockquote ul ol li figure figcaption table tr dl dt dd pre'.split())
REMOVE_TAGS = set('script style noscript svg form button input select textarea nav'.split())
REMOVE_CLASSES = {'sharedaddy', 'jp-relatedposts', 'sd-sharing-enabled', 'jetpack-likes-widget-wrapper'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex[:10] + '.tmp')
    temp.write_text(text, encoding='utf-8', newline='\n')
    # Dropbox, antivirus and an open reader may briefly hold a Windows file.
    for attempt in range(8):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(.1 * 2 ** attempt, 2))


def save_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def normal_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), unquote(parsed.path).rstrip('/') or '/', parsed.query, ''))


def plain_inline(fragment):
    return ' '.join(html.fragment_fromstring(fragment or '', create_parent='div').text_content().split())


def extract_text(fragment, base_url):
    """Preserve paragraphs, lists, captions and link targets, remove interface code."""
    root = html.fragment_fromstring(fragment or '', create_parent='div')
    for node in list(root.iterdescendants()):
        if not isinstance(node.tag, str):
            if node.getparent() is not None:
                node.drop_tree()
            continue
        if (node.tag in REMOVE_TAGS or REMOVE_CLASSES.intersection(node.get('class', '').split())):
            if node.getparent() is not None:
                node.drop_tree()
    pieces, links, images = [], [], []

    def add_link(value):
        url = urljoin(base_url, value)
        if urlsplit(url).scheme not in {'http', 'https', 'mailto'}:
            return None
        if url not in links:
            links.append(url)
        return links.index(url) + 1

    def walk(node):
        if not isinstance(node.tag, str):
            return
        tag = node.tag.lower()
        if tag in BLOCKS:
            pieces.append('\n\n')
        if tag == 'br':
            pieces.append('\n')
        if tag == 'li':
            parent = node.getparent()
            prefix = '- '
            if parent is not None and parent.tag == 'ol':
                siblings = [n for n in parent if n.tag == 'li']
                try:
                    start = int(parent.get('start', '1'))
                except ValueError:
                    start = 1
                prefix = str(start + siblings.index(node)) + '. '
            pieces.append(prefix)
        if tag == 'img':
            alt = ' '.join(node.get('alt', '').split())
            src = node.get('data-src') or node.get('src', '')
            images.append({'alt': alt, 'url': urljoin(base_url, src) if src else ''})
            if alt:
                pieces.append('[Bild: ' + alt + ']')
        if tag in {'iframe', 'video', 'audio', 'embed', 'object'}:
            src = node.get('src') or node.get('data')
            if src:
                number = add_link(src)
                if number:
                    pieces.append('\n[Medieninhalt: ' + links[number - 1] + ']\n')
        if node.text:
            pieces.append(re.sub(r'\s+', ' ', node.text))
        for child in node:
            walk(child)
            if child.tail:
                pieces.append(re.sub(r'\s+', ' ', child.tail))
        if tag == 'a' and node.get('href'):
            number = add_link(node.get('href'))
            if number:
                pieces.append(' [' + str(number) + ']')
        if tag in {'td', 'th'}:
            pieces.append(' | ')
        if tag in BLOCKS:
            pieces.append('\n\n')

    walk(root)
    body = ''.join(pieces).replace('\xa0', ' ').replace('\u200b', '')
    body = '\n'.join(re.sub(r'[ \t]+', ' ', line).strip() for line in body.splitlines())
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    return body, links, images


def filename(item, title):
    slug = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode().lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')[:85].rstrip('-') or 'ohne-titel'
    return f"{item['date'][:10]}__{item['id']}__{slug}.txt"


class Client:
    def __init__(self, delay=0.6, timeout=60):
        self.delay, self.timeout, self.last_request = delay, timeout, 0
        self.robots = None
        self.request_count = 0

    def get(self, url):
        if urlsplit(url).netloc != urlsplit(BASE).netloc:
            raise RuntimeError('Abruf ausserhalb der Quelldomain verweigert: ' + url)
        if self.robots is not None and not self.robots.can_fetch(AGENT, url):
            raise RuntimeError('robots.txt untersagt den Abruf: ' + url)
        for attempt in range(4):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            self.request_count += 1
            try:
                request = Request(url, headers={'User-Agent': AGENT, 'Accept': 'application/json, application/xml, text/html, text/plain'})
                with urlopen(request, timeout=self.timeout) as response:
                    if urlsplit(response.url).netloc != urlsplit(BASE).netloc:
                        raise RuntimeError('Unerwartete Weiterleitung: ' + response.url)
                    return response.read(), response.headers
            except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
                retryable = not isinstance(exc, HTTPError) or exc.code in {408, 429, 500, 502, 503, 504}
                if not retryable or attempt == 3:
                    raise
                wait = 2 ** (attempt + 1)
                retry_after = exc.headers.get('Retry-After') if isinstance(exc, HTTPError) else None
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        try:
                            wait = max(wait, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (TypeError, ValueError):
                            pass
                print(f'Wiederholung in {wait:.0f}s: {url}', flush=True)
                time.sleep(wait)

    def read_robots(self, out):
        url = urljoin(BASE, 'robots.txt')
        try:
            raw, _ = self.get(url)
            value = raw.decode('utf-8-sig')
        except HTTPError as exc:
            if exc.code not in {404, 410}:
                raise
            value = ''
        atomic_text(out / 'quellen' / 'robots.txt', value)
        self.robots = RobotFileParser(url)
        self.robots.parse(value.splitlines())
        self.delay = max(self.delay, self.robots.crawl_delay(AGENT) or 0)
        rate = self.robots.request_rate(AGENT)
        if rate:
            self.delay = max(self.delay, rate.seconds / rate.requests)


def fetch_collection(client, kind, out):
    items, expected, pages = {}, None, 1
    page = 1
    while page <= pages:
        query = urlencode({'per_page': 100, 'page': page, 'orderby': 'id', 'order': 'asc', 'status': 'publish', '_fields': FIELDS})
        raw, headers = client.get(BASE + 'wp-json/wp/v2/' + kind + '?' + query)
        batch = json.loads(raw)
        if not isinstance(batch, list):
            raise RuntimeError(f'{kind}: Unerwartete API-Antwort')
        total, page_count = int(headers['X-WP-Total']), int(headers['X-WP-TotalPages'])
        if expected is None:
            expected, pages = total, page_count
        elif total != expected or page_count != pages:
            raise RuntimeError(f'{kind}: Bestand waehrend Abruf geaendert; bitte neu starten')
        save_json(out / 'quellen' / f'{kind}-{page:03d}.json', batch)
        for item in batch:
            if item['id'] in items:
                raise RuntimeError(f"Doppelte ID: {item['id']}")
            items[item['id']] = item
        print(f'{kind}: Seite {page}/{pages}, {len(items)}/{expected} Eintraege', flush=True)
        page += 1
    if len(items) != expected:
        raise RuntimeError(f'{kind}: Erwartet {expected}, erhalten {len(items)}')
    return list(items.values()), expected


def sitemap_urls(client, out):
    roots = client.robots.site_maps() or [BASE + 'wp-sitemap.xml']
    queue, seen, urls = list(roots), set(), {'post': set(), 'page': set()}
    ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        raw, _ = client.get(url)
        atomic_text(out / 'quellen' / Path(urlsplit(url).path).name, raw.decode('utf-8-sig'))
        root = ET.fromstring(raw)
        if root.tag.endswith('sitemapindex'):
            for loc in root.findall('s:sitemap/s:loc', ns):
                if re.search(r'/wp-sitemap-posts-(post|page)-\d+\.xml$', loc.text or ''):
                    queue.append(loc.text)
        elif root.tag.endswith('urlset'):
            match = re.search(r'/wp-sitemap-posts-(post|page)-\d+\.xml$', url)
            if match:
                urls[match.group(1)].update(normal_url(n.text) for n in root.findall('s:url/s:loc', ns) if n.text)
    if not urls['post']:
        raise RuntimeError('Keine Artikel in der WordPress-Sitemap gefunden')
    return urls


def export_item(item, out, fetched_at):
    title = plain_inline(item['title']['rendered']) or f"Ohne Titel (ID {item['id']})"
    if item['content'].get('protected'):
        raise RuntimeError('Passwortgeschuetzter Inhalt: ' + item['link'])
    body, links, images = extract_text(item['content']['rendered'], item['link'])
    if not body:
        raise RuntimeError('Kein extrahierbarer Text: ' + item['link'])
    folder = 'artikel' if item['type'] == 'post' else 'seiten'
    relative = folder + '/' + filename(item, title)
    path = out / relative
    text = (f"{title}\n{'=' * min(len(title), 90)}\n\n"
            f"Quelle: {item['link']}\nWordPress-ID: {item['id']}\n"
            f"Inhaltstyp: {item['type']}\nVeroeffentlicht: {item['date']} (Website-Zeit)\n"
            f"Zuletzt geaendert: {item['modified']} (Website-Zeit)\nAbruf (UTC): {fetched_at}\n\n"
            f"{body}\n")
    if links:
        text += '\n\nLinks im Artikel\n' + '\n'.join(f'[{i}] {url}' for i, url in enumerate(links, 1)) + '\n'
    atomic_text(path, text)
    return {'id': item['id'], 'type': item['type'], 'title': title, 'url': item['link'],
            'date': item['date'], 'modified': item['modified'], 'file': relative,
            'body_characters': len(body), 'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
            'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'extraction_source': item.get('_extraction_source', 'wordpress_rest_api'),
            'links': links, 'images': images,
            'warnings': ['Kurzer Text; gegebenenfalls Medien-/Linkbeitrag'] if len(body) < 200 else []}


def page_fragment(raw, item_id):
    """Select a content container in an HTML page, never the whole site chrome."""
    document = html.fromstring(raw)
    selectors = [
        f"//*[@data-elementor-type='wp-page' and @data-elementor-id='{int(item_id)}']",
        # Elementor theme templates identify the page by post-ID; their own
        # data-elementor-id belongs to the template, not to the archived page.
        f"//*[@data-elementor-type='single-page' and contains(concat(' ', normalize-space(@class), ' '), ' post-{int(item_id)} ')]",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' entry-content ')]",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' wp-block-post-content ')]",
        '//main', "//*[@role='main']",
    ]
    for selector in selectors:
        nodes = document.xpath(selector)
        if len(nodes) == 1:
            fragment = html.tostring(nodes[0], encoding='unicode')
            if extract_text(fragment, BASE)[0]:
                return fragment
    raise RuntimeError('Kein eindeutiger HTML-Inhaltsbereich gefunden')


def fill_empty_content(client, item, out):
    if item['content'].get('protected'):
        return item
    if extract_text(item['content']['rendered'], item['link'])[0]:
        return item
    print('HTML-Fallback: ' + item['link'], flush=True)
    raw, _ = client.get(item['link'])
    (out / 'quellen' / f"frontend-{item['id']}.html").write_bytes(raw)
    fragment = page_fragment(raw, item['id'])
    return dict(item, content={'rendered': fragment, 'protected': False}, _extraction_source='frontend_html')


def run(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {'source': BASE, 'started_at': now(), 'complete': False, 'errors': []}
    entries = []
    client = Client(args.delay, args.timeout)
    try:
        client.read_robots(out)
        sitemaps = sitemap_urls(client, out)
        for kind, content_type in [('posts', 'post'), ('pages', 'page')]:
            items, expected = fetch_collection(client, kind, out)
            found = {normal_url(item['link']) for item in items}
            report[kind] = {'api_total': expected, 'sitemap_total': len(sitemaps[content_type]),
                            'missing_from_api': sorted(sitemaps[content_type] - found),
                            'missing_from_sitemap': sorted(found - sitemaps[content_type]), 'exported': 0}
            if len(found) != len(items):
                report['errors'].append(f'{kind}: Doppelte Quell-URLs')
            for item in items:
                try:
                    if not client.robots.can_fetch(AGENT, item['link']):
                        raise RuntimeError('robots.txt untersagt Artikel: ' + item['link'])
                    item = fill_empty_content(client, item, out)
                    entry = export_item(item, out, now())
                    entries.append(entry)
                    report[kind]['exported'] += 1
                except Exception as exc:
                    report['errors'].append(str(exc))
            if report[kind]['missing_from_api'] or report[kind]['missing_from_sitemap']:
                report['errors'].append(f'{kind}: Sitemap und API weichen voneinander ab')
        duplicates = defaultdict(list)
        for entry in entries:
            duplicates[entry['body_sha256']].append(entry['url'])
        report['identical_text_groups'] = [urls for urls in duplicates.values() if len(urls) > 1]
        report['short_texts'] = [entry['url'] for entry in entries if entry['warnings']]
        report['date_range'] = {kind: [min(e['date'] for e in entries if e['type'] == kind), max(e['date'] for e in entries if e['type'] == kind)] for kind in {e['type'] for e in entries}}
        report['complete'] = not report['errors']
    except Exception as exc:
        report['errors'].append(f'{type(exc).__name__}: {exc}')
    report['finished_at'] = now()
    report['http_requests'] = client.request_count
    report['exported_files'] = len(entries)
    # Timestamped manifests avoid presenting partial reruns as a complete older archive.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    save_json(out / 'protokolle' / f'lauf-{stamp}.json', report)
    save_json(out / 'crawl_report.json', report)
    if report['complete']:
        save_json(out / 'index.json', sorted(entries, key=lambda e: (e['date'], e['id'])))
        lines = ['BIP-TEXTARCHIV', f"Stand (UTC): {report['finished_at']}", f'Quelle: {BASE}', '']
        for entry in sorted(entries, key=lambda e: (e['date'], e['id'])):
            lines.extend([f"{entry['date'][:10]} | {entry['title']}", f"Datei: {entry['file']}", f"Quelle: {entry['url']}", ''])
        atomic_text(out / 'INHALTSVERZEICHNIS.txt', '\n'.join(lines))
    else:
        save_json(out / 'protokolle' / f'teilindex-{stamp}.json', entries)
    summary = {key: value for key, value in report.items() if key not in {'short_texts', 'identical_text_groups'}}
    summary['short_text_count'] = len(report.get('short_texts', []))
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if report['complete'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent, help='Archivordner (Standard: Skriptordner)')
    parser.add_argument('--delay', type=float, default=0.6, help='Mindestabstand zwischen Abrufen, Sekunden')
    parser.add_argument('--timeout', type=float, default=60, help='HTTP-Zeitlimit in Sekunden')
    args = parser.parse_args()
    if args.delay < 0 or args.timeout <= 0:
        parser.error('delay muss >= 0 und timeout > 0 sein')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
