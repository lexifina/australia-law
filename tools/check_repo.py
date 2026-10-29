"""Checks the git repository against the official text: every compilation of every Act on the Federal Register of
Legislation, read back from git with tools/aulaw.py on the day it took effect, must equal that compilation's text.

    python -X utf8 scripts/australia-law/check_repo.py [--repo ../australia-law] [--no-record]

Two checks per compilation, from the EPUB files in the cache (no network):
1. File by file: the Act's files in git on that day are exactly the files the build's parser makes from that
   compilation (same set of files, same bytes), and the tool names a commit whose In-force day is that compilation's.
2. Word for word: the Act's files in git, in the order the compilation prints the provisions, with the markdown
   marks removed, have the same words in the same order as the compilation's whole text, read independently of the parser: every visible text node of the HTML
   from the long title to the endnotes, joined with a space only between blocks (paragraphs, headings, table cells),
   leaving out the table of contents (running headers are empty paragraphs). The compilers' "[see Note 1]" marks in headings, which the
   build drops, are dropped here too.
The result is written to verification.json next to this script (unless --no-record), for REPRODUCE.md.
"""
import argparse
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import bulk  # noqa: E402
from gitread import GitReader  # noqa: E402

TOKEN = re.compile(r'\w+|[^\w\s]')
PARAS = ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6')


def official_words(raw: bytes) -> list[str]:
    """All the words of the compilation's body, without the build's parser: the visible text of every block from the
    long title to the first endnotes heading."""
    parts: list[str] = []
    on = False
    heads: dict[str, str] = {}
    levels = ('ActHead1', 'ActHead2', 'ActHead3', 'ActHead4')

    def tg(x):
        return x.tag.lower() if isinstance(x.tag, str) else ''

    def item(x):   # a list item read whole: an <li> with no paragraphs or tables inside
        return tg(x) == 'li' and not any(tg(d) in PARAS + ('table',) for d in x.iterdescendants())
    for pi, root in enumerate(build.html_roots(raw)):
        # A later volume: its cover and "About this compilation" up to its first heading, then the headings it
        # restates (equal to the open one of the same class), are not law text.
        skip = restate = pi > 0 and on
        body = next(e for e in root.iter() if isinstance(e.tag, str) and e.tag.lower() == 'body')
        for e in body.iter():
            if not isinstance(e.tag, str):
                continue
            tag = tg(e)
            cell = tag in ('td', 'th') and not any(tg(x) in PARAS or item(x) for x in e.iter())
            if tag not in PARAS and not cell and not item(e):
                continue
            if any(tg(a) in PARAS or item(a) for a in e.iterancestors()):
                continue
            cls = e.get('class') or ''
            text = build.clean(''.join(e.itertext()))
            if not on:
                on = cls == 'LongT'
            elif build.is_endnotes(e, text):
                return TOKEN.findall(build.clean(' '.join(parts)))
            if not on or cls.startswith('TOC') or cls in ('PageBreak', 'Contents'):
                continue
            if skip:
                if not cls.startswith('ActHead'):
                    continue
                skip = False
            if restate and text:
                if cls in levels and heads.get(cls) == text:
                    continue
                restate = False
            if cls in levels:
                heads = {k: v for k, v in heads.items() if k < cls}
                heads[cls] = text
            ul = item(e) and e.getparent() is not None and tg(e.getparent()) == 'ul'
            parts.append(('- ' if ul and text else '') + build.SEE_NOTE.sub('', text))   # the record marks a bullet
    return TOKEN.findall(build.clean(' '.join(parts)))


def repo_words(text: str) -> list[str]:
    lines = []
    for l in text.split('\n'):
        l = re.sub(r'^\s*#+ ', '', l)
        lines.append(l.replace(' | ', ' '))   # the separator between the cells of a table row
    return TOKEN.findall(' '.join(lines))


def tool(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-X', 'utf8', 'tools/aulaw.py', *args], cwd=repo, capture_output=True,
                          text=True, encoding='utf-8')


def files_at(repo: str, commit: str, slug: str) -> dict[str, str]:
    """The Act's files at a commit, read with one ls-tree and one cat-file --batch (not one git show per file)."""
    out = subprocess.run(['git', 'ls-tree', '-r', commit, f'{slug}/'], cwd=repo, capture_output=True,
                         text=True, encoding='utf-8').stdout.split('\n')
    blobs = []
    for line in out:
        if not line:
            continue
        info, f = line.split('\t', 1)
        if f.endswith('.md') and f != f'{slug}/README.md':
            blobs.append((f[len(slug) + 1:], info.split()[2]))
    if not blobs:
        return {}
    data = subprocess.run(['git', 'cat-file', '--batch'], cwd=repo, input=''.join(f'{sha}\n' for _, sha in blobs).encode(),
                          capture_output=True).stdout
    res, pos = {}, 0
    for name, _ in blobs:
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        res[name] = data[nl + 1:nl + 1 + size].decode('utf-8')
        pos = nl + 1 + size + 1
    return res


_reader = None


def check_one(job: tuple) -> dict:
    """One compilation: the file check and the word check. Run in a worker process."""
    global _reader
    repo, act, v, sample, use_cache = job
    if _reader is None:
        _reader = GitReader(repo)
    bulk.USE_PARSE_CACHE = use_cache
    slug = act['slug']
    raw = build.raw_of(v, True)
    _sha, parsed = bulk.parsed(v, act, True)
    expected = {build.file_name(k): u['text'] for k, u in parsed['units'].items()}
    tag = f'{slug}/{v["from"]}'
    got = _reader.files(tag, slug)
    m = re.search(r'^In-force: (\S+)', _reader.commit_message(tag), re.M)
    msgs = []
    bad = got != expected or not m or m.group(1) != v['from']
    if bad:
        diff = sorted(set(got) ^ set(expected)) or [k for k in expected if got.get(k) != expected[k]]
        msgs.append(f'MISMATCH {slug} {v["from"]}: {diff[:5]} (commit In-force {m.group(1) if m else None})')
    if sample:
        # The on-date tool must find the same commit as the compilation's tag.
        out = tool(repo, v['from'], slug, '--commit')
        tc = out.stdout.split('\n')[0].strip() if out.returncode == 0 else ''
        want = subprocess.run(['git', 'rev-parse', tag + '^{commit}'], cwd=repo, capture_output=True, text=True).stdout.strip()
        if tc != want:
            bad = True
            msgs.append(f'TOOL {slug} {v["from"]}: tool names {tc[:12]}, tag is {want[:12]}')
    # The words of the files in git, in the order the provisions come in the compilation, against the words read
    # without the parser.
    order = [build.file_name(k) for k in parsed['order']]
    rest = sorted(f for f in got if f not in set(order))
    ow = official_words(raw)
    rw = repo_words('\n'.join(got[f] for f in order + rest if f in got))
    wbad = ow != rw
    if wbad:
        i = next((i for i, (a, b) in enumerate(zip(ow, rw)) if a != b), min(len(ow), len(rw)))
        msgs.append(f'WORDS {slug} {v["from"]}: official {len(ow)} vs repo {len(rw)} words; first difference at '
                    f'{i}: {" ".join(ow[max(0, i - 6):i + 6])!r} / {" ".join(rw[max(0, i - 6):i + 6])!r}')
    return {'slug': slug, 'from': v['from'], 'files': len(expected), 'bad': bad, 'wbad': wbad,
            'words': len(ow), 'msgs': msgs}


def compilations(repo: str, today: str, only: set | None = None) -> list[tuple[dict, dict]]:
    """(title, compilation) for every compilation the build read, from the repository's data/versions/*.json."""
    out = []
    for p in sorted((Path(repo) / 'data' / 'versions').glob('*.json')):
        rec = json.loads(p.read_text(encoding='utf-8'))
        if only and rec['title_id'] not in only and rec['folder'] not in only:
            continue
        act = {'slug': rec['folder'], 'id': rec['title_id']}
        for c in rec['compilations']:
            if c['in_force'] <= today:
                out.append((act, {'from': c['in_force'], 'epubs': c['urls'], 'register_id': c['register_id']}))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(build.ROOT.parent / 'australia-law'))
    ap.add_argument('--today', default='9999-12-31', help='check only compilations on or before this day')
    ap.add_argument('--no-record', action='store_true')
    ap.add_argument('--only', default='')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--sample-every', type=int, default=50, help='run the on-date tool on every n-th compilation')
    ap.add_argument('--no-parse-cache', action='store_true', help='parse every file afresh')
    args = ap.parse_args()
    comps = compilations(args.repo, args.today, set(args.only.split(',')) if args.only else None)
    jobs = [(args.repo, act, v, i % args.sample_every == 0, not args.no_parse_cache) for i, (act, v) in enumerate(comps)]
    import concurrent.futures as cf
    with cf.ProcessPoolExecutor(args.workers) as ex:
        res = list(ex.map(check_one, jobs, chunksize=4))
    per_act: dict[str, int] = {}
    for r in res:
        for m in r['msgs']:
            print(m)
        per_act[r['slug']] = per_act.get(r['slug'], 0) + 1
    versions, files = len(res), sum(r['files'] for r in res)
    bad, wbad, nwords = sum(r['bad'] for r in res), sum(r['wbad'] for r in res), sum(r['words'] for r in res)
    print(f'{versions} compilations checked ({files} files), {bad} mismatches; {versions} compilations word for word '
          f'({nwords} words), {wbad} mismatches; {len(per_act)} titles')
    if not args.no_record and not args.only:
        p = HERE / 'verification.json'
        rec = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        rec['check'] = {'versions': versions, 'files': files, 'mismatches': bad, 'wordcheck': versions,
                        'words': nwords, 'word_mismatches': wbad, 'titles': len(per_act),
                        'tool_sampled': sum(1 for j in jobs if j[3])}
        p.write_text(json.dumps(rec, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    raise SystemExit(1 if bad or wbad else 0)


if __name__ == '__main__':
    main()
