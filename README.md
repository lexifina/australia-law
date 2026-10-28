# Australian (Commonwealth) law, change by change

The Acts of the Commonwealth Parliament as a git repository: the Commonwealth of Australia Constitution Act (the
Constitution), every Act in force, and the repealed principal Acts that have a registered compilation, each with every
compilation the register keeps. Each amending Act is one commit per day it took effect, across all the titles it
changed, credited to that Act and dated that day, at noon in Canberra. The text is the text of the compilations on the [Federal Register of Legislation](https://www.legislation.gov.au),
the authorised website for Commonwealth law, read through its public API.

## The questions, as git commands

| Question | Command |
| --- | --- |
| What did section 6 of the Privacy Act say on 1 January 2010? | `python tools/aulaw.py 2010-01-01 privacy-act 6` |
| The whole Privacy Act as it stood on 1 January 2000 | `python tools/aulaw.py 2000-01-01 privacy-act` |
| Australian Privacy Principle 11 on 1 July 2020 | `python tools/aulaw.py 2020-07-01 privacy-act "sch1 cl11"` |
| Section 51 of the Constitution on 1 January 1950 | `python tools/aulaw.py 1950-01-01 constitution 51` |
| Section 128 of the Constitution before and after the 1977 referendum | `git diff act/1977-84~1 act/1977-84 -- constitution/s-128.md` |
| Which Act wrote each line of section 13 of the Privacy Act? | `git blame privacy-act/s-013.md` |
| Every change to section 13, with the Act and the day | `git log --follow privacy-act/s-013.md` |
| Everything one Act changed | `git show --stat act/2012-197` |
| What changed between two compilations | `git diff --stat privacy-act/2022-07-01 privacy-act/2022-11-14` |
| The commit that holds a day's text, and why it was made | `python tools/aulaw.py 2015-06-01 privacy-act --commit` |
| Every change with its Act, day and compilation | [`changes.csv`](changes.csv) |
| Changes two Acts could have made (credited to neither) | `git log --grep="Kind: two or more Acts"` |
| Changes a compilation printed before the Act took effect | `git log --grep="Kind: printed before"` |
| Changes no Act explains | `git log --grep="Kind: no Act named"` |
| Acts of 2024 that changed the Privacy Act | `git log --format="%an %s" --author=", 2024)" -- privacy-act` |
| Section 5 of the Crimes Act 1914 on 1 January 2000 | `python tools/aulaw.py 2000-01-01 crimes-act-1914 5` |
| Every title, its folder, compilations and status | [`data/titles.csv`](data/titles.csv) |
| When an Act was repealed, and by what | `git log --grep="^Status: Repealed" --format="%ad %s" --date=short` |

## The dates, and which one we use

A Commonwealth Act has the day it received the Royal Assent and the day (or days) its provisions commenced.
**Commits are dated the day the change took effect**, at noon in Canberra: AEST (UTC+10), or AEDT (UTC+11) in daylight
saving time. That day comes from the register's version list, which records each day the title changed and the Acts that
changed it; the assent date is in an `Assented:` trailer. Many Acts commence in stages: the Privacy Amendment (Public
Health Contact Information) Act 2020 added Part VIIIA on 16 May 2020 and took it out at its sunset on 14 November 2022,
which are two commits and two tags. Every commit carries an `In-force: YYYY-MM-DD` trailer. To ask what the law was on a
day with plain git, use the end of that day: `git rev-list -1 --before="2014-03-12T23:59:59+11:00" main -- privacy-act`
(a bare date is read at the time of day you run it). `tools/aulaw.py` does this by the trailer.

Git cannot record a date before 1970: the Constitution's commits before then are dated 1 January 1970, one second apart,
and the real day is in `In-force:`. The register dates the Constitution's first version 1 January 1900; the Constitution
itself took effect on 1 January 1901. The day is kept as the register gives it.

The register keeps a **compilation** (the Act as amended up to a day) for most versions, but not all. Where several Acts
took effect between two compilations, each change is dated on the day of the Act that made it, so some days in this
history show a text no compilation printed. Only compilation days are checked against official text; each has a tag
(`privacy-act/2014-03-12`).

## What "author" means

The author of a commit is the Act that made the change, with an address ending `@legislation.invalid` that cannot
exist: `Privacy Amendment (Enhancing Privacy Protection) Act 2012 (No. 197, 2012)`, so `git blame` names the Act. Each
compilation's endnotes have an amendment history, a row per provision naming the Acts that added, amended, repealed or
substituted it ("am No 197, 2012"); a change is credited to the Act in that row that the register says took effect
between the two compilations. Rules, each labelled in the commit:

- Where two or more such Acts are in a provision's row, the change is credited to none of them: it is committed by the
  `Federal Register of Legislation (Office of Parliamentary Counsel)`, with the Acts named in `Candidate:` trailers.
- Where a compilation prints a change before the day the register gives for the Act (the compilation of 29 October 2001
  prints the Privacy Amendment (Private Sector) Act 2000, which the register dates 21 December 2001), or after it, the
  change is kept on the compilation's day, labelled, and credited to no Act.
- Editorial changes the endnotes record, changes of letter case or punctuation in the compilers' style, and changes the
  endnotes do not explain (older compilations were restructured: schedules set out as clauses, headings restyled) are
  committed by the register and labelled.
- The Constitution's first version is authored by the Commonwealth of Australia Constitution Act (63 & 64 Vict. c. 12).
  A title starts at the Act as made, authored by the Act, in the commit of the day it took effect. Where the Act as made
  is on the register without the marks that identify its sections (older Acts, converted from print), the title starts
  at its first compilation, authored by that compilation (the Privacy Act at 18 June 1997); the amendments made before
  then are in its text and not separated out.
- One Act that took effect on one day for several titles is one commit touching all of them.
- A repealed title's folder is removed on the day the register records the repeal, in the repealing Act's commit.
- Blame credits whole lines. Where two Acts changed words of one line, the later one is credited with the line; use
  `git log -p --word-diff` for the words.

## How it is checked

`tools/check_repo.py` reads every compilation of each title back from git on the day it took effect and compares it with
the official file: file by file with the text parsed from the compilation, and word for word with all the words of the
compilation, read independently of the parser. `tools/test_repo.py` checks that the commands above work. The results are
in [`REPRODUCE.md`](REPRODUCE.md).

## Files

Each title is a folder named after its name (`crimes-act-1914/`); the first two titles keep their earlier folders
(`constitution/`, `privacy-act/`). [`data/titles.csv`](data/titles.csv) maps folders to register IDs. Each
section is one file named after its number:
`privacy-act/s-006.md` is section 6, `s-006A.md` section 6A. The Constitution Act's covering clauses are `cc-1.md` to
`cc-9.md`, the sections of the Constitution `s-001.md` to `s-128.md`, and its Schedule (the oath) `sch.md`. A schedule's
heading is `sch-1.md` and its clauses `sch-1/cl-01.md` (Schedule 1 of the Privacy Act holds the Australian Privacy
Principles; Schedule 2, since 2025, the statutory tort for serious invasions of privacy). `intro.md` is the long title
and preamble. The headings of the chapters, parts and divisions that begin with a section are `##` lines at the top of
its file; its number and heading are the `#` line; then one line per paragraph (subsection, paragraph, definition, note,
penalty). A repealed section is removed. Each title's folder has a `README.md` listing its files with how often each
changed.

## What it does not show

- Legislative instruments (regulations, rules), and State and Territory law.
- Repealed Acts that were never compiled, and repealed amending Acts.
- A title before its first version the parser can read (for many older Acts, the Act as made); `data/titles.csv`
  gives each title's first day.
- The text of a day between two compilations other than as rebuilt from the Acts' days (above).
- Which of two Acts of one period made a change, where both are in the provision's history (above).
- The endnotes, the notes about the compilation and the tables of contents, which the register publishes with each
  compilation.
- Schedules that amend other Acts are kept as text; their effect on those Acts is not shown.

The authorised text is the one on the Federal Register of Legislation.

## How it is built

`tools/inventory.py` and `tools/fetch_all.py` read the register's API into a cache, and `tools/bulk.py` (with the per-title rules in `tools/build.py`) writes this repository from scratch; the same data gives the same hashes.
Every step, the software, the decisions made by hand and what an AI model did are in [`REPRODUCE.md`](REPRODUCE.md).
[`corpus.json`](corpus.json) describes the corpus for tools that read several of Lexifina's law repositories. After a
rebuild, `git fetch --force --tags` replaces stale tags in an existing clone.

## Licence

Based on content from the Federal Register of Legislation at 28 October 2026. For the latest information on Australian
Government legislation please go to https://www.legislation.gov.au. The content is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); it has been changed (split into files by provision, white
space and some typography normalised, history reconstructed). See [`LICENSE`](LICENSE). **This repository is private.**
