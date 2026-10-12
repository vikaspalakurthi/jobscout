# HTTP API

Everything the pages do goes through this JSON API on `http://localhost:8765`. No
authentication - the server binds loopback only. All responses carry `Cache-Control: no-store`.

## Jobs

| Method & path | Purpose |
|---|---|
| `GET /api/jobs` | the list. Query: `tab` (inbox · viewed · starred · saved · applied · not_interested · hidden · all), `q`, `min_score` (reads `COALESCE(fit, score)`), `loc`, `mode`, `role`, `source` (comma lists; `direct` / `aggregator` expand), `company`, `auth` (default hides citizen-only; `all`, `citizen_only`, `no_sponsorship`), `days` + `days_by` (`posted` / `updated`), `new=1`, `sort` (fit · score · date · updated · source · auth). Returns `jobs` (duplicates collapsed, capped at 1,500), `tab_counts`, `total`, `truncated`. |
| `GET /api/jobs/<id>` | full row with description, breakdown, flags |
| `POST /api/jobs/<id>/status` `{status}` | set a status (toggle semantics live in the UI); `applied` stamps `applied_at`, `viewed_at` and skips an un-run plan; `not_interested` / `hidden` drop the job from both queues |
| `POST /api/jobs/<id>/viewed`, `/notes` | timestamps and notes |
| `GET /api/facets` | companies, roles, sources for the filter widgets |
| `GET /api/status` | `version` (from `VERSION`), counts, last-refresh report, `refresh` progress (`running, phase, done, total, failed, started, trigger`), `auto_refresh` (`at, every_hours, next`), `home`, `view` |
| `POST /api/refresh` `{company?}` / `{companies?: []}` | full or partial refresh in the background; 409 while one runs |
| `POST /api/rescore` | re-apply `skills.yaml` to every stored job |

## Fit scoring

| Method & path | Purpose |
|---|---|
| `GET /api/fit/status` | queue, done/failed, ETA, scored/stale counts, gate candidates, profile version, pending feedback |
| `POST /api/fit/start` `{job_ids?, min_score?, limit?}` | queue gated jobs (given ids, or all without a score) |
| `POST /api/jobs/<id>/fit/feedback` `{verdict, note}` | verdict `too_high` / `right` / `too_low`; may trigger a profile update |
| `GET /api/fit/profile` | the current `fit-profile.md`, version, recent feedback |
| `POST /api/fit/profile` | fold pending feedback into the profile now; re-queues stale jobs |

## Presets, settings, companies

| Method & path | Purpose |
|---|---|
| `GET /api/presets` | shipped presets and the active one |
| `POST /api/presets/load` `{id}` | copy a preset over `skills.yaml` (backup kept) and rescore |
| `GET /api/settings`, `POST /api/settings` | sections `adzuna, filters, home, refresh, resume, apply, notify, fit`; POST writes `local.yaml` |
| `POST /api/settings/test_adzuna`, `/test_notify`, `/test_claude` | connectivity checks |
| `GET /api/companies` | every company with cumulative stats and board config |
| `POST /api/companies/detect` `{name, url?}` | probe or fingerprint a company's ATS (synchronous; Workday discovery can take a minute) |
| `POST /api/companies/add` `{name, ats, slug}` | append to `companies.yaml` (validated, reverted on failure) |
| `POST /api/companies/add_sweep` `{name}` | add to the Adzuna company sweeps |
| `POST /api/companies/add_watch` `{name, url?, platform?}` | append to `watchlist.yaml` |
| `GET /api/watchlist` | the watchlist with live Adzuna coverage |

## Resume tailoring

| Method & path | Purpose |
|---|---|
| `POST /api/resume/start` `{job_ids, include_applied?, regenerate?}` | queue tailoring (citizen-only, applied, ready, in-progress skipped; capped) |
| `GET /api/resume/status` | everything the Resumes tab needs: queue, workers, per-job states, open questions |
| `POST /api/resume/answer` `{answers:[{job_id, skill, answer, context, scope}]}` | answer analyze questions; `scope: global` writes the inventory |
| `POST /api/resume/queue` `{job_id, action}` | reorder / dequeue; `POST /api/resume/pause {paused}`; `POST /api/resume/parallel {n}` |
| `POST /api/jobs/<id>/resume/retry`, `/open`, `/qa`, `/cover` | retry, open the folder, generate Q&A answers, generate cover letters |
| `GET /api/jobs/<id>/resume/notes`, `GET /api/jobs/<id>/apply/files` | notes; the files a filler can use (resume, cover docx, cover text long/short) |

## Applying

| Method & path | Purpose |
|---|---|
| `POST /api/apply/plan` `{job_ids}` | read the form and build plans |
| `POST /api/jobs/<id>/apply/review` `{action: approve · skip · reset · fix, fixes?, mode?}` | review actions |
| `POST /api/apply/approve` `{job_ids}`, `POST /api/apply/enqueue` `{job_ids}`, `POST /api/jobs/<id>/apply/dequeue` | bulk approve, queue, dequeue |
| `POST /api/jobs/<id>/apply/draft_answers` | draft the plan's essay questions |
| `GET /api/apply/status` | plans by state, queue |
| `GET /api/apply/queue/next?mode=&runner=` | **filler**: lock and hand out the next queued plan (`{done: true}` when empty) |
| `POST /api/apply/result` `{run_id, outcome, reason?, screenshot?, form_hash_seen?, confirmation?}` | **filler**: report `prefilled · submitted · held · failed` |
| `POST /api/jobs/<id>/apply/mark_submitted` | you submitted a prefilled form yourself |
| `POST /api/jobs/<id>/apply/screenshot`, `GET …/screenshot/<kind>` | filled-form screenshots |

## Pages

`/` Jobs · `/companies` · `/watchlist` · `/settings` · `/about`.
