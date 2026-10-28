"""Builds the whole record: every title catalogue.py takes (the Constitution, every Act in force, every repealed
principal Act with a compilation), from the cache that inventory.py and fetch_all.py fill. Each title is read and
analysed by build.py's functions in a pool of worker processes (parsed compilations are cached), and the repository is
written with one git fast-import stream: blobs as the titles are read, then every commit in in-force order.

    python -X utf8 scripts/australia-law/bulk.py --today 2026-10-28 [--offline] [--complete-only] [--only ID,ID]
                                                [--repo ../australia-law] [--workers 8] [--no-parse-cache]

One commit per amending Act per in-force day, across all titles: an Act that amends twenty titles on one day is one
commit touching twenty folders; an Act's own text as made is in the same commit as the amendments it makes that day.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import hashlib
import io
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build as B  # noqa: E402
import catalogue as C  # noqa: E402

PARSED = B.CACHE / 'parsed'
_src = (HERE / 'build.py').read_text(encoding='utf-8')
PARSE_KEY = hashlib.sha256(_src[_src.index('# ---------------------------------------------------------------- reading one'):
                                 _src.index('# ---------------------------------------------------------------- one act')]
                           .encode()).hexdigest()[:16]
USE_PARSE_CACHE = True


# ---------------------------------------------------------------- one title (in a worker process)

def parsed(v: dict, act: dict, offline: bool) -> tuple[str, dict]:
    raw = B.raw_of(v, offline)
    sha = B.sha_of(raw)
    key = hashlib.sha256(f'{sha} {PARSE_KEY} {act["slug"] == "constitution"}'.encode()).hexdigest()
    path = PARSED / key[:2] / f'{key}.pkl.gz'
    if USE_PARSE_CACHE and path.exists():
        return sha, pickle.loads(gzip.decompress(path.read_bytes()))
    p = B.read_version(raw, act)
    if USE_PARSE_CACHE:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(gzip.compress(pickle.dumps(p, protocol=5), 3))
        os.replace(tmp, path)
    return sha, p


def act_of(a: dict) -> dict:
    t = a['title']
    key = (int(t['number']), int(t['year'])) if t.get('number') and t.get('year') and str(t['number']).isdigit() else None
    return {'id': a['id'], 'slug': a['slug'], 'short': a['short'], 'name': a['name'], 'citation': a['citation'],
            'key': key, 'tier': a['tier'], 'title': {'year': t.get('year'), 'number': t.get('number')},
            'making': (t.get('makingDate') or '')[:10] or None,
            **({'name': 'Commonwealth of Australia Constitution Act', 'citation': '63 & 64 Vict. c. 12', 'key': None}
               if a['id'] == C.CONSTITUTION else {})}


def repeal_of(reg: dict) -> dict | None:
    """The day the title stopped being in force, from the register's version list: the first version whose status is
    not InForce, with the Acts the register names for it."""
    for v in reg['versions']:
        if v.get('status') and v['status'] != 'InForce':
            keys = sorted({x['key'] for x in v['acts'] if x['key']})
            return {'day': v['from'], 'status': v['status'], 'acts': keys,
                    'markdown': [x['markdown'] for x in v['acts'] if x.get('markdown')]}
    return None


def title_job(job: tuple) -> dict:
    a, reg, offline, use_cache, today = job
    global USE_PARSE_CACHE
    USE_PARSE_CACHE = use_cache
    act = act_of(a)
    vs = [v for v in reg['versions'] if v['epubs'] and v['from'] <= today]
    try:
        for v in vs:
            sha, p = parsed(v, act, offline)
            v['sha256'] = sha
            v.update(p)
    except Exception as exc:   # an unreadable file: the title is left out and named in REPRODUCE.md
        return {'act': act, 'error': f'{type(exc).__name__}: {exc}'[:300]}
    unstyled = [v['from'] for v in vs if not B.styled(v)]
    if not any(B.styled(v) for v in vs):
        return {'act': act, 'error': 'no version with the register\'s section markup', 'unstyled': unstyled}
    act['unstyled_asmade'] = bool(vs and vs[0]['register_id'] == act['id'] and not B.styled(vs[0]))
    b = B.analyse(act, reg, vs)
    B.refine(b)
    if act['key'] and not act.get('assent'):
        act['assent'] = (b['table'].get(act['key']) or {}).get('assent') or act['making']
    return compact(b, unstyled, repeal_of(reg))


def compact(b: dict, unstyled: list[str], repeal: dict | None) -> dict:
    """What the main process needs: the first version's files, and for each change only the changed files."""
    vs = b['versions']
    first = vs[0]
    keep_v = lambda v: {k: v.get(k) for k in ('from', 'register_id', 'compilation', 'rect', 'epub', 'epubs', 'page',
                                               'sha256')}
    changes = []
    for ch in b['changes']:
        keys = [k for ks in ch['groups'].values() for k in ks]
        prev = {'from': ch['prev']['from'], 'register_id': ch['prev'].get('register_id'),
                'units': {k: {} for k in keys if k in ch['prev']['units']}}
        cur = {**keep_v(ch['cur']), 'units': {k: {'text': ch['cur']['units'][k]['text']} for k in keys
                                              if k in ch['cur']['units']}}
        changes.append({'from': ch['from'], 'prev': prev, 'cur': cur, 'groups': ch['groups'], 'why': ch['why'],
                        'window': ch['window'], 'in_window': ch['in_window']})
    last = vs[-1]
    return {'act': b['act'], 'reg': {'title': {'name': b['reg']['title']['name']},
                                     'versions': [{'from': v['from'], 'acts': v['acts'], 'epub': v['epub'],
                                                   'register_id': v['register_id']} for v in b['reg']['versions']]},
            'versions': [keep_v(v) for v in vs],
            'start_units': {k: {'text': first['units'][k]['text']} for k in first['order']},
            'start_order': list(first['order']),
            'last_order': list(last['order']), 'last_units': len(last['units']),
            'changes': changes, 'problems': b['problems'], 'table': b['table'], 'names': b['names'], 'ids': b['ids'],
            'days': b['days'], 'unstyled': unstyled, 'repeal': repeal}


# ---------------------------------------------------------------- the fast-import stream

class Stream:
    def __init__(self, path: Path):
        self.f = open(path, 'wb')
        self.mark = 0
        self.blobs: dict[str, int] = {}

    def data(self, b: bytes):
        self.f.write(b'data %d\n' % len(b) + b + b'\n')

    def blob(self, text: str) -> int:
        h = hashlib.sha1(text.encode('utf-8')).hexdigest()
        if h in self.blobs:
            return self.blobs[h]
        self.mark += 1
        self.f.write(b'blob\nmark :%d\n' % self.mark)
        self.data(text.encode('utf-8'))
        self.blobs[h] = self.mark
        return self.mark

    def commit(self, author: tuple[str, str], when: str, msg: str, ops: list[tuple]) -> int:
        self.mark += 1
        ident = lambda who: f'{re.sub(r"[<>]", "", who[0])} <{who[1]}> {when.lstrip("@")}'.encode('utf-8')
        self.f.write(b'commit refs/heads/main\nmark :%d\n' % self.mark)
        self.f.write(b'author ' + ident(author) + b'\ncommitter ' + ident(B.COMMITTER) + b'\n')
        self.data(msg.encode('utf-8'))
        for op in ops:
            if op[0] == 'M':
                self.f.write(f'M 100644 :{op[2]} {op[1]}\n'.encode('utf-8'))
            elif op[0] == 'D':
                self.f.write(f'D {op[1]}\n'.encode('utf-8'))
        self.f.write(b'\n')
        return self.mark

    def tag(self, name: str, mark: int):
        self.f.write(f'reset refs/tags/{name}\nfrom :{mark}\n\n'.encode('utf-8'))


def to_marks(r: dict, s: Stream):
    for k, u in r['start_units'].items():
        u['mark'] = s.blob(u.pop('text'))
    for ch in r['changes']:
        for k, u in ch['cur']['units'].items():
            u['mark'] = s.blob(u.pop('text'))


# ---------------------------------------------------------------- commits

RANK = dict(B.KIND_RANK, repeal=1)


def plan(results: list[dict], today: str) -> list[dict]:
    """Pieces (one title's part of a commit) grouped into commits, in in-force order."""
    groups: dict[tuple, dict] = {}

    def add(key, day, kind, order, ti, piece):
        if day > today:
            return
        g = groups.setdefault(key, {'day': day, 'rank': RANK[kind], 'order': order, 'ti': ti, 'pieces': []})
        g['rank'] = min(g['rank'], RANK[kind])
        g['ti'] = min(g['ti'], ti)
        g['pieces'].append(piece)
    for ti, b in enumerate(results):
        act = b['act']
        v0 = b['versions'][0]
        asmade = v0['register_id'] == act['id'] and act['key'] is not None and act['slug'] != 'constitution'
        key = ('act', act['key'], v0['from']) if asmade else ('start', ti)
        add(key, v0['from'], 'start', (act['key'][1], act['key'][0]) if asmade else (0, 0), ti,
            {'kind': 'start', 'b': b, 'ti': ti})
        for ch in b['changes']:
            if not any(g[2] == ch['from'] for g in ch['groups']):
                add(('unchanged', ti, ch['from']), ch['from'], 'unchanged', (0, 0), ti,
                    {'kind': 'unchanged', 'b': b, 'ch': ch, 'g': ('unchanged', (), ch['from']), 'keys': [], 'ti': ti})
            for g, keys in ch['groups'].items():
                kind, x, day = g
                if kind == 'act':
                    add(('act', x, day), day, kind, (x[1], x[0]), ti, {'kind': kind, 'b': b, 'ch': ch, 'g': g,
                                                                       'keys': keys, 'ti': ti})
                else:
                    add((kind, ti, x, day), day, kind, (0, 0), ti, {'kind': kind, 'b': b, 'ch': ch, 'g': g,
                                                                   'keys': keys, 'ti': ti})
        rp = b.get('repeal')
        if rp and rp['day'] > b['versions'][-1]['from']:
            if len(rp['acts']) == 1:
                a = rp['acts'][0]
                add(('act', a, rp['day']), rp['day'], 'repeal', (a[1], a[0]), ti, {'kind': 'repeal', 'b': b, 'ti': ti, 'act': a})
            else:
                add(('repeal', ti), rp['day'], 'repeal', (9999, 0), ti, {'kind': 'repeal', 'b': b, 'ti': ti, 'act': None})
    out = sorted(groups.items(), key=lambda kv: (kv[1]['day'], kv[1]['rank'], kv[1]['order'], kv[1]['ti'],
                                                 repr(kv[0])))
    for key, g in out:
        g['key'] = key
        g['pieces'].sort(key=lambda p: (p['ti'], RANK[p['kind']]))
    return [g for _, g in out]


def piece_ops(p: dict) -> list[tuple]:
    b = p['b']
    slug = b['act']['slug']
    if p['kind'] == 'start':
        return [('M', f'{slug}/{B.file_name(k)}', b['start_units'][k]['mark']) for k in b['start_order']]
    if p['kind'] == 'repeal':
        return [('D', slug)]
    ops = []
    for k in sorted(p['keys'], key=B.unit_sort):
        u = p['ch']['cur']['units'].get(k)
        ops.append(('D', f'{slug}/{B.file_name(k)}') if u is None else ('M', f'{slug}/{B.file_name(k)}', u['mark']))
    return ops


def repeal_message(p: dict):
    b = p['b']
    rp = b['repeal']
    name = b['reg']['title']['name']
    word = {'Repealed': 'repealed', 'Ceased': 'ceased to have effect'}.get(rp['status'], rp['status'].lower())
    a = p.get('act')
    if a:
        author = B.author_of(a, b)
        msg = [B.subject_short(f'{b["act"]["short"]}: {word} (No. {a[0]}, {a[1]})'), '', author[0] + '.', '']
        msg += B.wrap(f'The register\'s version list records the {name} as {word} from {rp["day"]}, by '
                      f'{B.act_label(a, b["table"], b["names"])}. Its folder is removed.') + ['']
        trailers = ['Kind: Act', f'In-force: {rp["day"]}']
    else:
        author = B.PUBLISHER
        msg = [B.subject_short(f'{b["act"]["short"]}: {word}'), '']
        msg += B.wrap(f'The register\'s version list records the {name} as {word} from {rp["day"]}'
                      + (f', naming {"; ".join(rp["markdown"])}' if rp['markdown'] else ', naming no Act')
                      + '. No single Act is credited. Its folder is removed.') + ['']
        trailers = ['Kind: repeal (not credited)', f'In-force: {rp["day"]}']
    trailers.append(f'Status: {rp["status"]}')
    trailers.append(f'Affecting: {b["act"]["short"]} (whole Act)')
    return author, msg, trailers


def single_message(p: dict, today: str):
    if p['kind'] == 'start':
        return B.start_message(p['b'], p['b']['versions'][0]['from'])
    if p['kind'] == 'repeal':
        return repeal_message(p)
    author, msg, trailers, _keys = B.change_message(p)
    return author, msg, trailers


def merged_message(g: dict):
    """One Act's commit for one day across several titles (and its own text as made, if it took effect that day)."""
    a = g['key'][1]
    day = g['day']
    bs = [p['b'] for p in g['pieces']]
    table = {}
    names = {}
    ids = {}
    for b in bs:
        for k, v in b['table'].items():
            table.setdefault(k, v)
        names.update({k: v for k, v in b['names'].items() if k not in names})
        ids.update({k: v for k, v in b['ids'].items() if k not in ids})
    own = next((p for p in g['pieces'] if p['kind'] == 'start'), None)
    if own:
        name = own['b']['reg']['title']['name']
        author = (f'{name} (No. {a[0]}, {a[1]})', f'{B.act_slug(a)}@legislation.invalid')
    else:
        author = B.author_of(a, {'table': table, 'names': names})
    titles = [p['b']['act']['short'] for p in g['pieces']]
    subj = f'{author[0]}: {len(titles)} Acts'
    if len(subj) > 72:
        subj = f'Act No. {a[0]}, {a[1]}: {len(titles)} Acts'
    msg = [subj, '', author[0] + '.', '']
    msg += B.wrap(f'This Act took effect on {day} for each title below, as the register\'s version list gives it. '
                  'For each amended title, the amendment history in the endnotes of its next compilation cites this '
                  'Act for the provisions listed.') + ['']
    trailers = ['Kind: Act']
    assent = (table.get(a) or {}).get('assent') or (own and own['b']['act'].get('assent'))
    if assent:
        trailers.append(f'Assented: {assent}')
    if ids.get(a) or own:
        trailers.append(f'Source: {B.SITE}/{ids.get(a) or own["b"]["act"]["id"]}/asmade/text')
    trailers.append(f'In-force: {day}')
    if assent and day < assent:
        trailers.append('Date-note: the register dates this Act\'s effect before its assent (retrospective)')
    affecting = []
    comps = []
    for p in g['pieces']:
        b = p['b']
        short = b['act']['short']
        if p['kind'] == 'start':
            msg.append(f'{b["reg"]["title"]["name"]}: as made ({len(b["start_order"])} provisions).')
            affecting.append(f'Affecting: {short} (whole Act)')
            comps += B.comp_trailers(b, b['versions'][0])
        elif p['kind'] == 'repeal':
            msg.append(f'{b["reg"]["title"]["name"]}: repealed (the register\'s version list); its folder is removed.')
            affecting.append(f'Affecting: {short} (whole Act)')
        else:
            ch = p['ch']
            lst, labels = B.listing(ch, sorted(p['keys'], key=B.unit_sort))
            note = '' if ch['from'] == day else f' (text of the compilation of {ch["from"]})'
            msg.append(f'{b["reg"]["title"]["name"]}{note}:')
            msg += ['  ' + l for l in lst]
            heading = [k for k in p['keys'] if 'heading row' in ch['why'][k]]
            prov = [B.unit_label(k) for k in sorted(p['keys'], key=B.unit_sort) if k not in heading]
            if prov:
                trailers.append(f'Evidence: amendment history of {short} cites this Act for {", ".join(prov)}')
            if heading:
                trailers.append(f'Evidence: amendment history of {short} cites this Act for a heading in '
                                + ', '.join(B.unit_label(k) for k in heading))
            trailers.append(f'Evidence: register version list dates this Act {", ".join(ch["in_window"][a])} for {short}')
            affecting += [f'Affecting: {short} {l}' for l in labels]
            comps += B.comp_trailers(b, ch['cur'])
    msg.append('')
    if day < '1970-01-01':
        msg += B.PRE_1970
    return author, msg, trailers + affecting + comps


def write_repo(repo: Path, results: list[dict], today: str, status: str | None, skipped: list[dict], requests: int):
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
    B.git(repo, 'init', '-q', '-b', 'main')
    B.git(repo, 'config', 'core.autocrlf', 'false')
    if remote:
        B.git(repo, 'remote', 'add', 'origin', remote)
    stream_file = repo / '.git' / 'lexifina-stream'
    s = Stream(stream_file)
    for r in results:
        to_marks(r, s)
    seq = [0]
    day_mark: dict[str, int] = {}
    law_days: dict[tuple, list[tuple[str, int]]] = {}
    log = []
    for g in plan(results, today):
        day = g['day']
        if len(g['pieces']) == 1:
            author, msg, trailers = single_message(g['pieces'][0], today)
        else:
            author, msg, trailers = merged_message(g)
        ops = [op for p in g['pieces'] for op in piece_ops(p)]
        when = B.commit_date(day, seq)
        text = '\n'.join(msg).rstrip('\n') + '\n\n' + '\n'.join(trailers) + '\n'
        m = s.commit(author, when, text, ops)
        for p in g['pieces']:
            day_mark[f'{p["b"]["act"]["slug"]}/{day}'] = m
        kind = g['key'][0]
        if kind == 'act':
            ds = law_days.setdefault(g['key'][1], [])
            ds.append((day, m))
        log.append({'day': day, 'kind': kind if len(g['pieces']) == 1 or kind == 'act' else kind,
                    'piece_kinds': [p['kind'] for p in g['pieces']], 'titles': len(g['pieces']),
                    'author': author[0], 'clamped': day < '1970-01-01'})
    tag_marks: dict[str, int] = {}
    for b in results:
        for v in b['versions']:
            k = f'{b["act"]["slug"]}/{v["from"]}'
            if v['from'] <= today and k in day_mark:
                tag_marks[k] = day_mark[k]
    for a, ds in law_days.items():
        if len(ds) == 1:
            tag_marks[B.tag_of(a)] = ds[0][1]
        else:
            for day, m in ds:
                tag_marks[f'{B.tag_of(a)}/{day}'] = m
    for name, m in sorted(tag_marks.items()):
        s.tag(name, m)
    s.f.write(b'done\n')
    s.f.close()
    marks_file = repo / '.git' / 'lexifina-marks'
    with open(stream_file, 'rb') as f:
        r = subprocess.run(['git', 'fast-import', '--quiet', '--done', f'--export-marks={marks_file}'], cwd=repo,
                           stdin=f, capture_output=True)
    if r.returncode:
        raise SystemExit(f'git fast-import failed: {r.stderr.decode(errors="replace")[:2000]}')
    shas = dict(l.split(' ') for l in marks_file.read_text().split('\n') if l)
    marks_file.unlink()
    stream_file.unlink()
    tags = {name: shas[f':{m}'] for name, m in tag_marks.items()}
    B.git(repo, 'reset', '-q', '--hard', 'main')
    write_docs(repo, results, today, log, tags, status, skipped, requests, seq)
    return log, tags


def write_docs(repo: Path, results, today, log, tags, status, skipped, requests, seq):
    docs = HERE / 'repo'
    for p in sorted(docs.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            dest = repo / p.relative_to(docs)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(p.read_bytes())
    (repo / 'tools').mkdir(exist_ok=True)
    for name in ('build.py', 'bulk.py', 'catalogue.py', 'inventory.py', 'fetch_all.py', 'reproduce.py',
                 'check_repo.py', 'check_words.py', 'test_repo.py'):
        if (HERE / name).exists():
            (repo / 'tools' / name).write_bytes((HERE / name).read_bytes())
    B.write_tables(repo, results, today)
    from reproduce import write_manifest, write_reproduce
    write_manifest(repo, results, today, skipped)
    write_reproduce(repo, results, log, tags, requests, today, skipped)
    if status:
        (repo / 'PROGRESS.md').write_text(
            '# Work in progress\n\nThis is a partial build of the expanded record, from the titles fetched so far; '
            f'it is force-pushed to the wip branch.\n\n{status}\n\n{len(results)} titles in this build.\n',
            encoding='utf-8', newline='\n')
    B.git(repo, 'add', '-A')
    when = B.commit_date(today, seq)
    env = {**os.environ, 'GIT_AUTHOR_NAME': B.COMMITTER[0], 'GIT_AUTHOR_EMAIL': B.COMMITTER[1], 'GIT_AUTHOR_DATE': when,
           'GIT_COMMITTER_NAME': B.COMMITTER[0], 'GIT_COMMITTER_EMAIL': B.COMMITTER[1], 'GIT_COMMITTER_DATE': when}
    B.git(repo, 'commit', '-q', '--cleanup=verbatim', '-F', '-', env=env,
          input=f'Documentation, tables, tools, corpus.json and REPRODUCE.md\n\nNo law text changes.\n\nIn-force: {today}\nKind: documentation\n')


# ---------------------------------------------------------------- main

def complete(reg: dict) -> bool:
    return all(B.cache_path(u).exists() for v in reg['versions'] for u in (v['epubs'] or []))


def build_results(args) -> tuple[list[dict], list[dict], int]:
    cat = C.load(True)
    sel = C.selection(cat)
    if args.only:
        only = set(args.only.split(','))
        sel = [a for a in sel if a['id'] in only or a['slug'] in only]
    jobs = []
    pending = 0
    for a in sel:
        reg = C.register(cat, a)
        if not any(v['epubs'] for v in reg['versions']):
            continue
        for v in reg['versions']:   # a file the register would not serve (a cached 4xx): no compilation
            if v['epubs'] and any(B.cache_path(u).with_suffix('.neg').exists() for u in v['epubs']):
                v['epubs'] = None
                v['epub'] = None
        if not any(v['epubs'] for v in reg['versions']):
            continue
        if args.complete_only and not complete(reg):
            # Work in progress: the newest compilations that are all in the cache (the fetch goes newest first), so
            # the title starts at the oldest of them.
            with_files = [v for v in reg['versions'] if v['epubs']]
            ok = [all(B.cache_path(u).exists() for u in v['epubs']) for v in with_files]
            i = len(ok)
            while i > 0 and ok[i - 1]:
                i -= 1
            if i == len(ok):
                pending += 1
                continue
            for v in with_files[:i]:
                v['epubs'] = None
                v['epub'] = None
            pending += 1
        jobs.append((a, reg, args.offline, not args.no_parse_cache, args.today))
    results, skipped = [], []
    with cf.ProcessPoolExecutor(args.workers) as ex:
        for r in ex.map(title_job, jobs, chunksize=1):
            (skipped if 'error' in r else results).append(r)
    requests = len({u for _, reg, _, _, _ in jobs for v in reg['versions'] for u in (v['epubs'] or [])})
    return results, skipped, requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(B.ROOT.parent / 'australia-law'))
    ap.add_argument('--today', required=True)
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--complete-only', action='store_true', help='only titles whose files are all in the cache')
    ap.add_argument('--only', default='')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--no-parse-cache', action='store_true')
    ap.add_argument('--status', default=None, help='a work-in-progress note (writes PROGRESS.md)')
    args = ap.parse_args()
    results, skipped, requests = build_results(args)
    print(f'{len(results)} titles read, {len(skipped)} left out', flush=True)
    log, tags = write_repo(Path(args.repo), results, args.today, args.status, skipped, requests)
    print(f'repo: {len(log)} commits + 1 documentation commit, {len(tags)} tags -> {args.repo}')


if __name__ == '__main__':
    main()
