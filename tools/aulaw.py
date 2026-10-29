"""Prints an Act, or one provision of it, as it stood on a given day, from this repository's history.

    python tools/aulaw.py 2015-01-01 privacy-act s6          # section 6 of the Privacy Act 1988 on 1 January 2015
    python tools/aulaw.py 2015-01-01 privacy-act 6A          # section 6A (letters follow the number)
    python tools/aulaw.py 2015-01-01 privacy-act "sch1 cl3"  # Schedule 1, clause 3 (Australian Privacy Principle 3)
    python tools/aulaw.py 2015-01-01 privacy-act sch1        # the heading of Schedule 1
    python tools/aulaw.py 2015-01-01 privacy-act intro       # long title and preamble
    python tools/aulaw.py 1975-01-01 constitution s128       # section 128 of the Constitution on 1 January 1975
    python tools/aulaw.py 1975-01-01 constitution cc5        # covering clause 5 of the Constitution Act
    python tools/aulaw.py 2020-01-01 privacy-act             # the whole Act on 1 January 2020
    python tools/aulaw.py 2020-01-01 privacy-act --commit    # the commit that holds that day's text, and who made it

Each commit carries the day its change took effect in an `In-force:` trailer. The text in force on a day is the last
commit on or before it (commits before 1970 are dated 1 January 1970 by git; the trailer has the real day). The
Constitution's record starts on 1900-01-01 (the register's date for it), the Privacy Act's on 1997-06-18 (its first
registered compilation). Standard library only.
"""
import re
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout


def _n(s: str) -> tuple:
    m = re.match(r'(\d+)(.*)$', s)
    if not m:
        return (0, s)
    # Australian numbering: s 6AA is inserted between s 6 and s 6A (but s 18BAA comes after s 18BA)
    suffix = m.group(2)
    return (int(m.group(1)), '!' + suffix if suffix.startswith('AA') else suffix)


def order(path: str) -> tuple:
    """The order of an Act's files: long title, covering clauses, sections, schedules (heading, then clauses)."""
    key = path.split('/', 1)[1][:-3]
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


def file_for(key: str) -> str:
    """'6' or 's6' -> s-006.md, '6A' -> s-006A.md, 'cc5' -> cc-5.md, 'sch1' -> sch-1.md, 'sch1 cl3' -> sch-1/cl-03.md,
    'sch' -> sch.md; anything else is a file name."""
    k = key.strip()
    m = re.fullmatch(r'(?:s|section)?\.?\s*(\d+)([A-Za-z]*)', k, re.I)
    if m:
        return f's-{int(m.group(1)):03d}{m.group(2).upper()}.md'
    m = re.fullmatch(r'cc\.?\s*(\d+)', k, re.I)
    if m:
        return f'cc-{int(m.group(1))}.md'
    m = re.fullmatch(r'sch(?:edule)?\s*-?\s*(\d*[A-Za-z]*)(?:\s*,?\s*(?:cl|clause)\.?\s*(\d+)([A-Za-z]*))?', k, re.I)
    if m:
        sch = f'sch-{m.group(1).upper()}' if m.group(1) else 'sch'
        return f'{sch}/cl-{int(m.group(2)):02d}{m.group(3).upper()}.md' if m.group(2) else f'{sch}.md'
    return k if k.endswith('.md') else f'{k}.md'


def commit_on(day: str, slug: str) -> str:
    out = git('log', '--format=%H%x09%(trailers:key=In-force,valueonly,separator=)', 'main', '--', slug)
    for line in out.splitlines():
        h, _, d = line.partition('\t')
        if d.strip() and d.strip() <= day:
            return h
    return ''


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    args = [a for a in sys.argv[1:] if a != '--commit']
    if len(args) < 2:
        sys.exit(__doc__)
    day, slug = args[0], args[1].strip('/')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        sys.exit(f'{day} is not a date (YYYY-MM-DD).')
    commit = commit_on(day, slug)
    if not commit:
        sys.exit(f'{slug} is not in the record on {day}.')
    if '--commit' in sys.argv:
        sys.stdout.write(git('show', '-s', '--format=%H%n%an%n%B', commit))
        return
    if len(args) > 2:
        name = file_for(' '.join(args[2:]))
        try:
            sys.stdout.write(git('show', f'{commit}:{slug}/{name}'))
        except subprocess.CalledProcessError:
            sys.exit(f'{slug}/{name} was not in the text on {day}.')
        return
    blobs = {}
    for line in git('ls-tree', '-r', commit, f'{slug}/').split('\n'):
        if line:
            info, f = line.split('\t', 1)
            if f.endswith('.md') and f != f'{slug}/README.md':
                blobs[f] = info.split()[2]
    names = sorted(blobs, key=order)
    data = subprocess.run(['git', 'cat-file', '--batch'], input=''.join(f'{blobs[f]}\n' for f in names).encode(),
                          capture_output=True, check=True).stdout
    pos = 0
    for f in names:   # one git process for every file, in the Act's order
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        sys.stdout.write(data[nl + 1:nl + 1 + size].decode('utf-8') + '\n')
        pos = nl + 1 + size + 1


if __name__ == '__main__':
    main()
