# Jobs page

The inbox. Everything stored that passes your location policy, with scores, filters and the
actions that drive the rest of the pipeline.

## Header

- **Stat line**: jobs tracked, last refresh time, new-this-refresh count; the auto-refresh
  schedule and next run; the refresh progress strip while a refresh is running.
- **Report**: every source of the last refresh with its kept count or error.
- **Rescore**: re-applies `config/skills.yaml` instantly.
- **Refresh**: fetches every source (10-15 minutes for ~530 boards).

## Tabs

Inbox · Viewed · Starred · Saved · Applied · Not interested · Hidden · All · **Resumes**. Counts
respect the active filters. The Resumes tab is the tailoring and applying workspace (see
[Resume tailoring](tailoring.md) and [Applying](applying.md)).

## Filters

| Filter | Notes |
|---|---|
| Search | title, company, description |
| Min score | reads the fit score where one exists, else the keyword score |
| Age | last 24h / 3 / 7 / 14 / 30 days, **by posted** (first published) or **by updated** (employer's last edit) |
| New this refresh | only postings inserted by the most recent refresh (the Discord digest links here) |
| Locations | multi-select, OR semantics: Home metro, Home state, Remote (US), Unknown |
| Mode | remote / hybrid / office |
| Roles | the labels from the active preset's `role_titles` |
| Company | substring |
| Sources | Direct boards (all) / Aggregators (all) / individual sources |
| Work auth | H4-safe default (citizenship/clearance-required jobs hidden) / show all / only no-sponsorship / only citizen-only |
| Sort | fit · keyword score · newest posted · newest updated · source · sponsorship |
| **Role view** | one click: home + home state + US remote, the preset's role labels, direct boards, 30 days. Named after the preset (e.g. *SRE view*). |

Results paint 300 cards at a time with a *Show 300 more* button; the count line says
"showing the top N of M" when the server capped the response.

## A card

```
[ 85 fit ]  Company  Title ↗           COMPANY  LOCATION  JOB TYPE  SOURCE  SPONSORSHIP   [☐][✎][★][🔖][✓][👎][✕][⌄]
[ kw 92 ]   📍 location · mode · dept · posted 2026-10-05 · updated 2026-10-08 · 💰 salary · +2 duplicates
            flag chips · skill-group chips
            fit reason … · gap chips                      ▼ too high  ✓  ▲ too low
            resume strip (when tailoring has run)
```

- **Score box**: fit (big) with keyword score beneath; keyword only until fit exists; `fit…`
  while queued; `*` when scored under an older profile version.
- **Actions**, all toggles: ☐ select for bulk tailoring, ✎ tailor, ★ star, 🔖 save, ✓ applied
  (also stamps viewed and skips any un-run application plan), 👎 not interested, ✕ hide,
  ⌄ details (score breakdown + full description). Marking a job applied or not interested also
  removes it from the tailoring and apply queues.
- **Thumbs** on the fit line feed the [learning loop](../concepts/scoring.md#the-learning-loop).

## Bulk actions

**Select all shown**, then **✎ Tailor resumes** (ticked jobs, or the whole current list when
nothing is ticked; capped by `resume.batch_max`) or **Fit-score** (ticked jobs, or every gated
job without a score).
