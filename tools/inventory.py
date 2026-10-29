"""The register's lists of every Act title, every version and every EPUB file of an Act, read from the build cache of
retrieved pages (catalogue.py reads them through these functions).

    python -X utf8 scripts/australia-law/inventory.py --summary  # counts and sizes from the cache
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

PAGE = 500
TITLE_SELECT = 'id,name,makingDate,collection,isPrincipal,isInForce,status,year,number,seriesType'
DOC_SELECT = 'titleId,start,retrospectiveStart,rectificationVersionNumber,volumeNumber,compilationNumber,registerId,sizeInBytes'


def pages(path: str, flt: str, select: str, order: str, offline: bool):
    skip = 0
    while True:
        q = {'$filter': flt}
        if select:
            q['$select'] = select
        q.update({'$orderby': order, '$top': str(PAGE), '$skip': str(skip)})
        j = build.api_json(path, offline, **q)
        rows = j['value']
        yield from rows
        if len(rows) < PAGE:
            return
        skip += PAGE


def titles(offline: bool) -> list[dict]:
    return list(pages('titles', "collection eq 'Act'", TITLE_SELECT, 'id', offline))


def epubs(offline: bool) -> list[dict]:
    return list(pages('documents', "type eq 'Primary' and format eq 'Epub' and startswith(titleId,'C')", DOC_SELECT,
                      'titleId,start,rectificationVersionNumber,volumeNumber', offline))


def versions(offline: bool) -> list[dict]:
    """Every version of every Act and of the Constitution: the day it took effect and the Acts that made it."""
    return list(pages('versions', "startswith(titleId,'C')", None, 'titleId,start,retrospectiveStart', offline))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--summary', action='store_true')
    ap.add_argument('--versions', action='store_true')
    a = ap.parse_args()
    a.summary = True   # the lists are read from the cache only
    ts = titles(a.summary)
    print(f'titles: {len(ts)}', flush=True)
    ds = epubs(a.summary)
    print(f'epub rows: {len(ds)}', flush=True)
    if a.versions:
        vs = versions(a.summary)
        print(f'version rows: {len(vs)}', flush=True)
    if not a.summary:
        return
    inforce = {t['id'] for t in ts if t.get('isInForce')}
    principal = {t['id'] for t in ts if t.get('isPrincipal')}
    by: dict[str, dict] = {}
    for d in ds:
        k = (d['titleId'], d['start'][:10])
        if d['volumeNumber'] != 0:
            by.setdefault(d['titleId'], {}).setdefault('vol', set()).add(k[1])
        best = by.setdefault(d['titleId'], {}).setdefault('days', {})
        if k[1] not in best or d['rectificationVersionNumber'] > best[k[1]]['rectificationVersionNumber']:
            best[k[1]] = d
    def tally(ids):
        n = sum(len(by.get(i, {}).get('days', {})) for i in ids)
        b = sum(sum((x.get('sizeInBytes') or 0) for x in by.get(i, {}).get('days', {}).values()) for i in ids)
        multi = sum(1 for i in ids if len(by.get(i, {}).get('days', {})) > 1)
        vol = sum(1 for i in ids if by.get(i, {}).get('vol'))
        return {'titles': len(ids), 'with_2plus_versions': multi, 'files': n, 'gb': round(b / 1e9, 2), 'volumes': vol}
    out = {'all': tally({t['id'] for t in ts}), 'in_force': tally(inforce), 'principal': tally(principal),
           'in_force_principal': tally(inforce & principal),
           'repealed_principal_multi': tally({i for i in principal - inforce if len(by.get(i, {}).get('days', {})) > 1})}
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
