# Australia: law history

This repository will hold the legislation of Australia (AU) as a history: one file per provision and one commit per law taking effect, dated by the day it took force, with the law that made each change as the commit author. It follows the shared design of [lexifina/global-law](https://github.com/lexifina/global-law), which places it at `au/` in the world tree.

**Status:** no law yet. This first commit records where the law of Australia can be read and what a build would need.

**Can it be built?** Ready to build: an official source has past versions by date and an API or bulk download.

**This commit is a placeholder.** The first real build must replace this history from scratch and force-push. It must never append law commits on top of it: they are dated by when each law took effect, often decades earlier, so dates in `main` would run backwards and break every on-date lookup. Builders should refuse to append when the root commit is this placeholder (its subject ends "sources, before any law").

**Official sources**

- [AustLII](https://www.austlii.edu.au): Point-in-time for NSW in full and SA and Queensland in part. Its usage policy forbids scraping, mirroring, API access and building AI datasets.
- [Federal Register of Legislation](https://www.legislation.gov.au): Every compilation since 2013, some from 1973. The API is live, needs no key, and asks you to get in touch before a full crawl.
- [NSW legislation](https://legislation.nsw.gov.au/help/export): Any historical version as XML by date. Scrape outside NSW business hours.
- [Queensland Legislation](https://www.legislation.qld.gov.au): Free API with sign-up; XML from 2016. The only Australian state with a public API.
- [Victorian Legislation](https://www.legislation.vic.gov.au): Tables of previous versions.
- [WA Legislation](https://www.legislation.wa.gov.au): Historical versions.
- [SA Legislation](https://www.legislation.sa.gov.au): Bulk dataset stopped in 2019.
- [Tasmanian Legislation](https://www.legislation.tas.gov.au)
- [ACT Legislation Register](https://www.legislation.act.gov.au): Republications.
- [NT Legislation](https://legislation.nt.gov.au)

All sources, with their status, are in [SOURCES.md](SOURCES.md). How these files were made is in [REPRODUCE.md](REPRODUCE.md).

This repository describes sources; it is not legal advice and holds no legal text yet.
