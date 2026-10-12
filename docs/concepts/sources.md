# Sources and the three categories

JobScout fetches only from places that publish job data on purpose. That rule decides what
gets built and what never will.

## The three categories of employer

| Category | What it is | JobScout does | Examples |
|---|---|---|---|
| **1 - documented ATS API** | The ATS vendor publishes a public, keyless job-board API | Fetches directly; one line in `companies.yaml` | Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable, BambooHR, Breezy |
| **2 - public JSON, undocumented** | The employer's own careers site is a front end over a JSON endpoint anyone's browser calls | Fetches directly; `detect.py --url` fingerprints the page and prints the slug | Workday (CXS), Oracle HCM, Phenom, iCIMS |
| **3 - protected** | Bot protection, login walls, or no API at all | **Never fetched.** Reached only via aggregators; listed on the Watchlist for manual checks | Tesla, Apple, Microsoft, LinkedIn, Indeed, Eightfold- and SuccessFactors-hosted sites |

!!! danger "Not negotiable"
    No scraping of LinkedIn or Indeed, no bypassing bot protection or logins, no captcha
    solving - in fetching or in applying. Contributions that cross this line are declined.

## Direct boards

`config/companies.yaml` holds ~530 boards as `{name, ats, slug}`. Each refresh fetches them
8 at a time; keyword-only platforms (Workday, iCIMS, Phenom, Oracle HCM, amazon.jobs) are
queried once per `search_terms` entry of the active preset. Every posting passes through:

1. `_norm()` - one normalised shape (`company, title, location, url, description, posted_at,
   updated_at, salary_text, employment_type, department, loc_class, work_mode`);
2. `is_relevant()` - the preset's title regexes, or the description rescue rule;
3. `_keep_location()` - your location policy.

Only postings that pass all three are stored.

## Aggregators

| Source | Needs | Notes |
|---|---|---|
| Adzuna | free API key | keyword queries per home location + a remote sweep, plus per-company title sweeps for category-3 employers; snippets, not full descriptions |
| Remotive, RemoteOK, The Muse | nothing | remote/US boards |
| Hacker News "Who is hiring" | nothing | monthly thread via Algolia |

Aggregator rows rank below direct ones when the same job appears twice.

## Finding a company's board

```bash
python detect.py "Company"                       # tries every ATS slug pattern
python detect.py --url https://careers.x.com "X" # fingerprints the page (category 2)
python discover.py --names list.txt --fingerprint
```

Watch for **slug collisions**: a small unrelated startup can answer on a big company's guessed
slug (Recruitee, Breezy and BambooHR are the usual culprits). A large employer with a
3-posting board is the tell; verify with `--url` before trusting a name guess. The
[Learnings](../LEARNINGS.md) page records every collision found so far.

## Posted vs updated

Boards disagree about dates. JobScout stores `posted_at` (first published: Greenhouse
`first_published`, Ashby `publishedAt`, …) and `updated_at` (the employer's last edit, where
the board reports it). The age filter and the sort can use either.
