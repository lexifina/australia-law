"""The register's catalogue of Acts, read from the build cache (inventory.py fills it): every Act title, every version,
every EPUB file, and the titles this record takes, in order, with their folders.

Taken: the Constitution; every Act in force; every repealed or ceased principal Act with at least one registered
compilation (its as-made text and its compilations, then its repeal).
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as B  # noqa: E402
import inventory as I  # noqa: E402

CONSTITUTION = 'C2004Q00685'
# The folders of the first two titles, kept from the first build of this record.
LEGACY = {'C2004Q00685': ('constitution', 'Constitution'), 'C2004A03712': ('privacy-act', 'Privacy Act 1988')}
MAX_SLUG = 72


def slugify(name: str) -> str:
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
    if len(s) > MAX_SLUG:
        s = s[:MAX_SLUG].rsplit('-', 1)[0]
    return s


def load(offline: bool = True) -> dict:
    titles = {t['id']: t for t in I.titles(offline)}
    titles[CONSTITUTION] = B.api_json(f"titles('{CONSTITUTION}')", offline)
    docs: dict[str, list] = {}
    for d in I.epubs(offline):
        docs.setdefault(d['titleId'], []).append(d)
    vers: dict[str, list] = {}
    try:
        for v in I.versions(offline):
            vers.setdefault(v['titleId'], []).append(v)
    except SystemExit:
        if not os.environ.get('AU_PARTIAL_VERSIONS'):
            raise
    for tid in (CONSTITUTION, 'C2004A03712'):   # testing before the list is complete: the per-title lists
        if tid not in vers and os.environ.get('AU_PARTIAL_VERSIONS'):
            vers[tid] = B.api_json('versions', True, **{'$filter': f"titleId eq '{tid}'", '$orderby': 'start'})['value']
    return {'titles': titles, 'docs': docs, 'versions': vers}


def tier(t: dict, docs: list) -> int | None:
    """0 the Constitution, 1 a principal Act in force, 2 a repealed principal Act with a compilation, 3 another Act in
    force (an amending Act, as made); None: not taken."""
    if t['id'] == CONSTITUTION:
        return 0
    compiled = any(d.get('registerId') and d['registerId'] != t['id'] for d in docs)
    if t.get('isInForce') and t.get('isPrincipal'):
        return 1
    if t.get('isPrincipal') and compiled:
        return 2
    if t.get('isInForce'):
        return 3
    return None


def selection(cat: dict, tiers=(0, 1, 2, 3)) -> list[dict]:
    """The titles taken, sorted by register ID, each with its folder (unique), short name and register record."""
    out = []
    for tid in sorted(cat['titles']):
        t = cat['titles'][tid]
        k = tier(t, cat['docs'].get(tid, []))
        if k is None or k not in tiers:
            continue
        out.append({'id': tid, 'tier': k, 'name': t['name'], 'title': t})
    used: set[str] = set()
    for a in out:
        slug, short = LEGACY.get(a['id'], (None, None))
        a['short'] = short or a['name']
        a['citation'] = (f'No. {a["title"]["number"]}, {a["title"]["year"]}' if a['title'].get('number') else '')
        a['slug'] = slug or slugify(a['name'])
    # Two titles with the same name (rare): the later register IDs get the ID in their folder.
    for a in out:
        if a['slug'] in used:
            a['slug'] = f'{a["slug"][:MAX_SLUG - 12]}-{a["id"].lower()}'
        used.add(a['slug'])
    return out


def register(cat: dict, a: dict) -> dict:
    return B.register_from(a['id'], a['title'], cat['versions'].get(a['id'], []), cat['docs'].get(a['id'], []))


def fetch_order(sel: list[dict]) -> list[dict]:
    return sorted(sel, key=lambda a: (a['tier'], a['id']))
