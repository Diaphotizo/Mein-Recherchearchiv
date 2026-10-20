"""Build searchable metadata and versioned reading PDFs from the BIP archive."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.request import urlopen

from lxml import html
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether
from pypdf import PdfReader

import crawler
from .taxonomy import TAGS, classify, normalize

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'recherche' / 'data'
CATALOG = DATA / 'catalog.json'
PIPELINE_VERSION = '1.0'


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_json(path, value):
    crawler.save_json(path, value)


def status(phase, message, **kwargs):
    DATA.mkdir(parents=True, exist_ok=True)
    write_json(DATA / 'status.json', {'phase': phase, 'message': message, 'time': crawler.now(), **kwargs})
    print(message, flush=True)


def source_items(source):
    items = {}
    for path in sorted((source / 'quellen').glob('posts-*.json')) + sorted((source / 'quellen').glob('pages-*.json')):
        for item in read_json(path, []):
            items[item['id']] = item
    return items


def fragment_for(item, entry, source):
    fragment = item['content']['rendered']
    if not crawler.extract_text(fragment, item['link'])[0]:
        raw = (source / 'quellen' / f"frontend-{item['id']}.html").read_bytes()
        fragment = crawler.page_fragment(raw, item['id'])
    return fragment


def paragraphs_from(fragment, url):
    document = html.fragment_fromstring(fragment, create_parent='div')
    # Older newsletters place section titles before <br> inside one long block.
    # Promote only isolated bold lines, preserving all source words and links.
    for node in document.xpath('//strong|//b'):
        text = ' '.join(node.text_content().split())
        parent = node.getparent()
        if not text or not 8 <= len(text) <= 170 or parent is None or parent.tag not in ('p','div','blockquote','li'):
            continue
        following = node.getnext()
        previous = node.getprevious()
        begins_line = (previous is None and not (parent.text or '').strip()) or (previous is not None and previous.tag=='br' and not (previous.tail or '').strip())
        ends_line = not (node.tail or '').strip() and (following is None or following.tag=='br')
        if begins_line and ends_line:
            node.tag='h3'
    normalized_fragment=html.tostring(document,encoding='unicode')
    body, links, images = crawler.extract_text(normalized_fragment, url)
    # Legacy Word/Wingdings list marker has no Unicode text meaning.
    body = body.replace('\uf0a7', '•')
    headings = set()
    for node in document.xpath('//h1|//h2|//h3|//h4|//h5|//h6|//p'):
        text = ' '.join(node.text_content().split())
        if not text or len(text) > 170:
            continue
        strong = ' '.join(' '.join(n.text_content().split()) for n in node.xpath('.//strong|.//b'))
        if str(node.tag).startswith('h') or (strong and len(strong) >= len(text) * .85):
            headings.add(normalize(text))
    paragraphs, section = [], ''
    for paragraph in re.split(r'\n\s*\n', body):
        clean = re.sub(r'\s*\[\d+\]', '', paragraph).strip()
        heading = normalize(clean) in headings and not re.match(r'^\d{1,2}[./]\d', clean)
        if heading:
            section = clean
        paragraphs.append({'text': paragraph, 'heading': heading, 'section': section})
    return paragraphs, links, images


KNOWN_BYLINE_NAMES = [
    'Sima Kadmon', 'Rolf Verleger', 'Peter Beinart', 'Michelle Alexander',
    'Norman Paech', 'Martin Breidert', 'Shir Hever', 'Andreas Zumach',
    'Michael Sfard', 'Amira Hass', 'Tamara Tamimi', 'Osama Risheq',
    'Yolande Knell', 'Gideon Levy', 'Hagar Shezaf', 'Ahmed Aziz',
    'Lubna Masarwa', 'Simon Hooper', 'Fayha Shalash', 'Ruwaida Kamal Amer',
    'Ben Sales', 'Yuval Abraham', 'Basel Adra', 'Sid Lowe', 'Oren Ziv',
    'James North', 'The Brussels Times',
]
# Reviewed signed articles: standalone signatures, not mere mentions.
SIGNED = {473: ['Martin Breidert'], 869: ['Rolf Verleger'], 3070: ['Norman Paech'],
          3306: ['Norman Paech'], 3445: ['Norman Paech'], 3696: ['Norman Paech'],
          3801: ['Norman Paech'], 5243: ['Norman Paech']}


def contributors_for(item, paragraphs):
    people = []
    def add(name, role, evidence, paragraph=None):
        if not any(p['name'] == name and p['role'] == role for p in people):
            people.append({'name': name, 'role': role, 'evidence': evidence, 'paragraph': paragraph})
    for i, para in enumerate(paragraphs):
        text = re.sub(r'\s*\[\d+\]', '', para['text'])
        if 'redaktionsteam' in normalize(text):
            team = 'BIB-Redaktion' if 'BIB' in text else 'BIP-Redaktion'
            add(team, 'Redaktion', text, i)
            if 'Shir Hever' in text:
                add('Shir Hever', 'Redaktion', text, i)
        for line in text.splitlines():
            line = line.strip()
            if not re.match(r'(?i)^(von |autor(?:in)?\s*:|text\s*:|beitrag von )', line) or len(line) >= 220:
                continue
            # A book's author in a review is not the review author.
            if 'Rezension von James North' in line:
                add('James North', 'Autor eines Teiltexts', line, i)
                continue
            for name in KNOWN_BYLINE_NAMES:
                if normalize(name) in normalize(line):
                    role = 'Autor' if i < 4 else 'Autor eines Teiltexts'
                    add(name, role, line, i)
    for name in SIGNED.get(item['id'], []):
        if not any(p['name'] == name and p['role'].startswith('Autor') for p in people):
            pos = next((i for i,p in enumerate(paragraphs) if name in p['text'] and len(p['text']) < 120), None)
            if pos is not None:
                add(name, 'Autor', paragraphs[pos]['text'], pos)
    title = crawler.plain_inline(item['title']['rendered'])
    if re.search(r'BIP-Gespr[aä]ch', title):
        guest = re.sub(r'^.*?(?:#?\d+\s*:\s*|:\s*)', '', title).strip()
        if guest and guest != title:
            add(guest, 'Gesprächsgast', title)
    return people


def kind_for(item, title):
    n = normalize(title)
    if 'gesprach' in n: return 'Gespräch / Podcast'
    if 'pressemitteilung' in n or 'presseerklarung' in n: return 'Pressemitteilung'
    if re.search(r'bi[pb].*aktuell|thema der woche', n): return 'BIP-Aktuell / Newsletter'
    if any(w in n for w in ['konferenz', 'vortrag', 'veranstaltung']): return 'Veranstaltung'
    if item['type'] == 'page': return 'Website-Seite'
    return 'Artikel'


def system_font_families():
    """Prefer Unicode system fonts, without requiring a particular OS."""
    families = []
    configured = os.environ.get('RECHERCHE_FONT_DIR')
    roots = [Path(configured)] if configured else []
    if os.environ.get('WINDIR'):
        roots.append(Path(os.environ['WINDIR']) / 'Fonts')
    roots.extend([Path('/usr/share/fonts/truetype/dejavu'),
                  Path('/usr/share/fonts/truetype/freefont'),
                  Path('/Library/Fonts'), Path.home() / 'Library' / 'Fonts'])
    for directory in roots:
        for names in [('segoeui.ttf','segoeuib.ttf','segoeuii.ttf'),
                      ('DejaVuSans.ttf','DejaVuSans-Bold.ttf','DejaVuSans-Oblique.ttf'),
                      ('FreeSans.ttf','FreeSansBold.ttf','FreeSansOblique.ttf'),
                      ('Arial.ttf','Arial Bold.ttf','Arial Italic.ttf')]:
            family = tuple(directory / name for name in names)
            if all(file.is_file() for file in family):
                families.append(family)
    return families


def font_files():
    families = system_font_families()
    if families:
        return families[0]
    # ReportLab ships these licensed TTFs with its wheel. A clean environment
    # can create German/English PDFs without downloading or bundling OS fonts.
    fonts = Path(reportlab.__file__).resolve().parent / 'fonts'
    return fonts/'Vera.ttf', fonts/'VeraBd.ttf', fonts/'VeraIt.ttf'


def setup_fonts():
    if 'BIP' in pdfmetrics.getRegisteredFontNames(): return
    for name, file in zip(('BIP','BIP-Bold','BIP-Italic'), font_files()):
        pdfmetrics.registerFont(TTFont(name, str(file)))
    pdfmetrics.registerFontFamily('BIP',normal='BIP',bold='BIP-Bold',italic='BIP-Italic',boldItalic='BIP-Bold')


RTL_RUN = re.compile(r'[\u0590-\u08ff]+(?:[ \t]+[\u0590-\u08ff]+)*')


def display_text(text):
    """Shape homogeneous RTL passages without changing archived source text."""
    if not RTL_RUN.search(text):
        return text
    from bidi.algorithm import get_display
    import arabic_reshaper
    return RTL_RUN.sub(lambda m: get_display(arabic_reshaper.reshape(m.group())), text)


def pdf_markup(text, links):
    # Retain original punctuation and Unicode; XML markup alone is escaped.
    result = escape(display_text(text)).replace('\n','<br/>')
    def linked(match):
        number = int(match.group(1))
        if 0 < number <= len(links):
            return f'<a href="{escape(links[number-1], quote=True)}" color="#13736A">[{number}]</a>'
        return match.group(0)
    return re.sub(r'\[(\d+)\]', linked, result)


def build_pdf(article, destination):
    setup_fonts()
    destination.parent.mkdir(parents=True, exist_ok=True)
    styles = {
        'title': ParagraphStyle('title',fontName='BIP-Bold',fontSize=20,leading=26,textColor=colors.HexColor('#163C40'),spaceAfter=14),
        'meta': ParagraphStyle('meta',fontName='BIP',fontSize=8.5,leading=12,textColor=colors.HexColor('#51656B'),spaceAfter=6),
        'body': ParagraphStyle('body',fontName='BIP',fontSize=10.4,leading=15.5,spaceAfter=9,splitLongWords=True,allowWidows=0,allowOrphans=0),
        'heading': ParagraphStyle('heading',fontName='BIP-Bold',fontSize=12,leading=17,spaceBefore=12,spaceAfter=8,keepWithNext=True),
        'link': ParagraphStyle('link',fontName='BIP',fontSize=8,leading=11,spaceAfter=6,splitLongWords=True),
    }
    people = article['contributors']
    author_text = '; '.join(f"{p['name']} ({p['role']})" for p in people) or 'Autor nicht angegeben'
    story = [
        Paragraph('BIP / RECHERCHEARCHIV',styles['meta']),
        Paragraph(escape(article['title']),styles['title']),
        Paragraph(escape(f"{article['date'][:10]}  |  {article['kind']}"),styles['meta']),
        Paragraph(escape(author_text),styles['meta']),
        Paragraph(f'<a href="{escape(article["url"],quote=True)}" color="#13736A">Original auf bip-jetzt.de öffnen</a>',styles['meta']),
        Paragraph(escape(f"Archivierte Textfassung · Quellstand {article['modified'][:10]} · Abruf {article['fetched_at'][:10]}"),styles['meta']),
        Spacer(1,14),
    ]
    for p in article['paragraphs']:
        # Large paragraphs split naturally across pages.
        story.append(Paragraph(pdf_markup(p['text'], article['links']),styles['heading' if p['heading'] else 'body']))
    if article['links']:
        story.append(Paragraph('Quellen und Links im Artikel',styles['heading']))
        for number,url in enumerate(article['links'],1):
            if url.startswith(('https://','http://','mailto:')):
                markup = f'[{number}] <a href="{escape(url,quote=True)}" color="#13736A">{escape(url)}</a>'
            else:
                markup = escape(f'[{number}] {url}')
            story.append(Paragraph(markup,styles['link']))
    if article['images']:
        story.append(Paragraph('Bildquellen',styles['heading']))
        for i,im in enumerate(article['images'],1):
            url = im['url']
            label = im.get('alt') or f'Bild {i}'
            if url.startswith(('http://','https://')):
                story.append(Paragraph(f'<a href="{escape(url,quote=True)}" color="#13736A">{escape(label)}</a>',styles['link']))
    def page(canvas,doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#D6E1DE'))
        canvas.line(48,43,A4[0]-48,43)
        canvas.setFont('BIP',8)
        canvas.setFillColor(colors.HexColor('#617572'))
        canvas.drawString(48,29,f"BIP-Recherche  |  Beitrag {article['id']}")
        canvas.drawRightString(A4[0]-48,29,str(doc.page))
        canvas.restoreState()
    temp = destination.with_suffix('.tmp.pdf')
    document = SimpleDocTemplate(str(temp),pagesize=A4,rightMargin=48,leftMargin=48,topMargin=43,bottomMargin=58,
        title=article['title'],author=author_text,subject='Archivierte BIP-Textfassung',pageCompression=1)
    document.build(story,onFirstPage=page,onLaterPages=page)
    reader = PdfReader(temp)
    if not reader.pages: raise RuntimeError('Leeres PDF')
    temp.replace(destination)
    return len(reader.pages)


def build(refresh=False, only=None):
    DATA.mkdir(parents=True,exist_ok=True)
    lock_path = DATA/'build.lock'
    # OS lock is released even if a process crashes; the file itself is harmless.
    lock = open(lock_path,'a+b')
    if os.name == 'nt':
        import msvcrt
        lock.seek(0)
        if lock.read(1) == b'': lock.write(b'0'); lock.flush()
        lock.seek(0)
        try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            lock.close()
            raise RuntimeError('Eine Aktualisierung läuft bereits.')
    started = crawler.now()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    source = ROOT
    try:
        old = read_json(CATALOG, {'articles':[]})
        if not old.get('articles') and not old.get('archived'):
            if any((ROOT/'PDF').rglob('*.pdf')) or any((ROOT/'versionen').rglob('quelle.json')):
                raise RuntimeError('Archivbestand ohne BIP-Quellenkatalog; bisherige Dateien und Fassungen bleiben erhalten.')
        old_by_id = {a['id']:a for a in old['articles']}
        if refresh:
            status('running','Öffentliche Artikel werden abgeglichen …')
            source = DATA/'imports'/stamp
            result = crawler.run(argparse.Namespace(output=source,delay=.7,timeout=60))
            if result: raise RuntimeError('Quellabgleich unvollständig; bestehende Sammlung bleibt verfügbar.')
        elif old.get('source_root'):
            source = Path(old['source_root'])
        elif old['articles']:
            imports = sorted((DATA/'imports').glob('*'),reverse=True)
            source = next((p for p in imports if (p/'index.json').exists() and read_json(p/'crawl_report.json',{}).get('complete')),ROOT)
        index = read_json(source/'index.json')
        if index is None: raise RuntimeError('Quellindex fehlt.')
        items = source_items(source)
        articles, changed, added, reused = [], [], [], 0
        for pos,entry in enumerate(index,1):
            if only and entry['id'] not in only: continue
            item = items[entry['id']]
            fragment = fragment_for(item,entry,source)
            paragraphs,links,images = paragraphs_from(fragment,item['link'])
            title = entry['title']
            contributors = contributors_for(item,paragraphs)
            article = {'id':item['id'],'title':title,'date':item['date'],'modified':item['modified'],
                'type':item['type'],'kind':kind_for(item,title),'url':item['link'],'contributors':contributors,
                'paragraphs':paragraphs,'links':links,'images':images,
                'fetched_at':read_json(source/'crawl_report.json',{}).get('finished_at',started),
                'source_file':str(source/entry['file']),'api_author_id':item.get('author')}
            number_match = re.search(r'#\s*(\d+)|Aktuell\s+(\d+)',title)
            article['number'] = next((s for s in number_match.groups() if s), '') if number_match else ''
            article['tag_evidence'] = classify(title,paragraphs)
            article['tags'] = list(article['tag_evidence'])
            fingerprint_data = {k:article[k] for k in ['title','date','modified','contributors','paragraphs','links','images','kind']}
            if any(RTL_RUN.search(p['text']) for p in paragraphs):
                fingerprint_data['rtl_rendering'] = 1
            fingerprint = hashlib.sha256((PIPELINE_VERSION+json.dumps(fingerprint_data,ensure_ascii=False,sort_keys=True)).encode()).hexdigest()
            article['fingerprint'] = fingerprint
            slug = crawler.filename(item,title)[:-4]
            relative_pdf = f"PDF/{item['date'][:4]}/{slug[:105]}__{fingerprint[:10]}.pdf"
            article['pdf'] = relative_pdf
            previous = old_by_id.get(item['id'])
            if previous and previous['fingerprint'] == fingerprint and (ROOT/relative_pdf).exists():
                article['pages'] = previous['pages']
                article['versions'] = previous.get('versions',[])
                reused += 1
            else:
                article['pages'] = build_pdf(article,ROOT/relative_pdf)
                article['versions'] = previous.get('versions',[]) if previous else []
                if previous and previous['fingerprint'] != fingerprint:
                    article['versions'] = article['versions'] + [{'pdf':previous['pdf'],'modified':previous['modified'],'archived_at':previous['fetched_at'],'fingerprint':previous['fingerprint']}]
                    changed.append(item['id'])
                elif not previous: added.append(item['id'])
            # Preserve the exact input for each version independently of later imports.
            version = ROOT/'versionen'/str(item['id'])/fingerprint[:16]
            if not (version/'quelle.json').exists():
                write_json(version/'quelle.json',item)
                crawler.atomic_text(version/'inhalt.html',fragment)
                crawler.atomic_text(version/'text.txt',(source/entry['file']).read_text(encoding='utf-8'))
            articles.append(article)
            if pos%40 == 0 or pos==len(index):
                status('running',f'{pos}/{len(index)} Dokumente verarbeitet',processed=pos,total=len(index))
        if only:
            write_json(DATA/'preview.json',{'articles':articles})
            status('preview','PDF-Vorschau erstellt',total=len(articles))
            return
        ids = {a['id'] for a in articles}
        removed = [a['id'] for a in old['articles'] if a['id'] not in ids]
        # Removed entries remain available in a distinct archived view.
        archived = {a['id']:a for a in old.get('archived',[])}
        for ident in removed: archived[ident] = old_by_id[ident]
        for ident in ids: archived.pop(ident,None)
        result = {'schema':1,'pipeline':PIPELINE_VERSION,'built_at':crawler.now(),
            'source_root':str(source),
            'source_checked_at': read_json(source/'crawl_report.json',{}).get('finished_at'),
            'articles':articles,'archived':list(archived.values()),
            'changes':{'new':added,'changed':changed,'removed':removed,'reused':reused},
            'taxonomy':[{'label':t['label'],'group':t['group']} for t in TAGS]}
        write_json(CATALOG,result)
        write_json(DATA/'runs'/f'{stamp}.json',{k:v for k,v in result.items() if k not in ['articles','archived','taxonomy']})
        status('complete',f'{len(articles)} Dokumente bereit. {len(added)} neu, {len(changed)} geändert.',total=len(articles),changes=result['changes'])
    except Exception as exc:
        status('error',str(exc))
        raise
    finally:
        lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--only',help='Comma-separated IDs for PDF preview only')
    args = parser.parse_args()
    build(refresh=args.refresh,only={int(x) for x in args.only.split(',')} if args.only else None)
