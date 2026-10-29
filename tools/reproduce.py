"""Writes data/versions.json, corpus.json and REPRODUCE.md into the Australian law repository, from the facts of the
build that made it. Called by build.py; kept apart so the record of how the repository was made reads as one document.

The results of the checks (check_repo.py, test_repo.py and the rebuild that compares hashes) are read from
verification.json next to this file, which check_repo.py, `test_repo.py --record` and `build.py --verify-rebuild` write.
They describe the build before the one that writes this file, from the same data; a rebuild with the same data and the
same verification.json gives the same hashes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / 'gitlaw_common'))
try:
    from publish_gate import publish_gate
except ImportError:          # an outside reader rebuilding from the published tools: no private rule to apply
    if os.environ.get('GITLAW_PUBLIC_REBUILD') != '1':
        raise SystemExit('publish gate missing: run from lexifina/scripts, or set GITLAW_PUBLIC_REBUILD=1')

    def publish_gate(files): return files
SITE = 'https://www.legislation.gov.au'
TERMS = 'https://www.legislation.gov.au/terms-of-use'
MODEL = 'Claude Opus 5.5 (model id `claude-opus-5-5`)'


def record_to(built: list[dict], today: str) -> str:
    return max(v['from'] for b in built for v in b['versions'] if v['from'] <= today)


def write_manifest(repo: Path, built: list[dict], today: str, skipped: list[dict] | None = None):
    """Every compilation file the text comes from, with its sha256 (data/versions/<folder>.json), the titles
    (data/titles.csv), the titles left out (data/left-out.csv), and corpus.json."""
    import csv
    import io
    skipped = skipped or []
    vdir = repo / 'data' / 'versions'
    vdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for b in built:
        rec = {'title_id': b['act']['id'], 'name': b['reg']['title']['name'], 'folder': b['act']['slug'],
               'compilations': [{'in_force': v['from'], 'register_id': v['register_id'], 'compilation': v['compilation'],
                                 'rectification': v['rect'], 'urls': v['epubs'], 'page': v['page'], 'sha256': v['sha256']}
                                for v in b['versions']],
               'versions_without_compilation': [{'in_force': v['from'], 'acts': [a['name'] for a in v['acts']]}
                                                for v in b['reg']['versions'] if not v['epub']],
               'unreadable_versions': b.get('unstyled') or [],
               'repeal': b.get('repeal')}
        (vdir / f'{b["act"]["slug"]}.json').write_text(json.dumps(rec, ensure_ascii=False, indent=1) + '\n',
                                                       encoding='utf-8', newline='\n')
        rp = b.get('repeal') or {}
        ended = bool(rp) and rp['day'] <= today
        rows.append({'folder': b['act']['slug'], 'title_id': b['act']['id'], 'name': b['reg']['title']['name'],
                     'citation': b['act'].get('citation') or '', 'tier': b['act'].get('tier', ''),
                     'compilations': len(b['versions']), 'first': b['versions'][0]['from'],
                     'last': b['versions'][-1]['from'], 'files_last': b.get('last_units', ''),
                     'status': rp['status'] if ended else 'InForce', 'ended': rp['day'] if ended else ''})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    (repo / 'data' / 'titles.csv').write_text(buf.getvalue(), encoding='utf-8', newline='\n')
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['title_id', 'name', 'reason'])
    for s in skipped:
        w.writerow([s['act']['id'], s['act']['name'], s['error']])
    (repo / 'data' / 'left-out.csv').write_text(buf.getvalue(), encoding='utf-8', newline='\n')
    rt = record_to(built, today)
    n_comp = sum(len(b['versions']) for b in built)
    corpus = {
        'corpus': 'au/federal/acts',
        'name': 'Australian (Commonwealth) law: the Constitution and the Acts of the Commonwealth Parliament',
        'label': 'Australia (Commonwealth)',
        'language': 'en',
        'acts': [f'{b["reg"]["title"]["name"]} ({b["act"]["id"]})' for b in built],
        'history_from': min(b['versions'][0]['from'] for b in built),
        'record_to': rt,
        'record_note': (f'{len(built)} titles from the Federal Register of Legislation: the Constitution, the Acts in '
                        'force and the repealed principal Acts that have a registered compilation, each from its first '
                        'version the parser can read (the Act as made, or its first compilation) through every '
                        f'registered compilation to the last on or before {today} ({n_comp} compilations), and repealed '
                        f'Acts to their repeal; data retrieved {today}. Commits between compilations are dated by the '
                        'register\'s version list; only compilation days are checked against official text.'),
        'in_force_trailer': 'In-force',
        'built': today,
        'checked_on': today,
        'history': 'replayed',
        'verification': (f'Verified against official text: every compilation on its compilation day ({n_comp} '
                         'compilations). Changes the register dates between two compilations are credited from the '
                         'endnotes, and their text on those intermediate days is not independently verified.'),
        'dates': {
            'commit': 'in-force',
            'other_trailers': ['Assented'],
            'time': '12:00 Canberra (AEST, UTC+10; AEDT, UTC+11, in daylight saving time)',
        },
        'scope': {
            'trailer': 'Applies',
            'when_absent': 'this corpus records no scope notes (no commit carries Applies:); the absence is not a statement '
                           'that the change reaches every case',
            'default_reach': {
                'rule': 'presumption against retrospective operation; repeal or amendment does not disturb what was done '
                        'or accrued before',
                'meaning': ('a Commonwealth Act is presumed not to operate retrospectively on substantive rights and '
                            'liabilities unless it says so; the repeal or amendment of an Act does not affect its previous '
                            'operation or any right, liability or penalty acquired or incurred under it, unless the '
                            'contrary intention appears; the Constitution does not forbid retrospective laws, and the '
                            'High Court has upheld a retrospective criminal law'),
                'authority': ('Acts Interpretation Act 1901 (Cth) s 7(2) (effect of repeal or amendment of an Act); '
                              'Maxwell v Murphy (1957) 96 CLR 261 (common-law presumption); Polyukhovich v Commonwealth '
                              '(1991) 172 CLR 501 (retrospective criminal law valid); not checked in this corpus'),
                'decided_here': False,
            },
        },
        'tool': 'tools/aulaw.py',
        'citation': {'file': r'([a-z0-9-]+)/s-0*([0-9]+[A-Z]*)\.md', 'cites': r'(section|s\.?) ?{n}([^0-9A-Za-z]|$)'},
    }
    (repo / 'corpus.json').write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def write_reproduce(repo: Path, built: list[dict], log: list[dict], tags: dict, n_req: int, today: str,
                    skipped: list[dict] | None = None):
    import csv
    import io
    skipped = skipped or []
    blob = subprocess.run(['git', 'hash-object', str(HERE / 'build.py')], capture_output=True, text=True).stdout.strip()
    bulk_blob = subprocess.run(['git', 'hash-object', str(HERE / 'bulk.py')], capture_output=True, text=True).stdout.strip()
    pyv = subprocess.run(['python', '--version'], capture_output=True, text=True).stdout.strip()
    gitv = subprocess.run(['git', '--version'], capture_output=True, text=True).stdout.strip()
    try:
        import lxml.etree
        lxmlv = '.'.join(str(x) for x in lxml.etree.LXML_VERSION[:3])
    except Exception:
        lxmlv = '?'
    ver = json.loads((HERE / 'verification.json').read_text(encoding='utf-8')) if (HERE / 'verification.json').exists() else {}
    kinds: dict[str, int] = {}
    merged = 0
    for c in log:
        kinds[c['kind']] = kinds.get(c['kind'], 0) + 1
        merged += c['titles'] > 1
    unit_kinds: dict[str, int] = {}
    from build import unit_label, act_label
    lab = io.StringIO()
    w = csv.writer(lab, lineterminator='\n')
    w.writerow(['title', 'folder', 'in_force', 'kind', 'provisions', 'acts_named'])
    for b in built:
        for ch in b['changes']:
            for (kind, x, day), keys in ch['groups'].items():
                if day > today:
                    continue
                unit_kinds[kind] = unit_kinds.get(kind, 0) + len(keys)
                if kind != 'act':
                    who = '; '.join(act_label(a, b['table'], b['names']) for a in x if isinstance(a, tuple))
                    w.writerow([b['reg']['title']['name'], b['act']['slug'], day, kind,
                                ' '.join(unit_label(k) for k in keys), who])
    (repo / 'data').mkdir(exist_ok=True)
    (repo / 'data' / 'labelled.csv').write_text(lab.getvalue(), encoding='utf-8', newline='\n')
    nl = chr(10)
    tiers: dict = {}
    for b in built:
        tiers[b['act'].get('tier')] = tiers.get(b['act'].get('tier'), 0) + 1
    n_comp = sum(len(b['versions']) for b in built)
    repealed = sum(1 for b in built if b.get('repeal') and b['repeal']['day'] <= today)
    asmade_start = sum(1 for b in built if b['versions'][0]['register_id'] == b['act']['id'])
    left = nl.join(f'- {s["act"]["name"]} (`{s["act"]["id"]}`): {s["error"]}' for s in skipped[:40]) or '- none'
    if len(skipped) > 40:
        left += nl + f'- and {len(skipped) - 40} more (`data/left-out.csv`)'
    chk = ver.get('check', {})
    det = ver.get('rebuild', {})
    smoke = ver.get('smoke', {})
    words = ver.get('words', {})
    check_line = (f'`{chk["versions"]} compilations checked ({chk["files"]} files), {chk["mismatches"]} mismatches; '
                  f'{chk["wordcheck"]} compilations word for word ({chk["words"]} words) against the whole official '
                  f'text, {chk["word_mismatches"]} mismatches`' if chk else '(not yet run)')
    words_line = (f'`{words["versions"]} compilations, {words["letters"]} letters and digits, {words["mismatches"]} '
                  f'mismatches`' if words else '(not yet run)')
    rebuild_line = (f'A second build from the same cache into an empty folder gave the same history: the last law commit '
                    f'was `{det["history_tip"]}` in both, and every one of the {det["commits"]} law commits and '
                    f'{det["tags"]} tags had the same hash.' if det.get('identical') else '(not yet run)')
    smoke_lines = (nl.join(f'   - {"pass" if r["ok"] else "FAIL"}: {r["test"]}: {r["detail"]}' for r in smoke['results'])
                   if smoke else '   (not yet run)')
    smoke_head = f'{smoke["passed"]} of {smoke["total"]} passed' if smoke else 'not yet run'
    text = f"""# How this history was made, and how to make it again

This file records exactly how the repository was built: the sources, the commands, the software, the decisions made
by hand, and what an AI model did and did not do. The build writes it, so it describes the build that made this
commit.

## Short version

```sh
git clone https://github.com/lexifina/australia-law && cd australia-law
pip install lxml
# with the retrieved files in place (below); outside Lexifina, GITLAW_PUBLIC_REBUILD=1 skips its publication check
GITLAW_PUBLIC_REBUILD=1 python -X utf8 tools/bulk.py --offline --repo ../rebuilt --today {today}
git -C ../rebuilt rev-parse main        # the same hash as this repository's main
python -X utf8 tools/check_repo.py --repo ../rebuilt --today {today}
python -X utf8 tools/check_words.py --repo ../rebuilt --today {today}
python -X utf8 tools/test_repo.py --repo ../rebuilt
```

The tools need Python 3.12 or later with `lxml`, and git. They read the retrieved files (the register's lists and
each compilation's EPUB file), kept next to the scripts under the name `build.cache_path()` gives each file's URL, and
write this repository into `--repo`. With the same files and the same `--today` the build gives the same commits with
the same hashes. `data/versions/<folder>.json` lists every compilation file read with its URL and sha256, so a copy of
the files can be checked byte for byte before rebuilding. The register may re-render or rectify a compilation; then
the hashes change too. How the files were retrieved is not part of this record.

## Sources

All legal text and all information about versions and amending Acts come from the **Federal Register of Legislation**
({SITE}), the authorised whole-of-government website for Commonwealth legislation, managed by the Office of
Parliamentary Counsel under the Legislation Act 2003. Nothing was typed in, summarised or rewritten.

The build read the register's lists and {n_req} compilation files:

- the list of Act titles, with each title's status (in force or not) and whether it is a principal Act, and the
  Constitution's title record;
- the list of versions of every Act, with the day each started (its in-force day), its status and the amending Acts
  the register names for it, with their register IDs, numbers and years;
- the list of registered files, which says which versions have a file and its rectifications;
- each version's file as EPUB (the latest rectification), whose XHTML holds the text and the endnotes; a compilation
  in volumes is one EPUB with a document per volume. `data/versions/<folder>.json` gives each file's URL and sha256.

**Titles.** {len(built)} titles: the Constitution ({tiers.get(0, 0)}), principal Acts in force ({tiers.get(1, 0)}),
repealed or ceased principal Acts with at least one registered compilation ({tiers.get(2, 0)}), and the other Acts in
force, which are amending Acts kept as made ({tiers.get(3, 0)}); {n_comp} versions read in all. `data/titles.csv` lists
them with their folders. {len(skipped)} titles taken could not be read and are left out:

{left}

**Terms of use.** The register's terms ({TERMS}) put its content, except the Commonwealth Coat of Arms and where
otherwise noted, under the Creative Commons Attribution 4.0 International licence (CC BY 4.0). See `LICENSE` for the
attribution the register asks for. The files were retrieved up to {today}. This repository is private.

## The build, step by step

1. From the retrieved files, `python -X utf8 scripts/australia-law/bulk.py --offline --today {today}` in
   Lexifina's own repository wrote this repository. `tools/` here holds the same files as `scripts/australia-law/` there (`build.py` is
   git blob `{blob}`, `bulk.py` `{bulk_blob}`).
2. `check_repo.py --today {today}` read every compilation of each title back from this repository with
   `tools/aulaw.py`, on the day it took effect, and compared it file by file with the official text parsed afresh
   from the cached EPUB; then it compared the whole Act, word for word, with all the words of the compilation's body
   read without the build's parser. Result: {check_line}.
3. `check_words.py --today {today}`, an independent check: the letters and digits of every compilation (casefolded,
   everything else removed), read from the raw XHTML by a separate minimal extractor (every text node of the body
   from the long title to the endnotes, except the table of contents, running headers and hidden elements), against
   the letters and digits of the files in git on that day. Result: {words_line}.
4. `test_repo.py --record` ran the functional smoke test ({smoke_head}):
{smoke_lines}
5. `bulk.py --offline --verify-rebuild` built the repository a second time from the same cache into a temporary
   folder and compared the hashes. {rebuild_line}
6. The build was run once more to write these results into this file, and the final `main` was rebuilt again and
   compared hash for hash.

It ran with {pyv}, lxml {lxmlv} and {gitv} on Windows 11. Git writes the law commits with `git fast-import` (one
stream: the files as blobs, then every commit in date order), then the documentation commit with `git commit`.
Compilations are parsed in a pool of worker processes, and each parsed compilation is kept, keyed by the sha256 of its
file and of the parser's code.

## How the text and the commits are derived

Everything below is done by the tools, deterministically.

- **Text.** Each title is a folder named after its name (`income-tax-assessment-act-1997/`); the first two titles of
  this record keep their folders (`constitution/`, `privacy-act/`). Each section is one file named after its number:
  `s 6` is `s-006.md`, `s 6A` is `s-006A.md`; the Constitution Act's covering clauses are `cc-1.md` to `cc-9.md`; a
  schedule's heading and opening words are `sch-1.md` and its clauses `sch-1/cl-01.md`; `intro.md` holds the long
  title and the preamble. The headings of the chapters, parts and divisions that open before a section are `##` lines
  at the top of its file; its number and heading are the `#` line; then one line per paragraph. Tables are one line
  per row, cells separated by ` | `. A section is found by the register's `CharSectno` mark on its heading.
- **Normalised.** White space (runs of spaces, tabs, no-break spaces become one space); soft hyphens and zero-width
  characters are dropped; the non-breaking hyphen (U+2011) is a hyphen; elements the page hides (`display:none`) are
  left out; and the compilers' reference marks in older headings ("[see Note 1]") are dropped. Every other character
  is kept.
- **Versions.** Every version on the register that has a registered file the parser can read: one with the register's
  section markup. {asmade_start} titles start at the Act as made; the others start at their first registered
  compilation, because the Act as made is on the register as a text converted from print without that markup (the
  amendments made before the first compilation are then in its text, not separated out). Versions without a file reach
  the record through the next compilation, dated as below. `data/versions/<folder>.json` names them all.
- **Repeal.** A title the register records as repealed or ceased has its folder removed on that day
  ({repealed} titles), in the repealing Act's commit when the register names one Act, otherwise in a commit by the
  register.
- **Changes.** Consecutive compilations are compared file by file.
- **Cause and date.** Each compilation's endnotes have an amendment history: a row per provision listing the Acts
  that added, amended, repealed or substituted it (`am No 197, 2012`), and a table of Acts with their numbers and
  assent dates. The register's version list says on which day each Act took effect for the title. For each changed
  file:
  1. the Acts its amendment-history row cites (or, failing that, the row of a part or division heading in the file)
     that the version list dates within the compilation's period. Exactly one Act: that Act, on its day
     ({unit_kinds.get('act', 0)} file changes). An Act that took effect on two days within the period is dated on the
     later one, and the commit says so.
  2. Two or more: none is credited; the register commits it on the latest of their days and names them as
     `Candidate:` ({unit_kinds.get('candidates', 0)}).
  3. An Act new in the history that the register dates after this compilation: the compilation printed it early; the
     register commits it on the compilation's day, labelled, not credited ({unit_kinds.get('early', 0)}). Dated
     before the previous compilation: printed late ({unit_kinds.get('late', 0)}).
  4. An editorial change recorded in the endnotes (`ed C93`): the register, labelled ({unit_kinds.get('editorial', 0)}).
  5. Letter case, punctuation or spacing only: the compilers' style, the register, labelled
     ({unit_kinds.get('style', 0)}).
  6. Otherwise no Act: the register commits it, labelled ({unit_kinds.get('unexplained', 0)}).
  A compilation whose text is the same as the one before gets an empty commit. Every labelled change is listed in
  `data/labelled.csv`.
- **One commit per Act per day.** All of one Act's credited changes on one day, in every title, are one commit
  ({merged} commits touch more than one title); an Act's own text as made is in the commit of the day it took effect.
- **Date.** The day the change took effect, as above; changes between compilations are dated on the day the register
  gives for the Act, even when no compilation shows that day's text. Only compilation days are checked against
  official text. The same day's commits come in this order: first versions, Acts (by year and number), candidates,
  printed early or late, editorial, style, unexplained, unchanged.
- **Commits.** {sum(kinds.values())} law commits ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}), then
  one for this documentation.
  - The author is the Act: `Privacy Amendment Act 2004 (No. 49, 2004)` at `act-2004-49@legislation.invalid`. The
    Constitution's first version is authored by the Commonwealth of Australia Constitution Act (63 & 64 Vict. c. 12),
    a title that starts at a compilation by that compilation (`Privacy Act 1988 (compilation of 1997-06-18)`).
  - Commits no Act is credited with are by `Federal Register of Legislation (Office of Parliamentary Counsel)` at
    `register@legislation.invalid`.
  - Author and committer dates are noon in Canberra on the day the change took effect: AEST (UTC+10), or AEDT (UTC+11)
    in daylight saving time, with the New South Wales/ACT rules since 1971 written out in `canberra_offset()`. Git
    cannot record a date before 1970: earlier commits are dated 1 January 1970 (UTC), one second apart in order, and
    the real day is in `In-force:`. The committer is `Lexifina <australia-law@lexifina.com>`.
  - Trailers: `Kind:`, `Assented:`, `In-force:`, `Evidence:`, `Affecting:` (one per provision), `Candidate:`,
    `Named-in-history:`, `Date-note:`, `Status:`, `Source:` (the Act as made on the register), `Compilation:` and
    `Compilation-id:` (the compilation whose text it is).
  - Tags: each compilation day (`privacy-act/2014-03-12`), which is the text of that compilation, and each Act
    (`act/2012-197`; an Act that took effect on several days has one tag per day, `act/2021-5/2022-07-01`);
    {len(tags)} in all.
- **Documentation commit.** Dated {today}, the day the data was retrieved, not the day of the run, so a later rebuild
  of the same data gives the same hash.

## Decisions made by hand

These choices were made when the tools were written, after looking at the data. They are in the code.

1. **The titles.** The Constitution; every Act the register lists as in force (principal Acts with all their
   compilations; amending Acts as made); and every repealed or ceased principal Act that has at least one registered
   compilation, through its compilations to its repeal. Repealed Acts never compiled and repealed amending Acts are
   not taken.
2. **The endnotes plus the version list are the evidence.** A change is credited to an Act only when the provision's
   own amendment history cites it and the register dates it within the period; where two Acts are possible, neither
   is credited.
3. **The first readable version starts each title** (above).
4. **The section is the unit**, as the register numbers it; subsections are lines.
5. **Future versions** (a compilation dated after {today}) are not in this record.

## What an AI model did

This repository was built in **Claude Code**, Anthropic's coding tool, by the model {MODEL}, working from a brief by
the user at Lexifina: first a "git for law" repository with two Australian titles, then its expansion to every
Commonwealth Act on the register, to the same standard as Lexifina's other corpora.

The model:

- **Researched the source:** the register, its terms of use and reuse terms, and the shape of the compilations and
  their endnotes.
- **Wrote all the code and text in this repository:** the tools in `tools/`, the README, this file and the licence
  notice, and chose the design described in them.
- **Ran the commands above** and checked the results.

The model did **not**:

- **Write or edit any legal text.** Every word of every provision is copied by the script from the register's files.
- **Take part in building the history.** No AI model is called when the history is built or checked.

The user decided what was in scope and decides whether the repository is ever made public. The work was not reviewed
line by line by a lawyer. The authorised text of Commonwealth law is the one on the Federal Register of Legislation.

## Prior art

Lexifina's other corpora, whose rules this one follows (Hong Kong, Slovakia, Liechtenstein, Switzerland and the
others). No code was copied from outside Lexifina.
"""
    (repo / 'REPRODUCE.md').write_text(text, encoding='utf-8', newline='\n')


def gate_published(repo: Path):
    """Every non-law file in the repository (the top-level files, data/ and tools/; not the title folders) through
    publish_gate before it is committed: a file the rule keeps private stops the build, and home paths are scrubbed."""
    paths = [p for p in repo.iterdir() if p.is_file()]
    for top in ('data', 'tools'):
        if (repo / top).is_dir():
            paths += [p for p in (repo / top).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files = {p.relative_to(repo).as_posix(): p.read_bytes() for p in paths}
    for name, data in publish_gate(files).items():
        if data != files[name]:
            (repo / name).write_bytes(data)
