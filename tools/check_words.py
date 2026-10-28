"""An independent check of the text: the letters and digits of every compilation, read from the raw EPUB by a minimal
extractor of its own (no code from build.py), against the letters and digits of the title's files in git on that day.

    python -X utf8 scripts/australia-law/check_words.py [--repo ../australia-law] [--today 2026-10-28] [--no-record]

The extractor takes every text node of each XHTML body in the EPUB, in document order, from the paragraph with class
LongT (the long title) to the first endnotes heading (a paragraph whose class starts with "ENotesHeading", or a
heading whose text starts "Endnotes" or "Notes to the"), leaving out elements styled display:none, paragraphs whose
class starts with "TOC", and the running header. It drops the compilers' "[see Note N]" marks in headings (the record
leaves them out on purpose), then keeps only letters and digits, casefolded. Each file of the title in git gives its
own stream the same way; the check passes when every file's stream occurs in the official stream, the files' spans do
not overlap, and together they cover all of it (so text missing, added, doubled or regrouped is caught; files may
come in an order other than the official one).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gitread import GitReader  # noqa: E402

CACHE = HERE / '.cache'
SEE = re.compile(r'\[\s*see\s+(?:Notes?|Table|Endnote)[^\]]*\]', re.I)
ALNUM = re.compile(r'[^\w]|_', re.U)


def letters(s: str) -> str:
    s = unicodedata.normalize('NFC', s)
    return ALNUM.sub('', s).casefold()


def cached(url: str) -> bytes:
    import hashlib
    return (CACHE / hashlib.sha256(url.encode()).hexdigest()[:32]).read_bytes()


def official(urls: list[str]) -> str:
    out: list[str] = []
    state = {'on': False, 'done': False}

    def walk(e):
        if state['done'] or not isinstance(e.tag, str):
            return
        if 'display:none' in (e.get('style') or '').replace(' ', ''):
            return
        tag = e.tag.split('}')[-1].lower()
        cls = e.get('class') or ''
        if tag in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            text = ''.join(t for t in e.itertext())
            if not state['on']:
                if cls == 'LongT':
                    state['on'] = True
                else:
                    return
            elif cls.startswith('ENotesHeading') or cls in ('EndNote', 'EndNotes', 'ActNotes1') or (
                    (tag.startswith('h') or 'Head' in cls) and re.match(r'\s*(Endnotes|Notes to the )', text)):
                state['done'] = True
                return
            if cls.startswith('TOC') or cls in ('Header', 'PageBreak', 'Contents'):
                return
        if e.text and state['on'] and not state['done']:
            out.append(e.text)
        for c in e:
            walk(c)
            if c.tail and state['on'] and not state['done']:
                out.append(c.tail)
    for u in urls:
        z = zipfile.ZipFile(io.BytesIO(cached(u)))
        for n in sorted(n for n in z.namelist() if re.search(r'\.x?html?$', n) and '/document_' in n):
            root = etree.fromstring(z.read(n), etree.HTMLParser())
            body = next((e for e in root.iter() if isinstance(e.tag, str) and e.tag.lower() == 'body'), None)
            if body is not None:
                walk(body)
    return letters(SEE.sub('', ' '.join(out)))


def covers(o: str, parts: list[str]) -> tuple[bool, str]:
    spans = []
    cur = 0
    for name, f in parts:
        if not f:
            continue
        i = o.find(f, cur)
        if i < 0:
            i = o.find(f)
        if i < 0:
            return False, f'{name}: not in the official text ({f[:60]!r})'
        # prefer a free occurrence when the first one is taken
        while any(a < i + len(f) and i < b for a, b in spans):
            j = o.find(f, i + 1)
            if j < 0:
                return False, f'{name}: only at taken positions'
            i = j
        spans.append((i, i + len(f)))
        cur = i + len(f)
    total = sum(b - a for a, b in spans)
    if total != len(o):
        spans.sort()
        gap = next(((spans[k][1], spans[k + 1][0]) for k in range(len(spans) - 1) if spans[k][1] < spans[k + 1][0]),
                   (spans[-1][1] if spans else 0, len(o)))
        return False, f'files cover {total} of {len(o)} letters; first gap {o[gap[0]:gap[0] + 60]!r}'
    return True, ''


_reader = None


def check(job) -> dict:
    global _reader
    repo, slug, day, urls = job
    if _reader is None:
        _reader = GitReader(repo)
    o = official(urls)
    files = _reader.files(f'{slug}/{day}', slug)
    parts = [(n, letters(t)) for n, t in files.items()]
    ok, why = covers(o, parts)
    return {'slug': slug, 'day': day, 'ok': ok, 'why': why, 'letters': len(o)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'australia-law'))
    ap.add_argument('--today', default='9999-12-31')
    ap.add_argument('--only', default='')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--no-record', action='store_true')
    args = ap.parse_args()
    only = set(args.only.split(',')) if args.only else None
    jobs = []
    for p in sorted((Path(args.repo) / 'data' / 'versions').glob('*.json')):
        rec = json.loads(p.read_text(encoding='utf-8'))
        if only and rec['folder'] not in only and rec['title_id'] not in only:
            continue
        for c in rec['compilations']:
            if c['in_force'] <= args.today:
                jobs.append((args.repo, rec['folder'], c['in_force'], c['urls']))
    with cf.ProcessPoolExecutor(args.workers) as ex:
        res = list(ex.map(check, jobs, chunksize=4))
    bad = [r for r in res if not r['ok']]
    for r in bad[:200]:
        print(f'MISMATCH {r["slug"]} {r["day"]}: {r["why"]}')
    n = sum(r['letters'] for r in res)
    print(f'{len(res)} compilations, {n} letters and digits, {len(bad)} mismatches')
    if not args.no_record and not only:
        p = HERE / 'verification.json'
        rec = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        rec['words'] = {'versions': len(res), 'letters': n, 'mismatches': len(bad)}
        p.write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    raise SystemExit(1 if bad else 0)


if __name__ == '__main__':
    main()
