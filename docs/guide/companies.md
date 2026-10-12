# Companies and Watchlist

## Companies page

Every company that has ever produced a stored job, plus every configured board, in one
table: jobs found all-time, active, applied, best score, board config (`ats:slug` or *via
aggregators*), last fetch result (kept count or the error), tracked since, and a per-board
**↻** that refreshes just that board.

Filters: name search (debounced; 300 rows painted at a time with *show all*), ATS, "has a
board", "has active jobs". Headers sort.

### Add a company

Type the name and press **Check availability**. JobScout probes every ATS slug pattern for it.
Paste the careers-page URL too for Workday / Oracle HCM / Phenom / iCIMS sites - those are
fingerprinted from the page, not guessed.

| Result | Options |
|---|---|
| Found on `<ats>` with N postings | **Add to JobScout** → appended to `companies.yaml` and fetched immediately |
| Already configured | shows the existing line |
| No public board found | **Add to Adzuna company sweeps** (per-company title queries through Adzuna) and/or **Add to watchlist** |

!!! tip "Slug collisions"
    A famous name answering with 1-3 postings on Recruitee, Breezy or BambooHR is almost always
    a different, small company. Prefer the URL route for large employers.

## Watchlist

`config/watchlist.yaml` → `/watchlist`. Employers JobScout cannot fetch (category 3: bot
protection, login walls, no API) with their careers URL, the platform note from detection,
whether they are covered by Adzuna sweeps, and how many Adzuna rows mention them. A place for
the manual weekly check, nothing more. When one of them moves to a fetchable ATS, add it from
the Companies page and remove it here.
