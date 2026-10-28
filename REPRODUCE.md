# How this history was made, and how to make it again

This file records exactly how the repository was built: the sources, the commands, the software, the decisions made
by hand, and what an AI model did and did not do. The build writes it, so it describes the build that made this
commit.

## Short version

```sh
git clone https://github.com/lexifina/australia-law && cd australia-law
pip install lxml
python -X utf8 tools/inventory.py --versions      # the register's lists of titles, versions and files
python -X utf8 tools/fetch_all.py                 # every compilation file, one request every 10 seconds
python -X utf8 tools/bulk.py --offline --repo ../rebuilt --today 2026-10-28
git -C ../rebuilt rev-parse main        # the same hash as this repository's main
python -X utf8 tools/check_repo.py --repo ../rebuilt --today 2026-10-28
python -X utf8 tools/check_words.py --repo ../rebuilt --today 2026-10-28
python -X utf8 tools/test_repo.py --repo ../rebuilt
```

The tools need Python 3.12 or later with `lxml`, and git. They read the register's API into a cache (`.cache/` next to
the scripts) and write this repository into `--repo`. With the same files and the same `--today` the build gives the
same commits with the same hashes. `data/versions/<folder>.json` lists every compilation file read with its sha256, so
a rebuild can show it read the same bytes. The register may re-render or rectify a compilation; then the hashes change
too.

## Sources

All legal text and all information about versions and amending Acts come from the **Federal Register of Legislation**
(https://www.legislation.gov.au), the authorised whole-of-government website for Commonwealth legislation, managed by the Office of
Parliamentary Counsel under the Legislation Act 2003, through its public API (https://api.prod.legislation.gov.au/v1; OpenAPI description at
`https://api.prod.legislation.gov.au/swagger/index.html`; no key needed). Nothing was typed in, summarised or rewritten.

The build read the register's lists and 301 compilation files:

- `GET /v1/titles?$filter=collection eq 'Act'` (in pages of 500): every Act title with its status (in force or not)
  and whether it is a principal Act; and `GET /v1/titles('C2004Q00685')`, the Constitution.
- `GET /v1/versions?$filter=startswith(titleId,'C')` (in pages of 500): every version of every Act, with the day it
  started (its in-force day), its status and `reasons`: the amending Acts the register names for it, with their
  register IDs, numbers and years.
- `GET /v1/documents?$filter=type eq 'Primary' and format eq 'Epub' and startswith(titleId,'C')` (in pages of 500):
  which versions have a registered file, and its rectifications.
- `GET /v1/documents(titleid='<id>',start=<day>,retrospectivestart=<day>,rectificationversionnumber=<n>,type='Primary',uniqueTypeNumber=0,volumeNumber=0,format='Epub')`:
  each version's file as EPUB (the latest rectification), whose XHTML holds the text and the endnotes.

**Titles.** 199 titles: the Constitution (1), principal Acts in force (198),
repealed or ceased principal Acts with at least one registered compilation (0), and the other Acts in
force, which are amending Acts kept as made (0); 290 versions read in all. `data/titles.csv` lists
them with their folders. 10 titles taken could not be read and are left out:

- Constitution Alteration (Social Services) 1946 (`C1946A00081`): no version with the register's section markup
- States Grants (Secondary Schools Libraries) Act 1971 (`C1971A00114`): no version with the register's section markup
- Commonwealth Banks Act 1973 (`C1973A00018`): no version with the register's section markup
- Prices Justification Act 1973 (`C1973A00037`): no version with the register's section markup
- Loan Act 1973 (`C2004A00023`): no version with the register's section markup
- Parliamentary and Judicial Retiring Allowances Act 1973 (`C2004A00035`): no version with the register's section markup
- Petroleum and Minerals Authority Act 1973 (`C2004A00102`): no version with the register's section markup
- Repatriation Acts Amendment Act 1974 (`C2004A00148`): no version with the register's section markup
- Loan Act 1974 (`C2004A00200`): no version with the register's section markup
- Loan (War Service Land Settlement) Act 1975 (`C2004A00284`): no version with the register's section markup

**Terms of use.** The register's terms (https://www.legislation.gov.au/terms-of-use) put its content, except the Commonwealth Coat of Arms and where
otherwise noted, under the Creative Commons Attribution 4.0 International licence (CC BY 4.0). See `LICENSE` for the
attribution the register asks for. **Crawling.** robots.txt on www.legislation.gov.au allows all agents with
`Crawl-delay: 10` and disallows only `/assets/`; it names no AI agents. The API host has no robots.txt (404); the
fetch obeys the www Crawl-delay there too: one request at a time, one every 10 seconds, each URL once (the cache),
with a User-Agent naming Lexifina. The register's data share and reuse page (https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/data-share-and-reuse) allows automated downloading
and spidering, says the API "is for our users who frequently access large sets of our data", and says that before a
full site crawl "it is advisable to contact us 1-2 weeks beforehand" and that further incremental crawls are preferably
run outside 08:00-20:00 Australian time (UTC+10). The fetch dates are recorded when the fetch is complete. This repository is private.

## The build, step by step

1. `python -X utf8 scripts/australia-law/inventory.py --versions` and `python -X utf8 scripts/australia-law/fetch_all.py`
   in Lexifina's own repository filled the cache. `python -X utf8 scripts/australia-law/bulk.py --offline --today
   2026-10-28` wrote this repository. `tools/` here holds the same files as `scripts/australia-law/` there (`build.py` is
   git blob `272093020570a87213d64322ee4f29eec56386ca`, `bulk.py` `e77559a4338080ee5a71d22a2363baabc61b6471`).
2. `check_repo.py --today 2026-10-28` read every compilation of each title back from this repository with
   `tools/aulaw.py`, on the day it took effect, and compared it file by file with the official text parsed afresh
   from the cached EPUB; then it compared the whole Act, word for word, with all the words of the compilation's body
   read without the build's parser. Result: `93 compilations checked (22477 files), 0 mismatches; 93 compilations word for word (6984802 words) against the whole official text, 0 mismatches`.
3. `check_words.py --today 2026-10-28`, an independent check: the letters and digits of every compilation (casefolded,
   everything else removed), read from the raw XHTML by a separate minimal extractor (every text node of the body
   from the long title to the endnotes, except the table of contents, running headers and hidden elements), against
   the letters and digits of the files in git on that day. Result: (not yet run).
4. `test_repo.py --record` ran the functional smoke test (6 of 6 passed):
   - pass: blame names an Act: privacy-act/s-013.md lines by 6 authors, 4 Acts
   - pass: text before and after an amendment: privacy-act/s-013.md 2014-03-11 != 2014-03-12; constitution/s-128.md 1977-07-28 != 1977-07-29; constitution/s-051.md 1946-12-18 != 1946-12-19
   - pass: log --follow in date order: privacy-act/s-006.md 51 commits; privacy-act/s-013.md 8 commits; constitution/s-051.md 3 commits
   - pass: tags resolve, In-force on every commit: 190 tags resolve; 145 commits carry In-force and a subject of at most 72 characters
   - pass: only the documentation commit is by Lexifina: only the documentation commits (1) are by Lexifina; 94 distinct authors
   - pass: README commands run: 14 README commands ran and printed output
5. `bulk.py --offline --verify-rebuild` built the repository a second time from the same cache into a temporary
   folder and compared the hashes. A second build from the same cache into an empty folder gave the same history: the last law commit was `6e4055a77e94420f02afae92272cc4340ab0978e` in both, and every one of the 144 law commits and 190 tags had the same hash.
6. The build was run once more to write these results into this file, and the final `main` was rebuilt again and
   compared hash for hash before it was pushed.

It ran with Python 3.12.10, lxml 6.1.3 and git version 2.46.0.windows.1 on Windows 11. Git writes the law commits with `git fast-import` (one
stream: the files as blobs, then every commit in date order), then the documentation commit with `git commit`.
Compilations are parsed in a pool of 8 worker processes, and each parsed compilation is cached by the sha256 of its
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
  section markup. 25 titles start at the Act as made; the others start at their first registered
  compilation, because the Act as made is on the register as a text converted from print without that markup (the
  amendments made before the first compilation are then in its text, not separated out). Versions without a file reach
  the record through the next compilation, dated as below. `data/versions/<folder>.json` names them all.
- **Repeal.** A title the register records as repealed or ceased has its folder removed on that day
  (0 titles), in the repealing Act's commit when the register names one Act, otherwise in a commit by the
  register.
- **Changes.** Consecutive compilations are compared file by file.
- **Cause and date.** Each compilation's endnotes have an amendment history: a row per provision listing the Acts
  that added, amended, repealed or substituted it (`am No 197, 2012`), and a table of Acts with their numbers and
  assent dates. The register's version list says on which day each Act took effect for the title. For each changed
  file:
  1. the Acts its amendment-history row cites (or, failing that, the row of a part or division heading in the file)
     that the version list dates within the compilation's period. Exactly one Act: that Act, on its day
     (772 file changes). An Act that took effect on two days within the period is dated on the
     later one, and the commit says so.
  2. Two or more: none is credited; the register commits it on the latest of their days and names them as
     `Candidate:` (25).
  3. An Act new in the history that the register dates after this compilation: the compilation printed it early; the
     register commits it on the compilation's day, labelled, not credited (89). Dated
     before the previous compilation: printed late (5).
  4. An editorial change recorded in the endnotes (`ed C93`): the register, labelled (1).
  5. Letter case, punctuation or spacing only: the compilers' style, the register, labelled
     (19).
  6. Otherwise no Act: the register commits it, labelled (78).
  A compilation whose text is the same as the one before gets an empty commit. Every labelled change is listed in
  `data/labelled.csv`.
- **One commit per Act per day.** All of one Act's credited changes on one day, in every title, are one commit
  (0 commits touch more than one title); an Act's own text as made is in the commit of the day it took effect.
- **Date.** The day the change took effect, as above; changes between compilations are dated on the day the register
  gives for the Act, even when no compilation shows that day's text. Only compilation days are checked against
  official text. The same day's commits come in this order: first versions, Acts (by year and number), candidates,
  printed early or late, editorial, style, unexplained, unchanged.
- **Commits.** 341 law commits (act 121, candidates 9, early 1, editorial 1, late 5, start 175, style 7, unexplained 22), then
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
    411 in all.
- **Documentation commit.** Dated 2026-10-28, the day the data was retrieved, not the day of the run, so a later rebuild
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
5. **Polite fetching.** One request at a time, 10 seconds apart (the Crawl-delay), one fetch per URL ever.
6. **Future versions** (a compilation dated after 2026-10-28) are not in this record.

## What an AI model did

This repository was built in **Claude Code**, Anthropic's coding tool, by the model Claude Opus 5.5 (model id `claude-opus-5-5`), working from a brief by
the user at Lexifina: first a "git for law" repository with two Australian titles, then its expansion to every
Commonwealth Act on the register, to the same standard as Lexifina's other corpora.

The model:

- **Researched the source:** the API and its OpenAPI description, the terms of use, robots.txt on both hosts, the
  data share and reuse page, and the shape of the compilations and their endnotes.
- **Wrote all the code and text in this repository:** the tools in `tools/`, the README, this file and the licence
  notice, and chose the design described in them.
- **Ran the commands above** and checked the results.

The model did **not**:

- **Write or edit any legal text.** Every word of every provision is copied by the script from the register's files.
- **Take part in building the history.** No AI model is called when the history is built or checked.

The user decided what was in scope and decides whether the repository is ever made public. The work was not reviewed
line by line by a lawyer. The authorised text of Commonwealth law is the one on the Federal Register of Legislation.

## Prior art

Lexifina's other corpora, whose rules this one follows (Hong Kong, Slovakia, Liechtenstein, Switzerland and the others
in `scripts/SMALL-COUNTRY-BRIEF.md`). No code was copied from outside Lexifina.
