"""Builds a git repository of Australian (Commonwealth) law from the Federal Register of Legislation, in which every
amending Act is one commit, dated the day it took effect.

    python -X utf8 scripts/australia-law/build.py [--repo ../australia-law] [--today 2026-10-28] [--offline]
                                                 [--verify-rebuild]

The Federal Register of Legislation (www.legislation.gov.au), managed by the Office of Parliamentary Counsel, keeps
every registered compilation of an Act: its text as amended up to a date. Its public API
(api.prod.legislation.gov.au/v1) lists each Act's versions with the day each took effect and the amending Acts that
produced it, and serves each compilation as an EPUB file. Each compilation's endnotes carry an amendment history: a
row per provision naming the Acts that added, amended, repealed or substituted it ("am No 197, 2012").

We take every compilation of the acts below, compare consecutive compilations provision by provision, and credit each
change to the Act that is new in that provision's amendment history, on the day the register says that Act took effect
for this title. A change nothing explains, or that two Acts could explain, is never credited to an Act: it gets its own
commit by the register, labelled.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import textwrap
import threading
import urllib.parse
import zipfile
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = HERE / '.cache'
API = 'https://api.prod.legislation.gov.au/v1'
SITE = 'https://www.legislation.gov.au'
COMMITTER = ('Lexifina', 'australia-law@lexifina.com')
PUBLISHER = ('Federal Register of Legislation (Office of Parliamentary Counsel)', 'register@legislation.invalid')

# The acts in the record: the register's title ID, a folder name and a short name.
ACTS = [
    {'id': 'C2004Q00685', 'slug': 'constitution', 'short': 'Constitution',
     'name': 'Commonwealth of Australia Constitution Act', 'citation': '63 & 64 Vict. c. 12'},
    {'id': 'C2004A03712', 'slug': 'privacy-act', 'short': 'Privacy Act 1988', 'name': 'Privacy Act 1988',
     'citation': 'No. 119, 1988'},
]


# ---------------------------------------------------------------- the cache of retrieved files

REQUESTS: list[dict] = []   # every URL the build read, for REPRODUCE.md
_lock = threading.Lock()
DOWNLOAD = None   # set by the retrieval tools, which are not part of the published tools


def cache_path(url: str) -> Path:
    return CACHE / hashlib.sha256(url.encode()).hexdigest()[:32]


def fetch(url: str, offline: bool = False) -> bytes:
    """The retrieved file for a URL, from the file cache keyed by the URL, so a rebuild reads the same bytes."""
    path = cache_path(url)
    with _lock:
        REQUESTS.append({'url': url, 'file': path})
    if path.exists():
        return path.read_bytes()
    if offline or DOWNLOAD is None:
        raise SystemExit(f'not cached: {url}')
    return DOWNLOAD(url)


def api(path: str, **query) -> str:
    return f'{API}/{path}' + ('?' + urllib.parse.urlencode(query, quote_via=urllib.parse.quote) if query else '')


def api_json(path: str, offline: bool, **query) -> dict:
    return json.loads(fetch(api(path, **query), offline).decode('utf-8'))


def doc_url(title: str, start: str, rect: int, fmt: str = 'Epub', vol: int = 0) -> str:
    return (f"{API}/documents(titleid='{title}',start={start},retrospectivestart={start},"
            f"rectificationversionnumber={rect},type='Primary',uniqueTypeNumber=0,volumeNumber={vol},format='{fmt}')")


_DROP = re.compile('[­​‌‍﻿]')
_WS = re.compile(r'[\s    ]+')


def clean(s: str) -> str:
    """Runs of white space (spaces, line breaks, tabs, no-break spaces) become one space; soft hyphens and zero-width
    characters go, and a non-breaking hyphen (U+2011) is a hyphen. Nothing else changes."""
    return _WS.sub(' ', _DROP.sub('', s).replace('‑', '-')).strip()


MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December']
MON3 = {m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)}


def au_date(s: str) -> str | None:
    """'24 Dec 1990', '24 Sept 1991', '1 July 2000' -> ISO; the first date in the string."""
    m = re.search(r'\b(\d{1,2}) ([A-Z][a-z]{2,8})\.? (\d{4})\b', s or '')
    if not m or m.group(2)[:3].lower() not in MON3:
        return None
    return f'{m.group(3)}-{MON3[m.group(2)[:3].lower()]:02d}-{int(m.group(1)):02d}'


def long_day(day: str) -> str:
    d = dt.date.fromisoformat(day)
    return f'{d.day} {MONTHS[d.month - 1]} {d.year}'


# ---------------------------------------------------------------- the register: versions and compilations

def act_number(t: dict | None) -> tuple[int, int] | None:
    """An amending title's (number, year), as the endnotes cite it ('No. 197, 2012')."""
    if not t or not t.get('year') or not t.get('number'):
        return None
    return (int(t['number']), int(t['year']))


def register(act: dict, offline: bool) -> dict:
    """The title's record, every version (day it took effect, the amending Acts the register names for it) and, for
    each version that has a registered compilation, its EPUB file (the latest rectification)."""
    tid = act['id']
    title = api_json(f"titles('{tid}')", offline)
    vers = api_json('versions', offline, **{'$filter': f"titleId eq '{tid}'", '$orderby': 'start'})['value']
    docs = api_json('documents', offline, **{'$filter': f"titleId eq '{tid}' and type eq 'Primary' and format eq 'Epub'",
                                             '$select': 'start,rectificationVersionNumber,volumeNumber,registerId,'
                                                        'compilationNumber,sizeInBytes', '$top': '500'})['value']
    return register_from(tid, title, vers, docs)


def register_from(tid: str, title: dict, vers: list[dict], docs: list[dict]) -> dict:
    """The title's versions from the register's version list and its EPUB files: for each day, the latest rectification
    (a compilation in volumes is all the volumes of that rectification, in order). A version whose retrospective start
    differs from its start (a compilation registered to apply from an earlier day) is listed but not read."""
    best: dict[str, dict] = {}
    vols: dict[tuple, set] = {}
    for d in docs:
        day = d['start'][:10]
        if (d.get('retrospectiveStart') or d['start'])[:10] != day:
            continue
        vols.setdefault((day, d['rectificationVersionNumber']), set()).add(d['volumeNumber'])
        if day not in best or d['rectificationVersionNumber'] > best[day]['rectificationVersionNumber']:
            best[day] = d
    versions = []
    seen = set()
    for v in sorted(vers, key=lambda v: (v['start'], v['retrospectiveStart'])):
        day = v['start'][:10]
        if v['retrospectiveStart'][:10] != day or day in seen:
            continue
        seen.add(day)
        acts = []
        for r in v.get('reasons') or []:
            t = r.get('affectedByTitle') or r.get('amendedByTitle')
            key = act_number(t)
            mm = re.fullmatch(r'Act No\. (\d+) of (\d{4})', (r.get('markdown') or '').strip())
            if key is None and mm:   # a reason given only as text ('Act No. 148 of 2006')
                key = (int(mm.group(1)), int(mm.group(2)))
            acts.append({'affect': r.get('affect'), 'title': (t or {}).get('titleId'), 'name': (t or {}).get('name'),
                         'provisions': (t or {}).get('provisions'), 'key': key, 'markdown': r.get('markdown')})
        d = best.get(day)
        urls = None
        if d:
            vs = sorted(vols[(day, d['rectificationVersionNumber'])])
            if vs == [0]:
                urls = [doc_url(tid, day, d['rectificationVersionNumber'])]
            else:   # volumes 1..n; a stray volume 0 next to them is not read
                urls = [doc_url(tid, day, d['rectificationVersionNumber'], vol=n) for n in vs if n]
        versions.append({'from': day, 'to': (v.get('end') or '')[:10] or None, 'register_id': v.get('registerId'),
                         'compilation': v.get('compilationNumber'), 'acts': acts, 'status': v.get('status'),
                         'rect': d['rectificationVersionNumber'] if d else None,
                         'epub': urls[0] if urls and len(urls) == 1 else (urls and '|'.join(urls)),
                         'epubs': urls, 'page': f'{SITE}/{tid}/{day}/text'})
    return {'title': title, 'versions': versions}


# ---------------------------------------------------------------- reading one compilation

HEAD_SPANS = {'CharChapNo', 'CharPartNo', 'CharDivNo', 'CharSubdNo', 'ScheduleHeadNo', 'CharAmSchNo'}
SEE_NOTE = re.compile(r'\s*\[\s*see\s+(?:Notes?|Table|Endnote)[^\]]*\]', re.I)


def _hidden(e) -> bool:
    return 'display:none' in (e.get('style') or '').replace(' ', '')


def _text(e) -> str:
    """The visible words of an element, as the page shows them inline (hidden elements are removed when the file is
    read, and a line break is a space)."""
    return clean(''.join(e.itertext()))


def prune(root):
    """Removes the elements the page hides (display:none: Word's placeholders such as 'Do not delete: Division
    placeholder'), keeping the text after them, and makes each line break a space."""
    for x in root.xpath('//*[contains(translate(@style, " ", ""), "display:none")]'):
        parent = x.getparent()
        if parent is None:
            continue
        tail = x.tail or ''
        prev = x.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or '') + tail
        else:
            parent.text = (parent.text or '') + tail
        parent.remove(x)
    for br in root.iter('br'):
        br.tail = ' ' + (br.tail or '')
    return root


def _span(e, cls: str) -> str:
    for s in e.iter():
        if isinstance(s.tag, str) and s.get('class') == cls and not _hidden(s):
            t = _text(s)
            if t:
                return t
    return ''


def _classes(e) -> set:
    return {s.get('class') for s in e.iter() if isinstance(s.tag, str) and s.get('class') and not _hidden(s)}


def _tag(e) -> str:
    return e.tag.split('}')[-1].lower() if isinstance(e.tag, str) else ''


def _list_item(e) -> bool:
    """A list item read as one block: an <li> holding no paragraphs or tables of its own (the register sets bulleted
    notes, e.g. ITAA 1936 s 26AB, and numbered articles, e.g. the ILO Constitution, as <ul>/<li>). A nested list
    stays inside its item, in order."""
    return _tag(e) == 'li' and not any(_tag(x) in PARAS + ('table',) for x in e.iterdescendants())


def blocks_of(root):
    """The document's blocks in order: paragraphs and headings, list items, and tables as wholes."""
    def walk(e):
        for c in e:
            if not isinstance(c.tag, str) or _hidden(c):
                continue
            tag = _tag(c)
            if tag == 'table':
                yield 'table', c
            elif tag in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6') or _list_item(c):
                yield 'p', c
            else:
                yield from walk(c)
    body = next(e for e in root.iter() if _tag(e) == 'body')
    yield from walk(body)


def table_rows(t) -> list[list[str]]:
    rows = []
    for tr in t.iter():
        if _tag(tr) != 'tr':
            continue
        rows.append([cell_text(td) for td in tr if _tag(td) in ('td', 'th')])
    return rows


PARAS = ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6')


def cell_text(td) -> str:
    """A table cell's words: its paragraphs and list items joined with a space (a cell without either is read
    whole)."""
    def block(x):
        return _tag(x) in PARAS or (_list_item(x) and not any(_tag(a) == 'li' for a in x.iterancestors() if a is not td))
    paras = [x for x in td.iter() if block(x) and not any(_tag(a) in PARAS for a in x.iterancestors() if a is not td)]
    if not paras:
        return _text(td)
    def bullet(x):
        return '- ' if _tag(x) == 'li' and x.getparent() is not None and _tag(x.getparent()) == 'ul' and _text(x) else ''
    return clean(' '.join(bullet(x) + _text(x) for x in paras))


def num_key(n: str, width: int = 3) -> str:
    m = re.match(r'(\d+)(.*)$', n)
    return f'{int(m.group(1)):0{width}d}{m.group(2)}' if m else n


def _n(s: str) -> tuple:
    m = re.match(r'(\d+)(.*)$', s)
    if not m:
        return (0, s)
    # Australian numbering: s 6AA is inserted between s 6 and s 6A (but s 18BAA comes after s 18BA)
    suffix = m.group(2)
    return (int(m.group(1)), '!' + suffix if suffix.startswith('AA') else suffix)


def unit_sort(key: str) -> tuple:
    """intro, covering clauses, sections, schedules (head, then clauses), in number order."""
    if key == 'intro':
        return (0, (0, ''), (0, ''), key)
    if key.startswith('cc-'):
        return (1, _n(key[3:]), (0, ''), key)
    if key.startswith('s-'):
        return (2, _n(key[2:]), (0, ''), key)
    m = re.match(r'sch(?:-([^/]+))?(?:/cl-(.+))?$', key)
    if m:
        return (3, _n(m.group(1) or '0'), _n(m.group(2)) if m.group(2) else (-1, ''), key)
    return (9, (0, key), (0, ''), key)


def unit_label(key: str) -> str:
    """The provision as Australian Acts cite it: 's 6A', 'covering cl 5', 'Sch 1 cl 3', 'Sch 2'."""
    base = re.sub(r'-dup\d+$', '', key)
    if base == 'intro':
        return 'long title and preamble'
    if base.startswith('cc-'):
        return f'covering cl {base[3:].lstrip("0")}'
    if base.startswith('s-'):
        return f's {base[2:].lstrip("0")}'
    m = re.match(r'sch(?:-([^/]+))?(?:/cl-(.+))?$', base)
    if m:
        sch = f'Sch {m.group(1)}' if m.group(1) else 'Sch'
        return f'{sch} cl {m.group(2).lstrip("0")}' if m.group(2) else sch
    return key


def file_name(key: str) -> str:
    return f'{key}.md'


def is_endnotes(e, text: str) -> bool:
    if not text.strip():   # an empty notes heading (a page-break artefact, e.g. before an as-made text's body)
        return False
    cls = e.get('class') or ''
    if cls in ('ENotesHeading1', 'EndNote', 'ActNotes1', 'ActNotesa', 'ENotesHeading2', 'EndNotes', 'EndNote1'):
        return True
    return bool((_tag(e).startswith('h') or 'Head' in cls or cls in ('Notes', 'NotesSection')) and
                re.match(r'(Endnotes|Notes to the |Notes$|NOTES$)', text))


def epub_parts(raw: bytes | list[bytes]) -> list[tuple[zipfile.ZipFile, str]]:
    """The XHTML files of a compilation in reading order: each volume's document files, volume by volume."""
    out = []
    for r in ([raw] if isinstance(raw, bytes) else raw):
        z = zipfile.ZipFile(io.BytesIO(r))
        names = [n for n in z.namelist() if re.search(r'\.x?html?$', n) and '/document_' in n]
        out += [(z, n) for n in sorted(names, key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', n)])]
    return out


def html_roots(raw: bytes | list[bytes]):
    for z, name in epub_parts(raw):
        yield prune(etree.fromstring(z.read(name), etree.HTMLParser()))


def read_version(raw: bytes | list[bytes], act: dict) -> dict:
    """One compilation as units: the long title and preamble, each covering clause (Constitution), each section, each
    schedule (its heading) and each schedule clause, with the headings of the chapters, parts and divisions that open
    before it. Then the endnotes: the amendment history per provision and the table of Acts."""
    units: dict[str, dict] = {}
    order: list[str] = []
    pending: list[str] = []
    ctx = {'phase': 'front', 'cur': None, 'sched': None, 'covering': act['slug'] == 'constitution'}
    after: list[tuple[str, object]] = []

    def unit(key: str) -> dict:
        base, n = key, 2
        while key in units:
            key = f'{base}-dup{n}'
            n += 1
        u = {'key': key, 'lines': []}
        units[key] = u
        order.append(key)
        return u

    def add_lines(lines: list[str]):
        if ctx['cur'] is None:
            pending.extend(lines)
        else:
            units[ctx['cur']]['lines'].extend(lines)

    # A compilation in volumes comes as one EPUB with a document per volume. Each later volume repeats the cover and
    # "About this compilation" (skipped, up to its first heading or its endnotes) and restates the headings still open
    # (e.g. "Schedule 1—..." and "Chapter 2—..." of TAA 1953): a restated heading equal to the open one at its level
    # is skipped, so it opens nothing twice.
    open_heads: dict[str, str] = {}
    for pi, root in enumerate(html_roots(raw)):
        skip = restate = pi > 0 and ctx['phase'] not in ('front', 'end')
        for kind, e in blocks_of(root):
            if ctx['phase'] == 'end':
                after.append((kind, e))
                continue
            cls = e.get('class') or ''
            if skip:
                if kind == 'table':
                    continue
                if is_endnotes(e, _text(e)):
                    ctx['phase'] = 'end'
                    continue
                if not cls.startswith('ActHead'):
                    continue
                skip = False
            if restate and kind == 'p' and _text(e) and not cls.startswith('TOC') and cls != 'PageBreak':
                if cls in ('ActHead1', 'ActHead2', 'ActHead3', 'ActHead4') and open_heads.get(cls) == _text(e):
                    continue
                restate = False
            if cls in ('ActHead1', 'ActHead2', 'ActHead3', 'ActHead4'):
                lvl = int(cls[-1])
                open_heads = {k: v for k, v in open_heads.items() if int(k[-1]) < lvl}
                open_heads[cls] = _text(e)
            if kind == 'table':
                if ctx['phase'] in ('body', 'intro'):
                    rows = [SEE_NOTE.sub('', ' | '.join(c for c in r if c)).strip() for r in table_rows(e)]
                    add_lines([r for r in rows if r])
                continue
            text = SEE_NOTE.sub('', _text(e)).strip()   # a compiler's "[see Note 2]" mark, wherever it is
            if _tag(e) == 'li':   # a bullet (an <ol> item's number is part of its text)
                parent = e.getparent()
                text = f'- {text}' if text and parent is not None and _tag(parent) == 'ul' else text
            if not text or cls.startswith('TOC') or cls in ('PageBreak', 'Contents'):
                continue
            if ctx['phase'] == 'front':
                if cls == 'LongT':
                    ctx['phase'] = 'intro'
                    ctx['cur'] = unit('intro')['key']
                    add_lines([text])
                continue
            if is_endnotes(e, text):
                ctx['phase'] = 'end'
                continue
            sectno = _span(e, 'CharSectno')
            if not sectno and (cls == 'ActHead5' or _tag(e) == 'h5'):
                # A section heading whose number the source does not mark (Constitution s 19).
                m = re.match(r'(\d+[A-Z]*)\.?\s', text)
                sectno = m.group(1) if m else ''
            if sectno and re.match(r'\d', sectno):
                n = sectno.rstrip('.').strip()
                if ctx['covering']:
                    key = f'cc-{n}'
                elif ctx['sched'] is not None:
                    key = (f'sch-{ctx["sched"]}' if ctx['sched'] else 'sch') + f'/cl-{num_key(n.split(".")[0], 2)}'
                else:
                    key = f's-{num_key(n)}'
                u = unit(key)
                u['lines'] = pending[:] + ['# ' + SEE_NOTE.sub('', text)]
                pending.clear()
                ctx['cur'] = u['key']
                ctx['phase'] = 'body'
                continue
            tag = _tag(e)
            spans = _classes(e)
            head = bool(spans & HEAD_SPANS) or cls in ('Schedule', 'ActHead1', 'ActHead2', 'ActHead3', 'ActHead4') \
                or tag in ('h1', 'h2', 'h3', 'h4')
            if head:
                line = '## ' + SEE_NOTE.sub('', text)
                ms = re.match(r'(?:THE )?SCHEDULES?\b\s*(\d+[A-Z]*)?', text, re.I)
                if 'CharChapNo' in spans and ctx['covering'] and not ms:
                    ctx['covering'] = False
                if ms and (cls in ('Schedule', 'ActHead1') or spans & {'CharChapNo', 'ScheduleHeadNo', 'CharAmSchNo'}
                           or tag in ('h1', 'h2')):
                    ctx['covering'] = False
                    ctx['sched'] = ms.group(1) or ''
                    u = unit(f'sch-{ctx["sched"]}' if ctx['sched'] else 'sch')
                    u['lines'] = pending[:] + [line]
                    pending.clear()
                    ctx['cur'] = u['key']
                else:
                    ctx['cur'] = None
                    pending.append(line)
                ctx['phase'] = 'body'
                continue
            add_lines([text])
    if pending and order:
        units[order[-1]]['lines'].extend(pending)
    for k in order:
        u = units[k]
        u['text'] = '\n\n'.join(u['lines']) + '\n'
        u['label'] = unit_label(k)
    notes = read_endnotes(after)
    return {'units': units, 'order': order, **notes}


# ---------------------------------------------------------------- the endnotes

ACT_REF = re.compile(r'\bNos?\.?\s+((?:\d+[A-Z]?(?:\s*,\s*|\s+and\s+))*\d+[A-Z]?)\s*,\s*(\d{4})\b')
ED_REF = re.compile(r'\bed\.?\s+C(\d+)\b')
ENTRY = re.compile(r'^(ad|am|rs|rep|renum|exp|ed|rr|orig|mod|om|reloc|ad\.|am\.|rs\.|rep\.|renum\.|exp\.)\b', re.I)


def acts_in(s: str) -> set:
    out = set()
    for m in ACT_REF.finditer(s):
        for n in re.findall(r'\d+', m.group(1)):
            out.add((int(n), int(m.group(2))))
    for m in ED_REF.finditer(s):
        out.add(('ed', int(m.group(1))))
    return out


def label_keys(label: str, sched) -> list[str] | None:
    """The unit keys an amendment-history row names: 's 6', 'S. 6', 'Note to s. 3', 'Ss. 8, 9', 'ss 13A-13F',
    'c. 10', 'cl 7.2', 'Cc. 8, 9', 'Preamble'. Ranges come back as ('range', a, b) and are resolved later."""
    lab = re.sub(r'\.{2,}.*$', '', label).strip()
    lab = re.sub(r'\([^)]*\)', '', lab)
    if re.match(r'(?i)(preamble|long title|title)\b', lab):
        return ['intro']
    m = re.search(r'(?:^|\b(?:to|of|and|in)\s+)(ss?|cc?|cl|cls|covering cl|sections?|clauses?)\.?\s*(\d.*)$', lab, re.I)
    if not m:
        return None
    kind = m.group(1).lower()
    rest = m.group(2)
    keys = []
    for a, b in re.findall(r'(\d+[A-Z]*)(?:\.\d+)*(?:\s*[–—-]\s*(\d+[A-Z]*))?', rest):
        keys.append(('range', a, b) if b else a)
    out = []
    for k in keys:
        def mk(n):
            if kind.startswith('s'):
                return f's-{num_key(n)}'
            if kind.startswith('covering') or sched is None:
                return f'cc-{n}'
            return (f'sch-{sched}' if sched else 'sch') + f'/cl-{num_key(n, 2)}'
        out.append(('range', mk(k[1]), mk(k[2])) if isinstance(k, tuple) else mk(k))
    return out


def read_endnotes(after: list) -> dict:
    """The amendment history (provision -> Acts and editorial changes cited for it) and the table of Acts
    (number, year -> name, assent, commencement), from the tables after the body."""
    history: dict[str, set] = {}
    ranges: list[tuple[str, str, set]] = []
    heads: dict[str, set] = {}
    acts: dict[tuple, dict] = {}
    label = ''
    sched = None
    part = ''
    for kind, e in after:
        if kind != 'table':
            continue
        for cells in table_rows(e):
            cells = [c for c in cells]
            if len(cells) >= 2 and re.fullmatch(r'(\d+[A-Z]?), (\d{4})', cells[1] or ''):
                n, y = re.fullmatch(r'(\d+[A-Z]?), (\d{4})', cells[1]).groups()
                if n.isdigit():
                    acts.setdefault((int(n), int(y)), {'name': re.sub(r'\s*\(C\d{4}[A-Z]\d{5}\)\s*$', '', cells[0]), 'assent': au_date(cells[2]) if len(cells) > 2 else None,
                                                       'commencement': cells[3] if len(cells) > 3 else ''})
                continue
            if len(cells) != 2:
                continue
            lab, entries = cells
            lab = re.sub(r'\s*\.{2,}\s*$', '', lab).strip()
            if not ENTRY.match(entries) and not acts_in(entries):
                if lab and not entries:
                    pass
                else:
                    continue
            if lab:
                label = lab
                ms = re.match(r'(?i)(?:the )?schedules?\s*(\d+[A-Z]*)?$', lab)
                if ms:
                    sched = ms.group(1) or ''
                    part = ''
                    history.setdefault(f'sch-{sched}' if sched else 'sch', set()).update(acts_in(entries))
                    continue
                mp = re.match(r'(?i)(chapter|part|division|subdivision|div\.|subdiv\.)\s+([0-9A-Z]+)\s*$', lab)
                if mp:
                    word = mp.group(1).lower().rstrip('.')
                    word = {'div': 'division', 'subdiv': 'subdivision'}.get(word, word)
                    if word in ('chapter', 'part'):
                        part = f'{word} {mp.group(2)}'
                        hk = part
                    else:
                        hk = f'{part}/{word} {mp.group(2)}'
                    label = '@' + (f'sch-{sched}:' if sched is not None else '') + hk
            if label.startswith('@'):
                heads.setdefault(label[1:], set()).update(acts_in(entries))
                continue
            keys = label_keys(label, sched)
            if not keys:
                continue
            found = acts_in(entries)
            for k in keys:
                if isinstance(k, tuple):
                    ranges.append((k[1], k[2], found))
                else:
                    history.setdefault(k, set()).update(found)
    return {'history': history, 'ranges': ranges, 'heads': heads, 'table': acts}


# ---------------------------------------------------------------- one act: compilations, changes, causes

def unit_history(p: dict, key: str) -> set:
    """The Acts and editorial changes a compilation's amendment history cites for a unit (ranges resolved by order)."""
    out = set(p['history'].get(key, set()))
    group = lambda k: k.split('/')[0] if '/' in k else k.split('-')[0]
    for a, b, found in p['ranges']:
        if group(a) == group(key) and unit_sort(a) <= unit_sort(key) <= unit_sort(b):
            out |= found
    return out


def head_keys(text: str) -> list[str]:
    """The heading keys ('part III', 'part III/division 1') of the '## ' lines of a unit, as the endnotes name them."""
    out = []
    part = None
    for line in text.split('\n\n'):
        m = re.match(r'## (Chapter|Part|Division|Subdivision) ([0-9A-Z]+)\b', line)
        if not m:
            continue
        w = m.group(1).lower()
        if w in ('chapter', 'part'):
            part = f'{w} {m.group(2)}'
            out.append(part)
        else:
            out.append(f'{part or ""}/{w} {m.group(2)}')
    return out


def act_label(key: tuple, table: dict, names: dict) -> str:
    n, y = key
    name = (table.get(key) or {}).get('name') or names.get(key) or ''
    return f'{name} (No. {n}, {y})' if name else f'Act No. {n}, {y}'


def raw_of(v: dict, offline: bool) -> bytes | list[bytes]:
    raws = [fetch(u, offline) for u in v['epubs']]
    return raws[0] if len(raws) == 1 else raws


def sha_of(raw: bytes | list[bytes]) -> str:
    if isinstance(raw, bytes):
        return hashlib.sha256(raw).hexdigest()
    return ' '.join(hashlib.sha256(r).hexdigest() for r in raw)


def styled(p: dict) -> bool:
    """A compilation the parser can read: it has the register's markup for sections or clauses. Old as-made texts
    (converted from print, without the markup) do not."""
    return any(k.startswith(('s-', 'cc-', 'sch')) for k in p['units'])


def build_act(act: dict, offline: bool) -> dict:
    reg = register(act, offline)
    vs = [v for v in reg['versions'] if v['epub']]
    raws = [raw_of(v, offline) for v in vs]
    with cf.ProcessPoolExecutor(4) as ex:
        parsed = list(ex.map(read_version, raws, [act] * len(vs)))
    for v, p, raw in zip(vs, parsed, raws):
        v['sha256'] = sha_of(raw)
        v.update(p)
    return analyse(act, reg, vs)


def baseline(vs: list[dict]) -> list[dict]:
    """The record starts at the first version the parser can read (an Act as made in 1988 that is on the register
    without its section markup starts at its first registered compilation)."""
    return [v for v in vs if styled(v)]


def analyse(act: dict, reg: dict, vs: list[dict]) -> dict:
    """The changes between consecutive parsed versions and their causes."""
    vs = baseline(vs)
    # When each Act took effect for this title, from the register's version list: (number, year) -> days.
    days: dict[tuple, list[str]] = {}
    names: dict[tuple, str] = {}
    ids: dict[tuple, str] = {}
    for v in reg['versions']:
        for a in v['acts']:
            if a['key']:
                days.setdefault(a['key'], [])
                if v['from'] not in days[a['key']]:
                    days[a['key']].append(v['from'])
                names[a['key']] = a['name']
                ids[a['key']] = a['title']
    table: dict[tuple, dict] = {}
    for v in vs:
        for k, t in v['table'].items():
            table.setdefault(k, t)
    changes = []
    problems = []
    for prev, cur in zip(vs, vs[1:]):
        lo, hi = prev['from'], cur['from']
        keys = sorted(set(prev['units']) | set(cur['units']), key=unit_sort)
        changed = [k for k in keys if (prev['units'].get(k) or {}).get('text') != (cur['units'].get(k) or {}).get('text')]
        in_window = {k: [d for d in ds if lo < d <= hi] for k, ds in days.items()}
        window_acts = sorted(k for k, ds in in_window.items() if ds)
        cause: dict[str, tuple] = {}
        why: dict[str, str] = {}
        for k in changed:
            p, c = prev['units'].get(k), cur['units'].get(k)
            hist = unit_history(cur, k) | (unit_history(prev, k) if c is None else set())
            new = hist - unit_history(prev, k)
            heads = set()
            for u in (p, c):
                if u:
                    for hk in head_keys(u['text']):
                        heads |= cur['heads'].get(hk, set()) | prev['heads'].get(hk, set())
            new_acts = {a for a in new if a[0] != 'ed'}
            eds = sorted(a[1] for a in new if a[0] == 'ed')
            # The Acts this provision's amendment history cites that the register says took effect in this interval.
            # (Recent compilations list amendments before they commence, so "new in the history" is not enough.)
            dated = {a for a in hist if a[0] != 'ed' and in_window.get(a)}
            src = 'provision row'
            if not dated:
                dated = {a for a in heads if a[0] != 'ed' and in_window.get(a)}
                src = 'heading row'
            if len(dated) == 1:
                a = next(iter(dated))
                ds = in_window[a]
                day = hi if hi in ds else max(ds)
                cause[k] = ('act', a, day)
                why[k] = f'amendment history ({src})' + (f'; the Act took effect on {", ".join(ds)} in this interval' if len(ds) > 1 else '')
            elif len(dated) > 1:
                day = max(d for a in dated for d in in_window[a])
                cause[k] = ('candidates', tuple(sorted(dated)), day)
                why[k] = f'{len(dated)} Acts new in the amendment history ({src})'
            elif eds:
                cause[k] = ('editorial', tuple(eds), hi)
                why[k] = 'editorial change noted in the endnotes'
            elif new_acts:
                cause[k] = ('undated', tuple(sorted(new_acts)), hi)
                why[k] = 'an Act new in the amendment history that the register does not date in this interval'
            else:
                cause[k] = ('unexplained', (), hi)
                why[k] = 'no new entry in the amendment history'
            if cause[k][0] != 'act':
                problems.append(f'{hi} {unit_label(k)} ({why[k]}{": " + ", ".join(act_label(a, table, names) if isinstance(a, tuple) else str(a) for a in cause[k][1]) if cause[k][1] else ""})')
        groups: dict[tuple, list[str]] = {}
        for k in changed:
            groups.setdefault(cause[k], []).append(k)
        changes.append({'from': hi, 'prev': prev, 'cur': cur, 'groups': groups, 'why': why, 'window': window_acts,
                        'in_window': {a: in_window[a] for a in window_acts}})
    return {'act': act, 'reg': reg, 'versions': vs, 'changes': changes, 'problems': problems, 'table': table,
            'names': names, 'ids': ids, 'days': days}


def refine(b: dict):
    """Names the kinds of change no single Act explains: printed before or after the Act took effect, style only."""
    days = b['days']
    for ch in b['changes']:
        lo, hi = ch['prev']['from'], ch['from']
        regrouped: dict[tuple, list[str]] = {}
        for g, keys in ch['groups'].items():
            for k in keys:
                ng = g
                if g[0] == 'undated':
                    ds = sorted(d for a in g[1] for d in days.get(a, []))
                    if ds and any(d > hi for d in ds):
                        ng = ('early', g[1], hi)
                        ch['why'][k] = 'the compilation prints an amendment before the day the register gives for it'
                    elif ds and all(d <= lo for d in ds):
                        ng = ('late', g[1], hi)
                        ch['why'][k] = 'the compilation prints an amendment after the day the register gives for it'
                    else:
                        ng = ('unexplained', g[1], hi)
                elif g[0] == 'unexplained':
                    p, c = ch['prev']['units'].get(k), ch['cur']['units'].get(k)
                    if p and c and style_key(p['text']) == style_key(c['text']):
                        ng = ('style', (), hi)
                        ch['why'][k] = 'letter case, punctuation or spacing only'
                regrouped.setdefault(ng, []).append(k)
        ch['groups'] = regrouped


def style_key(t: str) -> str:
    t = t.casefold()
    t = re.sub(r'[‘’“”"\'`]', '', t)
    t = re.sub(r'[–—‑\-]', '-', t)
    return re.sub(r'[\s.,:;]+', '', t)


# ---------------------------------------------------------------- the git repository

def git(repo: Path, *args: str, env: dict | None = None, input: str | None = None) -> str:
    # Bytes in and out: in text mode Windows would turn "\n" on stdin into "\r\n" in commit messages.
    out = subprocess.run(['git', *args], cwd=repo, capture_output=True, env=env,
                         input=input.encode('utf-8') if input is not None else None)
    if out.returncode:
        raise SystemExit(f'git {" ".join(args)} failed: {out.stderr.decode("utf-8", "replace")}')
    return out.stdout.decode('utf-8').replace('\r\n', '\n')


def _sun(y: int, m: int, nth: int | None = None, last: bool = False, on_or_after: int | None = None) -> dt.date:
    if last:
        d = (dt.date(y, m + 1, 1) if m < 12 else dt.date(y + 1, 1, 1)) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    d = dt.date(y, m, on_or_after or 1)
    return d + dt.timedelta(days=(6 - d.weekday()) % 7)


def canberra_offset(day: dt.date) -> int:
    """Hours ahead of UTC at noon on a day in Canberra (written out, because this machine has no tz database).
    Australian Eastern Standard Time is UTC+10. Daylight saving (UTC+11) in the ACT follows New South Wales; it changes
    at 2:00 standard time, so noon on a change day already has the new offset. The rules (tz database, 'AN' rules):
    1971-1985 from the last Sunday in October; 1986 from 19 October; 1987-1999 from the last Sunday in October; 2000 from
    the last Sunday in August (Sydney Olympics); 2001-2007 from the last Sunday in October; since 2008 from the first
    Sunday in October. It ends: 1972 on 27 February; 1973-1981 on the first Sunday in March; 1982-1983 on the first
    Sunday in April; 1984-1985 on the first Sunday in March; 1986-1989 on the third Sunday in March (Sunday on or after
    the 15th); 1990-1995 on the first Sunday in March; 1996-2005 on the last Sunday in March; 2006 on the first Sunday
    in April (Commonwealth Games); 2007 on the last Sunday in March; since 2008 on the first Sunday in April."""
    y = day.year
    if y < 1971:
        return 10
    # the end of the summer that began the year before (January to April)
    if y == 1971:
        end = None
    elif y == 1972:
        end = dt.date(1972, 2, 27)
    elif y <= 1981:
        end = _sun(y, 3)
    elif y <= 1983:
        end = _sun(y, 4)
    elif y <= 1985:
        end = _sun(y, 3)
    elif y <= 1989:
        end = _sun(y, 3, on_or_after=15)
    elif y <= 1995:
        end = _sun(y, 3)
    elif y <= 2005:
        end = _sun(y, 3, last=True)
    elif y == 2006:
        end = _sun(y, 4)
    elif y == 2007:
        end = _sun(y, 3, last=True)
    else:
        end = _sun(y, 4)
    # the start of this year's summer
    if y == 1986:
        start = dt.date(1986, 10, 19)
    elif y == 2000:
        start = _sun(y, 8, last=True)
    elif y <= 2007:
        start = _sun(y, 10, last=True)
    else:
        start = _sun(y, 10)
    if end is not None and day < end:
        return 11
    return 11 if day >= start else 10


def commit_date(day: str, seq: list[int]) -> str:
    """Noon in Canberra on the day. Git cannot record a time before 1970, so changes in force before then are dated
    1 January 1970 (UTC), one second apart in the order they took effect; the real day is in the In-force trailer."""
    d = dt.date.fromisoformat(day)
    if d.year < 1970:
        seq[0] += 1
        return f'@{seq[0]} +0000'
    off = canberra_offset(d)
    stamp = int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone(dt.timedelta(hours=off))).timestamp())
    return f'@{stamp} +{off:02d}00'


def act_slug(a: tuple) -> str:
    return f'act-{a[1]}-{a[0]}'


def author_of(a: tuple, b: dict) -> tuple[str, str]:
    name = (b['table'].get(a) or {}).get('name') or b['names'].get(a) or 'Act'
    return f'{name} (No. {a[0]}, {a[1]})', f'{act_slug(a)}@legislation.invalid'


def tag_of(a: tuple) -> str:
    return f'act/{a[1]}-{a[0]}'


def write_unit(repo: Path, slug: str, key: str, unit: dict | None):
    path = repo / slug / file_name(key)
    if unit is None:
        path.unlink(missing_ok=True)
        if path.parent != repo / slug and path.parent.exists() and not any(path.parent.iterdir()):
            path.parent.rmdir()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit['text'], encoding='utf-8', newline='\n')


KIND_RANK = {'start': 0, 'act': 1, 'candidates': 2, 'early': 3, 'late': 4, 'editorial': 5, 'style': 6, 'unexplained': 7,
             'unchanged': 8}


def plan_commits(built: list[dict]) -> list[tuple]:
    """Every commit, sorted by day: the first version of each act first on its day, then one commit per amending Act
    (in order of year and number), then the changes credited to two or more candidate Acts, then those printed early
    or late, then editorial and style changes, then the changes nothing explains."""
    commits = []
    for ai, b in enumerate(built):
        commits.append((b['versions'][0]['from'], 0, (0, 0), ai, {'kind': 'start', 'b': b}))
        for ch in b['changes']:
            if not any(g[2] == ch['from'] for g in ch['groups']):
                g = ('unchanged', (), ch['from'])
                commits.append((ch['from'], KIND_RANK['unchanged'], (0, 0), ai, {'kind': 'unchanged', 'b': b, 'ch': ch,
                                                                                 'g': g, 'keys': []}))
            for g, keys in ch['groups'].items():
                kind, x, day = g
                order = (x[1], x[0]) if kind == 'act' else (0, 0)
                commits.append((day, KIND_RANK[kind], order, ai, {'kind': kind, 'b': b, 'ch': ch, 'g': g, 'keys': keys}))
    commits.sort(key=lambda c: c[:4])
    return commits


def change_kind(p: dict | None, c: dict | None) -> str:
    if p is None:
        return 'Added'
    if c is None:
        return 'Removed'
    return 'Changed'


def listing(ch: dict, keys: list[str]) -> tuple[list[str], list[str]]:
    by: dict[str, list[str]] = {}
    labels = []
    for k in keys:
        p, c = ch['prev']['units'].get(k), ch['cur']['units'].get(k)
        labels.append(unit_label(k))
        by.setdefault(change_kind(p, c), []).append(unit_label(k))
    out = []
    for n in ('Added', 'Changed', 'Removed'):
        if n in by:
            out += textwrap.wrap(f'{n}: {", ".join(by[n])}', 110, subsequent_indent='  ', break_on_hyphens=False,
                                 break_long_words=False)
    return out, labels


def subject_short(s: str) -> str:
    return s if len(s) <= 72 else s[:69].rstrip() + '...'


def subject(short: str, labels: list[str], tail: str) -> str:
    s = f'{short}: {", ".join(labels) if labels else "no words"} ({tail})'
    if len(s) > 72:
        s = subject_short(f'{short}: {len(labels)} provisions ({tail})')   # a long Act name: still at most 72
    return s


def wrap(text: str) -> list[str]:
    return textwrap.wrap(text, 110, break_on_hyphens=False, break_long_words=False)


PRE_1970 = ['Git cannot record a date before 1970, so this commit is dated 1 January 1970; the day it took effect is',
            'in the In-force trailer.', '']


def cur_acts(b: dict, day: str) -> list[dict]:
    return next((v['acts'] for v in b['reg']['versions'] if v['from'] == day), [])


def comp_trailers(b: dict, v: dict) -> list[str]:
    out = [f'Compilation: {v["page"]}']
    if v.get('register_id'):
        out.append(f'Compilation-id: {v["register_id"]}' + (f' (compilation No. {v["compilation"]})' if v.get('compilation') else ''))
    return out


def act_trailers(b: dict, a: tuple) -> list[str]:
    t = b['table'].get(a) or {}
    out = ['Kind: Act']
    if t.get('assent'):
        out.append(f'Assented: {t["assent"]}')
    if b['ids'].get(a):
        out.append(f'Source: {SITE}/{b["ids"][a]}/asmade/text')
    return out


def start_message(b: dict, day: str):
    v = b['versions'][0]
    act = b['act']
    name = b['reg']['title']['name']
    if act['slug'] == 'constitution':
        author = (f'{act["name"]} ({act["citation"]})', 'constitution-act-1900@legislation.invalid')
        msg = [subject_short(f'{act["short"]}: {name} as enacted'), '',
               *wrap(f'The {name} as enacted by the Parliament of the United Kingdom (63 & 64 Vict. c. 12, Royal Assent '
                     f'9 July 1900): the first version the register keeps. The register dates this version {day}; the '
                     'Constitution itself took effect on 1 January 1901, the day appointed by the Queen\'s proclamation '
                     'of 17 September 1900 under covering clause 3. The day is kept as the register gives it.'), '']
        trailers = ['Kind: Act of the Parliament of the United Kingdom', 'Assented: 1900-07-09']
    elif v['register_id'] == act['id'] and act.get('key'):
        # The Act as made: its own commit, by the Act.
        a = act['key']
        author = (f'{name} (No. {a[0]}, {a[1]})', f'{act_slug(a)}@legislation.invalid')
        msg = [subject_short(f'{act["short"]}: as made'), '',
               *wrap(f'The {name} (Act No. {a[0]}, {a[1]}) as made, in force from {day} as the register\'s version list '
                     'gives it (the day its first provisions commenced).'), '']
        trailers = ['Kind: Act']
        if act.get('assent'):
            trailers.append(f'Assented: {act["assent"]}')
        trailers.append(f'Source: {SITE}/{act["id"]}/asmade/text')
    else:
        author = (f'{name} (compilation of {day})', f'{v["register_id"].lower()}@legislation.invalid')
        made = (f'The Act as made ({act["title"]["year"]}) is on the register without the markup that identifies its '
                f'sections, and no compilation exists for the amendments made before {day}'
                if act.get('unstyled_asmade') else
                'Earlier versions are not on the register as compilations the parser can read, and the amendments made '
                f'before {day}')
        msg = [subject_short(f'{act["short"]}: the Act as compiled on {day}'), '',
               *wrap(f'The {name}{" (Act " + act["citation"] + ")" if act.get("citation") else ""} as amended and in '
                     f'force on {day}: the first registered compilation ({v["register_id"]}), the first version of this '
                     f'record. {made}, so they are in this text and not separated out. The amendment history in this '
                     'compilation\'s endnotes names them.'), '']
        trailers = ['Kind: Baseline']
    trailers += [f'In-force: {day}', f'Source: {SITE}/{act["id"]}/{day}/text'] + comp_trailers(b, v)
    if day < '1970-01-01':
        msg += PRE_1970
    return author, msg, trailers


def change_message(c: dict):
    b, ch, (kind, x, day), keys = c['b'], c['ch'], c['g'], c['keys']
    short = b['act']['short']
    keys = sorted(keys, key=unit_sort)
    lst, labels = listing(ch, keys)
    hi = ch['from']
    cur = ch['cur']
    cid = cur.get('register_id') or ''
    trailers = []
    names = lambda acts: ', '.join(act_label(a, b['table'], b['names']) for a in acts)
    if kind == 'act':
        a = x
        author = author_of(a, b)
        msg = [subject(short, labels, f'No. {a[0]}, {a[1]}'), '', author[0] + '.', ''] + lst + ['']
        why = (f'The amendment history in the endnotes of the compilation of {hi} ({cid}) cites this Act for '
               f'{"this provision" if len(keys) == 1 else "these provisions"}, and the register\'s version list gives '
               f'{day} as the day it took effect for the {b["reg"]["title"]["name"]}.')
        if day != hi:
            why += f' No compilation was registered for {day}; the text is that of the next compilation, of {hi}.'
        msg += wrap(why) + ['']
        staged = [k for k in keys if 'took effect on' in ch['why'][k]]
        if staged:
            msg += wrap(f'This Act took effect for this title on {", ".join(ch["in_window"][a])}, all before the next '
                        f'compilation; the change is dated {day}.') + ['']
        heading = [k for k in keys if 'heading row' in ch['why'][k]]
        trailers += act_trailers(b, a)
        trailers.append(f'In-force: {day}')
        t = b['table'].get(a) or {}
        if t.get('assent') and day < t['assent']:
            trailers.append('Date-note: the register dates this Act\'s effect before its assent (retrospective)')
        trailers.append('Evidence: amendment history (endnotes) cites this Act for ' + ', '.join(
            unit_label(k) for k in keys if k not in heading))
        if heading:
            trailers.append('Evidence: amendment history cites this Act for a heading in ' + ', '.join(unit_label(k) for k in heading))
        trailers.append(f'Evidence: register version list dates this Act {", ".join(ch["in_window"][a])}')
    elif kind == 'candidates':
        author = PUBLISHER
        msg = [subject(short, labels, 'two or more Acts'), '']
        msg += wrap(f'The amendment history in the endnotes of the compilation of {hi} ({cid}) cites more than one Act '
                    f'that took effect between {ch["prev"]["from"]} and {hi} for '
                    f'{"this provision" if len(keys) == 1 else "these provisions"}, and no compilation separates them. '
                    'The change is credited to none of them.') + [''] + lst + ['']
        for a in x:
            msg.append(f'- {act_label(a, b["table"], b["names"])}: took effect {", ".join(ch["in_window"][a])}')
        msg.append('')
        trailers += ['Kind: two or more Acts (not credited)', f'In-force: {day}'] + [f'Candidate: {act_label(a, b["table"], b["names"])}' for a in x]
    elif kind in ('early', 'late'):
        author = PUBLISHER
        when = 'before' if kind == 'early' else 'after'
        msg = [subject(short, labels, f'printed {when} the Act took effect'), '']
        ds = sorted({d for a in x for d in b['days'].get(a, [])})
        if kind == 'early':
            when_text = (f'none of them within this compilation\'s period ({ch["prev"]["from"]} to {hi}), and '
                         f'{max(ds)} after it, so the compilation printed the change before it took effect')
        else:
            when_text = f'all before this compilation\'s period ({ch["prev"]["from"]} to {hi})'
        msg += wrap(f'The compilation of {hi} ({cid}) prints this change, and its amendment history newly cites '
                    f'{names(x)}; but the register\'s version list dates that Act {" and ".join(ds)}: {when_text}. '
                    'The text is kept as the compilation prints it, on its day, and the change is credited to no Act.') \
            + [''] + lst + ['']
        trailers += [f'Kind: printed {when} the Act took effect (not credited)', f'In-force: {hi}']
        trailers += [f'Named-in-history: {act_label(a, b["table"], b["names"])}' for a in x]
    else:
        author = PUBLISHER
        label = {'editorial': 'editorial change', 'style': 'compilation style', 'unexplained': 'no Act named',
                 'unchanged': 'compilation marker' if ch['groups'] else 'no change in the text'}[kind]
        msg = [subject(short, labels, label), '']
        if kind == 'unchanged' and ch['groups']:
            msg = [subject_short(f'{short}: compilation of {hi}'), '']
            msg += wrap(f'The compilation of {hi} ({cid}). Its changes from the compilation before took effect earlier, '
                        f'on {", ".join(sorted({g[2] for g in ch["groups"]}))}, and are in the commits of those days; its '
                        'text is the text of this record since then. This empty commit marks the compilation.')
        elif kind == 'unchanged':
            msg = [subject_short(f'{short}: compilation of {hi}, no change in the text'), '']
            msg += wrap(f'The compilation of {hi} ({cid}) has the same text, provision for provision, as the one before. '
                        'The register names ' + (names([a['key'] for a in cur_acts(b, hi) if a['key']]) or 'no Act')
                        + ' for it; whatever it changed is not in the words of this Act (or is in a part this record '
                        'does not keep, such as the endnotes). This empty commit marks the compilation.')
        elif kind == 'editorial':
            msg += wrap(f'The endnotes of the compilation of {hi} ({cid}) record an editorial change by the First '
                        f'Parliamentary Counsel (ed C{", C".join(str(n) for n in x)}) for '
                        f'{"this provision" if len(keys) == 1 else "these provisions"}. No Act made it.')
        elif kind == 'style':
            msg += wrap(f'The text of the compilation of {hi} ({cid}) differs from the one before only in letter case, '
                        'punctuation or spacing: the compilers\' style, not an amendment. No Act made it.')
        else:
            msg += wrap(f'The text of the compilation of {hi} ({cid}) differs from the one before, but the amendment '
                        'history in its endnotes cites no Act that took effect in between for '
                        f'{"this provision" if len(keys) == 1 else "these provisions"} (a restructured compilation, an '
                        'editorial change, or text moved between provisions). The change is credited to no Act.')
        msg += [''] + lst + ([''] if lst else [])
        trailers += [f'Kind: {label} (not credited)', f'In-force: {hi}']
        if x:
            trailers += [f'Named-in-history: {act_label(a, b["table"], b["names"])}' for a in x if isinstance(a, tuple)]
    trailers += [f'Affecting: {short} {l}' for l in labels]
    trailers += comp_trailers(b, cur)
    if day < '1970-01-01':
        msg += PRE_1970
    return author, msg, trailers, keys


def write_repo(repo: Path, built: list[dict], docs: Path, today: str) -> tuple[list[dict], dict]:
    remote = None
    if (repo / '.git').exists():
        out = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=repo, capture_output=True, text=True)
        remote = out.stdout.strip() or None

    def force(fn, path, _exc):
        os.chmod(path, 0o700)
        fn(path)
    if repo.exists():
        for p in repo.iterdir():
            shutil.rmtree(p, onexc=force) if p.is_dir() else p.unlink()
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'core.autocrlf', 'false')
    if remote:
        git(repo, 'remote', 'add', 'origin', remote)

    # The law commits are written in one pass with git fast-import (one process, not three per commit), then the
    # working tree is checked out and the documentation commit is made with git itself.
    seq = [0]
    tags: dict[str, str] = {}
    law_days: dict[tuple, list[tuple[str, int]]] = {}
    day_mark: dict[str, int] = {}
    log = []
    out = io.BytesIO()

    def data(b: bytes):
        out.write(b'data %d\n' % len(b))
        out.write(b)
        out.write(b'\n')

    def ident(who: tuple[str, str], when: str) -> bytes:
        return f'{who[0]} <{who[1]}> {when.lstrip("@")}'.encode('utf-8')
    mark = 0
    for day, _r, _o, ai, c in plan_commits(built):
        b = c['b']
        slug = b['act']['slug']
        if day > today:
            break
        if c['kind'] == 'start':
            v = b['versions'][0]
            author, msg, trailers = start_message(b, day)
            keys = list(v['order'])
            files = [(k, v['units'][k]) for k in keys]
        else:
            author, msg, trailers, keys = change_message(c)
            files = [(k, c['ch']['cur']['units'].get(k)) for k in keys]
        when = commit_date(day, seq)
        mark += 1
        out.write(b'commit refs/heads/main\nmark :%d\n' % mark)
        out.write(b'author ' + ident(author, when) + b'\n')
        out.write(b'committer ' + ident(COMMITTER, when) + b'\n')
        data(('\n'.join(msg).rstrip('\n') + '\n\n' + '\n'.join(trailers) + '\n').encode('utf-8'))
        for k, u in files:
            path = f'{slug}/{file_name(k)}'.encode('utf-8')
            if u is None:
                out.write(b'D ' + path + b'\n')
            else:
                out.write(b'M 100644 inline ' + path + b'\n')
                data(u['text'].encode('utf-8'))
        out.write(b'\n')
        day_mark[f'{slug}/{day}'] = mark
        if c['kind'] == 'act':
            a = c['g'][1]
            ds = law_days.setdefault(a, [])
            if ds and ds[-1][0] == day:
                ds[-1] = (day, mark)
            else:
                ds.append((day, mark))
        log.append({'day': day, 'slug': slug, 'kind': c['kind'], 'author': author[0], 'units': len(keys),
                    'clamped': day < '1970-01-01', 'keys': keys, 'act': c['g'][1] if c['kind'] == 'act' else None})
    # A tag for every compilation day: the record on that day, which equals that compilation.
    tag_marks: dict[str, int] = {}
    for b in built:
        for v in b['versions']:
            k = f'{b["act"]["slug"]}/{v["from"]}'
            if v['from'] <= today and k in day_mark:
                tag_marks[k] = day_mark[k]
    for a, ds in law_days.items():
        if len(ds) == 1:
            tag_marks[tag_of(a)] = ds[0][1]
        else:
            for day, m in ds:
                tag_marks[f'{tag_of(a)}/{day}'] = m
    for name, m in sorted(tag_marks.items()):
        out.write(f'reset refs/tags/{name}\nfrom :{m}\n\n'.encode('utf-8'))
    out.write(b'done\n')
    marks_file = repo / '.git' / 'lexifina-marks'
    r = subprocess.run(['git', 'fast-import', '--quiet', '--done', f'--export-marks={marks_file}'], cwd=repo,
                       input=out.getvalue(), capture_output=True)
    if r.returncode:
        raise SystemExit(f'git fast-import failed: {r.stderr.decode(errors="replace")}')
    shas = dict(l.split(' ') for l in marks_file.read_text().split('\n') if l)
    marks_file.unlink()
    tags = {name: shas[f':{m}'] for name, m in tag_marks.items()}
    git(repo, 'reset', '-q', '--hard', 'main')
    for p in sorted(docs.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            dest = repo / p.relative_to(docs)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(p.read_bytes())
    (repo / 'tools').mkdir(exist_ok=True)
    for name in ('build.py', 'reproduce.py', 'check_repo.py', 'test_repo.py'):
        (repo / 'tools' / name).write_bytes((HERE / name).read_bytes())
    write_tables(repo, built, today)
    from reproduce import gate_published, write_manifest, write_reproduce
    write_manifest(repo, built, today)
    write_reproduce(repo, built, log, tags, REQUESTS, today)
    gate_published(repo)
    git(repo, 'add', '-A')
    when = commit_date(today, seq)
    env = {**os.environ, 'GIT_AUTHOR_NAME': COMMITTER[0], 'GIT_AUTHOR_EMAIL': COMMITTER[1], 'GIT_AUTHOR_DATE': when,
           'GIT_COMMITTER_NAME': COMMITTER[0], 'GIT_COMMITTER_EMAIL': COMMITTER[1], 'GIT_COMMITTER_DATE': when}
    git(repo, 'commit', '-q', '--cleanup=verbatim', '-F', '-', env=env,
        input=f'Documentation, tables, tools, corpus.json and REPRODUCE.md\n\nNo law text changes.\n\nIn-force: {today}\nKind: documentation\n')
    return log, tags


def write_tables(repo: Path, built: list[dict], today: str):
    rows = []
    for b in built:
        for ch in b['changes']:
            for (kind, x, day), keys in ch['groups'].items():
                if day > today:
                    continue
                t = b['table'].get(x) if kind == 'act' else None
                rows.append({'title': b['act']['short'], 'in_force': day, 'kind': kind,
                             'act': act_label(x, b['table'], b['names']) if kind == 'act' else
                             '; '.join(act_label(a, b['table'], b['names']) for a in x if isinstance(a, tuple)),
                             'assent': (t or {}).get('assent') or '', 'provisions': ' '.join(unit_label(k) for k in sorted(keys, key=unit_sort)),
                             'compilation': ch['from'], 'compilation_id': ch['cur'].get('register_id') or ''})
    rows.sort(key=lambda r: (r['in_force'], r['title'], r['kind'], r['act']))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    (repo / 'changes.csv').write_text(buf.getvalue(), encoding='utf-8', newline='\n')
    for b in built:
        vs = [v for v in b['versions'] if v['from'] <= today]
        if not vs or (b.get('repeal') and b['repeal']['day'] <= today):
            continue   # a repealed title has no folder (data/titles.csv lists it)
        last = vs[-1]
        last_order = b.get('last_order') or last['order']
        counts: dict[str, int] = {}
        lastday: dict[str, str] = {}
        for ch in b['changes']:
            for (kind, x, day), keys in ch['groups'].items():
                if day > today:
                    continue
                for k in keys:
                    counts[k] = counts.get(k, 0) + 1
                    lastday[k] = max(lastday.get(k, ''), day)
        out = [f'# {b["reg"]["title"]["name"]}', '',
               f'Register title {b["act"]["id"]} · <{SITE}/{b["act"]["id"]}/latest/versions>', '',
               f'{len(vs)} compilations, {vs[0]["from"]} to {last["from"]}.', '',
               '| Provision | File | Changes | Last changed |', '| --- | --- | ---: | --- |']
        for k in last_order:
            out.append(f'| {unit_label(k)} | [{file_name(k)}]({file_name(k)}) | {counts.get(k, 0)} | {lastday.get(k, "")} |')
        (repo / b['act']['slug'] / 'README.md').write_text('\n'.join(out) + '\n', encoding='utf-8', newline='\n')


# ---------------------------------------------------------------- main

def data_day() -> str:
    files = [p for p in CACHE.iterdir() if p.is_file() and p.name != '.gitignore'] if CACHE.exists() else []
    if not files:
        return dt.date.today().isoformat()
    return dt.date.fromtimestamp(max(p.stat().st_mtime for p in files)).isoformat()


def build_all(offline: bool) -> list[dict]:
    built = [build_act(a, offline) for a in ACTS]
    for b in built:
        refine(b)
    return built


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(ROOT.parent / 'australia-law'))
    ap.add_argument('--offline', action='store_true', help='read only the cache; fail on anything not cached')
    ap.add_argument('--today', default=None, help='the build day (default: the day the cached data was retrieved)')
    ap.add_argument('--verify-rebuild', action='store_true',
                    help='build again into a temporary folder from the cache, compare every hash with --repo, and record the result')
    args = ap.parse_args()
    if args.verify_rebuild:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([os.sys.executable, '-X', 'utf8', str(HERE / 'build.py'), '--offline', '--repo', tmp]
                                 + (['--today', args.today] if args.today else []), capture_output=True, text=True)
            if out.returncode:
                raise SystemExit(out.stderr)

            def hashes(r):
                log = git(Path(r), 'log', '--format=%H', 'main').split()
                tags = dict(l.split(' ', 1)[::-1] for l in git(Path(r), 'show-ref', '--tags').split('\n') if l)
                return log, tags
            a, b = hashes(args.repo), hashes(tmp)
            same_history = a[0][1:] == b[0][1:]
            rec = {'identical': same_history and a[1] == b[1], 'history_tip': a[0][1], 'commits': len(a[0]) - 1,
                   'tags': len(a[1]), 'main_identical': a[0][0] == b[0][0], 'main': a[0][0], 'rebuilt_main': b[0][0]}
            print(json.dumps(rec, indent=1))
            p = HERE / 'verification.json'
            ver = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
            ver['rebuild'] = {k: rec[k] for k in ('identical', 'history_tip', 'commits', 'tags')}
            p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
            for f in Path(tmp).rglob('*'):
                os.chmod(f, 0o700)
            raise SystemExit(0 if rec['identical'] else 1)
    built = build_all(args.offline)
    today = args.today or data_day()
    for b in built:
        n = sum(len(ks) for ch in b['changes'] for ks in ch['groups'].values())
        kinds: dict[str, int] = {}
        for ch in b['changes']:
            for g, ks in ch['groups'].items():
                kinds[g[0]] = kinds.get(g[0], 0) + len(ks)
        print(f'{b["act"]["slug"]}: {len(b["versions"])} compilations {b["versions"][0]["from"]}..{b["versions"][-1]["from"]}, '
              f'{len(b["versions"][-1]["units"])} units, {n} unit changes {json.dumps(kinds, sort_keys=True)}')
    log, tags = write_repo(Path(args.repo), built, HERE / 'repo', today)
    print(f'repo: {len(log)} commits + 1 documentation commit, {len(tags)} tags -> {args.repo}')


if __name__ == '__main__':
    main()
