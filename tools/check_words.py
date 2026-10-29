"""An independent check of the text: the letters and digits of every compilation, read from the raw EPUB by a minimal
extractor of its own (no code from build.py), against the letters and digits of the title's files in git on that day.

    python -X utf8 scripts/australia-law/check_words.py [--repo ../australia-law] [--today 2026-10-28] [--no-record]

The extractor takes every text node of each XHTML body in the EPUB, in document order, from the paragraph with class
LongT (the long title) to the first endnotes heading (a paragraph whose class starts with "ENotesHeading" or is one
of the notes classes EndNote, EndNote1, EndNotes, ActNotes1, ActNotesa, or a
heading, or a paragraph of class NotesSection, whose text starts "Endnotes" or "Notes to the", or is "Notes"),
leaving out elements styled display:none, paragraphs whose class starts with "TOC" (the running header is an empty paragraph; an empty notes heading ends nothing). It drops
the compilers' "[see Note N]" marks wherever they are (the record leaves them out on purpose), then keeps only letters
and digits, casefolded. Each file of the title in git gives its own stream the same way; the check passes when every file's stream occurs in the official stream, the files' spans do
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
    # skip: a later volume's cover and "About this compilation", up to its first ActHead paragraph; restate: the
    # headings at the top of a later volume that repeat the open heading of the same class (ActHead1-4).
    state = {'on': False, 'done': False, 'skip': False, 'restate': False, 'heads': {}}
    LEVELS = ('ActHead1', 'ActHead2', 'ActHead3', 'ActHead4')

    def live():
        return state['on'] and not state['done'] and not state['skip']

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
            elif text.strip() and (cls.startswith('ENotesHeading') or cls in ('EndNote', 'EndNote1', 'EndNotes', 'ActNotes1', 'ActNotesa') or (
                    (tag.startswith('h') or 'Head' in cls or cls == 'NotesSection') and
                    re.match(r'\s*(Endnotes|Notes to the |Notes\s*$|NOTES\s*$)', text))):
                state['done'] = True
                return
            if state['skip']:
                if not cls.startswith('ActHead'):
                    return
                state['skip'] = False
            if cls.startswith('TOC') or cls in ('PageBreak', 'Contents'):
                return
            if state['restate'] and text.strip():
                if cls in LEVELS and state['heads'].get(cls) == letters(text):
                    return
                state['restate'] = False
            if cls in LEVELS:
                state['heads'] = {k: v for k, v in state['heads'].items() if k < cls}
                state['heads'][cls] = letters(text)
        elif state['skip'] and tag == 'table':
            return
        if e.text and live():
            out.append(e.text)
        for c in e:
            walk(c)
            if c.tail and live():
                out.append(c.tail)
    first = True
    for u in urls:
        z = zipfile.ZipFile(io.BytesIO(cached(u)))
        names = [n for n in z.namelist() if re.search(r'\.x?html?$', n) and '/document_' in n]
        for n in sorted(names, key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', n)]):
            root = etree.fromstring(z.read(n), etree.HTMLParser())
            body = next((e for e in root.iter() if isinstance(e.tag, str) and e.tag.lower() == 'body'), None)
            if body is not None:
                if not first and state['on'] and not state['done']:
                    state['skip'] = state['restate'] = True
                first = False
                walk(body)
    # text nodes joined as they run (Word splits words across spans: '[se' 'e Note 1]'); letters() drops spaces anyway
    return letters(SEE.sub('', ''.join(out)))


def covers(o: str, parts: list[str]) -> tuple[bool, str]:
    """Whether the files tile the official stream. Positions are claimed greedily, first in the files' order and, if
    that fails, longest file first: a short file whose text also occurs inside a longer one (s-028-dup2.md sorts
    before s-028.md) can otherwise claim the long one's only place. Either way a pass needs an exact cover with no
    overlap, so the second order makes the check no weaker."""
    ok, why = _covers(o, parts)
    if not ok:
        ok2, _ = _covers(o, sorted(parts, key=lambda p: -len(p[1])))
        if ok2:
            return True, ''
    return ok, why


def _covers(o: str, parts: list[str]) -> tuple[bool, str]:
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
