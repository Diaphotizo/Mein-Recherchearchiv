"""Validate every current PDF against the archived paragraph text."""
import json
import re
import unicodedata
from pypdf import PdfReader
from .build import ROOT,DATA,read_json,write_json,display_text,RTL_RUN


def comparable(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text)).replace('\u00ad','').replace('\u200b','')


def main():
    catalog=read_json(DATA/'catalog.json')
    failures=[]
    page_count=paragraphs=0
    for i,a in enumerate(catalog['articles'],1):
        reader=PdfReader(ROOT/a['pdf'])
        page_count+=len(reader.pages)
        text=''
        for number,page in enumerate(reader.pages,1):
            raw=page.extract_text() or ''
            # Strip each page footer before removing whitespace: page 1 must not
            # accidentally consume the prefix of a page 10 footer.
            raw=raw.replace(f"BIP-Recherche  |  Beitrag {a['id']}\n{number}\n",'')
            text+=comparable(raw)
        alternate=None
        if len(reader.pages)!=a['pages']:failures.append({'id':a['id'],'reason':'page_count'})
        for p in a['paragraphs']:
            # Paragraphs split across pages contain running footers in extraction.
            expected=comparable(p['text'])
            paragraphs+=1
            if expected not in text and '\u00ad' in p['text']:
                # A source soft hyphen may become a visible hyphen at a line
                # break. Permit it only at that exact source position.
                source=re.sub(r'\s+','',unicodedata.normalize('NFKC',p['text'])).replace('\u200b','')
                pattern=re.escape(source).replace('\u00ad','-?')
                if re.search(pattern,text):
                    continue
            if expected not in text and RTL_RUN.search(p['text']):
                # pypdf drops neighboring Latin text at direction switches.
                # pdfplumber preserves every positioned glyph, in display order.
                if alternate is None:
                    import pdfplumber
                    with pdfplumber.open(ROOT/a['pdf']) as pdf:
                        alternate=''.join(comparable(page.extract_text() or '') for page in pdf.pages)
                expected=comparable(display_text(p['text']))
                if expected in alternate:
                    continue
            if expected not in text:
                failures.append({'id':a['id'],'reason':'paragraph_text','excerpt':p['text'][:100]})
        if i%75==0:print(f'{i}/{len(catalog["articles"])} PDFs geprüft',flush=True)
    result={'pdfs':len(catalog['articles']),'pages':page_count,'paragraphs':paragraphs,'failures':failures}
    write_json(DATA/'pdf_validation.json',result)
    print(json.dumps({**result,'failures':failures[:25]},ensure_ascii=True,indent=2))
    return bool(failures)


if __name__=='__main__':
    raise SystemExit(main())
