# JobScout architecture

Local Flask app. One SQLite DB. No build step, no external services except the job
APIs themselves. Single user (the author), single machine.

## Sources: three categories of employer (read this before adding a fetcher)

1. **Rented ATS with a documented public feed** - Greenhouse, Lever, Ashby, SmartRecruiters,
   Recruitee, Workable, BambooHR, Breezy. One-line config, `detect.py "<Company>"` guesses the slug.
2. **Rented ATS without a documented feed** - the careers page is a web app that loads its
   jobs from a JSON endpoint (no auth, no captcha); we call the same endpoint. Supported:
   **Workday** (CXS `/wday/cxs/<tenant>/<site>/jobs`), **Oracle HCM** (`/hcmRestApi/resources/
   latest/recruitingCEJobRequisitions`, `fetch_oraclehcm`, slug `apihost:siteNumber[:publichost]`),
   **Phenom** (`POST /widgets` refineSearch + the job page's embedded `phApp.ddo` for the
   description, `fetch_phenom`, slug `host:/pathprefix`), **iCIMS** (`fetch_icims`: "Attract"
   sites `careers.<co>.com/api/jobs` JSON, classic `<x>-<co>.icims.com` portals parsed from
   `/jobs/search` HTML + job pages). Identifiers come from `python detect.py --url <careers
   url> "<Company>"`, which fingerprints the page (script hosts, redirect targets) and prints
   the companies.yaml line; it also names platforms it recognises but cannot fetch yet
   (Eightfold - API refuses without the page token; SuccessFactors; Jobvite; Taleo).
3. **Own builds behind bot protection** - Apple, Tesla, Google, Meta, Microsoft, Indeed
   (`detect.py --url` reports "bot-protected"). Adzuna only; never worked around.

Each category-2 fetcher: search each `BIGBOARD_TERMS` term, keep `TITLE_RELEVANT` /
`desc_relevant` hits, fetch up to ~50 detail pages per company, route through `_norm()` and
`_keep_location()`. Sources `oraclehcm`, `phenom`, `icims` are in `DIRECT_SOURCES` and
`SOURCE_PRIORITY` (priority 0, like the other boards).

## Data flow

```
fetchers.run_all()                    server.py /api/refresh (background thread)
  ├─ company boards (companies.yaml)   or refresh.py (CLI)
  │    greenhouse / lever / ashby / smartrecruiters / workday / oraclehcm / phenom / icims
  │    recruitee / workable / bamboohr / breezy
  │    fetched in a ThreadPoolExecutor (settings refresh.board_workers, default 8),
  │    results folded in completion order; progress(msg, done, total, failed, phase)
  ├─ big-tech fetchers: amazon (amazon.jobs JSON), oracle (ORC API)
  ├─ aggregators: adzuna (queries + company title-sweeps), remotive,
  │               remoteok, themuse, hn (Algolia "Who is hiring")
  ▼
 normalize (_norm): classify location + work mode, infer employment type
  ▼  prefilter: is_relevant(title, desc) = TITLE_RELEVANT regex OR desc_relevant
  ▼  location gate: _keep_location per filters config
 db.upsert_jobs  — INSERT new / UPDATE job fields by dedupe_key(source:external_id);
                   never touches status/viewed_at/applied_at/notes
  ▼
 scoring.rescore_all — recomputes for ALL rows: score, breakdown, flags, role,
                       work_auth, employment backfill; loc_class is refreshed when the
                       stored class is vague (unknown/remote_global/us_other) and the
                       classifier now sees remote_us/texas/austin (or non_us); work_mode
                       'office' flips to remote/hybrid when the location text says so
  ▼
 UI reads /api/jobs (filters, sorts, dedupe-collapse by fingerprint)
```

## DB schema (data/jobs.db, table `jobs`)

Core: id, dedupe_key (unique, `source:external_id`), fingerprint (company|title,
cross-source dedupe), source, company, title, location, url, description, posted_at,
salary_text, first_seen, last_seen.

Classified (recomputed by rescore; a rescore adopts the classifier's fresh verdict when the
stored class is unknown/remote_global/us_other and the fresh one is more specific, when the
fresh one is non_us, or when a stored non_us is no longer non_us - so classifier fixes
self-heal): loc_class (austin/texas/remote_us/remote_global/
us_other/non_us/unknown), work_mode (remote/hybrid/office/unknown), employment_type,
role (best-matching role label), work_auth ('' = OK / no_sponsorship / citizen_only),
score, breakdown (json), flags (json), department.

User state (NEVER overwritten by fetch): status (new/starred/saved/applied/
not_interested/hidden), viewed_at, applied_at, notes.

Migrations: idempotent `ALTER TABLE ... ADD COLUMN` in `db.init()` under try/except.

`meta` table: `last_refresh` = per-source stats + per-company results (feeds the
Companies page and the Report button).

- `posted_at` = the employer's first-publish date; `updated_at` = their last edit where the board
  reports it (Greenhouse `updated_at`, Ashby `updatedAt`; NULL elsewhere). Both are job fields
  refreshed on every upsert.
- `fit`, `fit_reason`, `fit_gaps` (JSON), `fit_at`, `fit_version`, `fit_state`, `fit_error`
  — model fit scoring (fit.py); table `fit_feedback` holds the thumbs. See "Fit scoring".

## Scoring (config/skills.yaml — user-tunable, rescore applies instantly)

score = title role match (max ~30, best match wins; `title_blockers` reject outright;
`title_penalties` support `unless` context guards — dev titles penalized unless ops
context) + seniority bonus + skills groups found in description (capped 40)
+ location points + experience-fit + freshness bonus (posting date) + direct-board
bonus (source_points) − flag penalties (citizenship/ITAR heavy; no-sponsorship tiny;
flags match literal `terms` plus optional regex `patterns` (for phrasing families like
"be a US/U.S./United States citizen"; the hit shown is the matched text), and support
`ignore` context windows for boilerplate). Clamped 0–100.

work_auth is derived from flags at rescore time. Freshness/source need
`all_jobs_for_scoring` to include source + posted_at.

## Fit scoring (fit.py) — the model score beside the keyword score

The keyword score cannot tell "SRE who runs Kafka" from "Kafka internals expert"; it is kept
as a cheap **prefilter and fallback**, and `fit` (0–100) is the number the UI sorts and
filters on when present (`sort=fit`; the min-score slider is `COALESCE(fit, score)`).

- **Inputs** (all in gitignored `resume/`, sent only through the user's own subscription via
  `claude -p`, prompt on stdin): `fit-profile.md` (judgement rules — created with a default
  on first use, editable by hand), `skills-inventory.md` (levels; written by inventory.py
  from tailoring Q&A, so tailoring answers also sharpen fit), `preferences.md`.
- **Gate** (`fit.gate_ids`): status new/starred/saved, not citizen-only, loc_class in
  austin/texas/remote_us, keyword score ≥ `fit.gate_min_score` (60), and no fit yet or
  `fit_version` < current profile version ("stale").
- **Scoring**: batches of `fit.batch` (10) per `claude -p --model fit.model` call (Haiku 4.5
  default); `thinking` full/small/off maps to `MAX_THINKING_TOKENS` unset/2048/0 (~90/45/30 s
  per batch). Strict-JSON reply → `fit`, `fit_reason`, `fit_gaps` (JSON list), `fit_at`,
  `fit_version`; `fit_state` queued/scoring/failed/'' + `fit_error`. Worker pool mirrors
  tailor.py (`fit.parallel`, default 2). `fit.after_refresh(new_ids)` runs from
  `_do_refresh` after rescoring; `fit.start()` (Fit-score button / `POST /api/fit/start`)
  queues up to `max_per_run` gated jobs; `fit.recover()` on server start clears
  queued/scoring leftovers.
- **Learning loop**: card thumbs → `fit_feedback` rows (verdict too_high/too_low/right,
  note, score snapshot). After `fit.auto_profile_after` (5) pending items — or Settings →
  "Update profile now" / `POST /api/fit/profile` — `fit.update_profile()` has
  `fit.profile_model` (Sonnet) rewrite `fit-profile.md` (backup `.bak-<ts>` kept; refuses a
  reply without "## Learned adjustments"), marks feedback applied, bumps meta
  `fit_profile_version`; every scored job is now stale and `start()` re-queues the gated
  ones. First real run 2026-10-03: one thumbs-down on a DBA-flavoured SRE moved Backblaze
  62→50 and RELX DBA Lead 60→50 while RELX's general SRE II scored 93.
- Endpoints: `GET /api/fit/status` (queue, done/failed, ETA, scored/stale counts,
  gate_candidates, profile_version, feedback_pending), `POST /api/fit/start`
  `{job_ids?|min_score?|limit?}`, `POST /api/jobs/<id>/fit/feedback {verdict, note}`,
  `GET/POST /api/fit/profile`. `/api/jobs` rows carry `fit*` + `fit_stale`.
- CLI: `python fit.py score <id>…` (synchronous, prints), `gate`, `profile`, `status`.

## Server API (server.py)

- `GET /api/jobs` — params: tab, q, min_score, days, company (LIKE), auth
  ('' = exclude citizen_only default / no_sponsorship / citizen_only / all),
  multi-value CSV: loc, mode, role, source (source accepts 'direct'/'aggregator'
  groups + names). Sorts: score/date/source/auth; viewed tab defaults to viewed_at
  DESC. Returns jobs (deduped, order-preserving) + `tab_counts` computed under the
  same filters (drives the tab badges).
- `POST /api/jobs/<id>/status` (toggle semantics live in the UI; **applied also stamps
  viewed_at** — applying implies viewing), `/viewed`, `/notes`;
  `GET /api/jobs/<id>` (full description + breakdown).
- The server listens on both `127.0.0.1` and `::1` (`server.serve`): with IPv4 only,
  `localhost` cost ~2 s per request on Windows (IPv6 first, then fallback). `python perf.py`
  times every UI endpoint on both names and fails on anything over 500 ms.
- `fetchers.load_settings` / `load_companies` are mtime-cached YAML loads (deep-copied on
  return); settings edits still apply immediately, nothing needs a restart for config.
- All responses carry `Cache-Control: no-store` (after_request hook) — a stale cached
  index.html once made status clicks look broken; never remove this.
- `POST /api/refresh` (optional {company} or {companies:[]} for partial board refresh —
  merges into last_refresh meta), `GET /api/status`, `POST /api/rescore`.
  `/api/status.refresh` = `{running, message, phase, done, total, failed, started, trigger}`;
  phase walks boards → aggregators → saving → scoring, done/total count within the phase
  (index.html draws the progress strip from it). `/api/status.auto_refresh` =
  `{every_hours, next}`.
- **Scheduled refresh** (`server.start_refresh` + `_auto_refresh_loop`): a daemon thread
  started with the user's main instance (skipped when `JOBSCOUT_PORT` is set, so a preview
  server never double-refreshes). Every minute it re-reads `settings.refresh` (Settings →
  Refresh schedule, saved to local.yaml): `at: "06:00, 11:00, 18:00, 23:00"` (PC-local clock
  times, the default) fires `start_refresh(trigger="auto")` once per slot — a slot that
  passed while the server was down, or before it started, is skipped, never run late;
  with `at` empty, `every_hours > 0` fires that long after the previous run. `/api/status
  .auto_refresh.next` is the next slot as a local ISO time. Nothing runs while the server
  is down — there is no OS task; `python refresh.py` is the CLI equivalent if one is ever
  wanted in Task Scheduler (never alongside the in-server schedule: two refreshes at once).
- `GET /api/jobs`: `days=N` ages by `posted_at`, or by `COALESCE(updated_at, posted_at)` with
  `days_by=updated`; sorts `fit | score | date (posted) | updated | source | auth`.
- `GET /api/jobs` reads every matching row (no SQL LIMIT), collapses duplicates by
  fingerprint, then caps the response at `PAGE_CAP` (1500) and returns `total`/`truncated`
  so the UI can say "showing the top N of M". (A pre-dedupe LIMIT made wide OR filters
  return fewer cards than narrow ones.)
- `GET /api/companies` (board config + cumulative stats), `GET /api/facets`
  (roles/companies/sources for filter widgets).
- index.html paints `PAGE` (300) cards per render with a "Show 300 more" button; one
  delegated click/change handler on `#list` serves every card; a status click redraws only
  that card (`statusClick`) and then reloads the list with `loadJobs({keep: true})` so the
  page position is kept.
- Company management: `POST /api/companies/detect` {name} (synchronous probe of all
  platforms via detect.detect_company — Workday discovery can take minutes),
  `POST /api/companies/add` {name, ats, slug} (appends a line to companies.yaml,
  validates it re-parses, reverts on failure), `POST /api/companies/add_sweep` {name}
  (adds to adzuna.companies via local.yaml), `POST /api/companies/add_watch` {name, url,
  platform} (inserts into config/watchlist.yaml at the end of the `companies:` block - the
  file also has a `boards:` key, so a plain append lands in the wrong list; re-parses and
  reverts on failure). The Companies page wraps these in the
  "Add a company" card; it also has client-side filters and sortable headers. The table
  paints at most `ROW_CAP` (300) rows per render with a "show all N" link, and the search
  box is debounced (150 ms) — re-laying out 1,500+ rows on every keystroke froze the tab.
- Settings: `GET/POST /api/settings` (writes config/local.yaml; sections adzuna/filters/
  refresh/resume/apply/notify; GET also returns `resume_models`), `POST /api/settings/test_adzuna`,
  `POST /api/settings/test_claude` (claude --version + a 1-line `-p` round trip).
- Resume tailoring: `POST /api/resume/start` {job_ids, include_applied, regenerate} →
  {queued, skipped{citizen_only,applied,already_ready,in_progress,batch_limit}};
  `GET /api/resume/status` {queue{workers{id:{job_id,name,task,phase,started,phase_started}},
  parallel, queue[{job_id,task,name}], queued, running, paused, message, stats{ready_today,
  failed_today, avg{analyze,generate,answer,cover}}, eta_seconds, log[]}, jobs[...with open
  questions + phases[{phase,seconds,ok,started,worker}]]}; `POST /api/resume/parallel` {n};
  `POST /api/resume/queue` {job_id, task, action front|back|remove}; `POST /api/resume/answer` {answers:[{job_id, skill, question, answer
  have|familiar|dont-have, context, scope job|global}]} → re-queues jobs whose questions
  are all answered; `POST /api/resume/pause` {paused}; `POST /api/jobs/<id>/resume/retry`
  {skip_questions?} (skip = unanswered questions are dropped → generate);
  `POST /api/jobs/<id>/resume/open` {what: file|folder} (os.startfile — local app);
  `GET /api/jobs/<id>/resume/notes`. `/api/jobs` rows carry resume_state/_path/
  _question_count/_error/_updated_at.
- Pages: `/` `/companies` `/watchlist` `/settings` `/about` from `static/`.
- `GET /api/watchlist` — config/watchlist.yaml (unfetchable companies) enriched with
  live Adzuna coverage (sweep membership + job counts seen).

## Frontend (static/index.html)

- Filter bar: multi-selects are OR within a filter, AND across filters. The **SRE view**
  preset button sets Austin + Texas + US remote, role SRE only (the SRE title rule in
  skills.yaml also covers systems/service/platform/network/security/data reliability engineer
  and production engineer; hardware/manufacturing "reliability" titles are blocked), direct
  boards only, last 30 days (`setMsel()` re-renders a multi-select
  programmatically; `MSEL_OPTS` remembers each widget's options). `DIRECT_SET` must list every
  direct source (incl. oraclehcm/phenom/icims) or cards label them "aggregator".

Vanilla JS. Tabs = status views (toggle buttons per card: star/save/applied/
not-interested/hide + details). Multi-select filter widgets (makeMsel), datalist
company box, auth dropdown. Card right side = aligned info columns: Company, Location,
Job type, Source(+kind), Sponsorship. Viewed is auto-marked on title-link click and
detail expand. Facets and the source dropdown populate dynamically — new sources need
no UI change.

Resume tailoring UI: per-card ✎ button + a strip under the chips showing the pipeline
state plus a phase timeline (`analyze 41s · generate 2m 18s`, from the status feed) (queued/analyzing/needs_answers/generating/ready/failed) with the one relevant
action; filter-bar **✎ Tailor resumes** queues the ticked jobs (checkbox per card, "Select all
shown"/clear helpers; button reads "Tailor selected (N)") or, with nothing ticked,
everything currently listed, after an inline confirm dialog (skips citizen-only / applied / already-ready unless ticked);
**Resumes** tab (also `/#resumes` on load) renders from `/api/resume/status`: a **Pipeline
panel** on top (worker slots with phase/elapsed/progress vs typical, ordered queue with
Front/Remove, parallel ±, pause, today's KPIs, queue ETA, live log — refreshed in place
every poll without touching answer drafts), then questions
grouped **by job** (header row + that posting's questions, each labelled with the
requirement it came from); a question several jobs share is listed under each of them
with "Also asked by …" and the rows are kept in sync, so one answer → every job that
asked (submit de-duplicates by group; each job card has its own "Submit & generate <company>"
button next to the global one; the job row also offers Skip questions & generate and
**Not interested**, which sets the job status and thereby drops it from both pipelines), answer = have /
familiar / dont-have + context + optional "Also remember" (global); in-progress /
ready / failed lists (a ready resume whose job is applied, apply-submitted or whose
application was skipped moves to a collapsed **Finished** section: Open resume / Folder /
Notes, plus Restore on skipped ones); Pause queue. `pollResume()` polls every 3s while the queue is
active (15s idle) and re-renders cards whose state changed.

## Repo layout for contributors

- `templates/resume/*.example.*` — copies of the gitignored `resume/` fact files with placeholder
  data; keep them in step with `apply.py`'s profile keys.
- `tests/test_smoke.py` — offline unittest suite (classifiers, upsert → rescore, status survival,
  API on a temp DB). CI (`.github/workflows/ci.yml`) runs it with pyflakes on Linux and Windows.
- Cover-letter DOCX names derive from `resume.file_name` (`tailor.cover_files()`); nothing in
  tracked code carries a person's name.

## Config precedence

`config/settings.yaml` (committed defaults) ← deep-merged under `config/local.yaml`
(gitignored; Adzuna keys, Settings-page writes). Env vars ADZUNA_APP_ID/KEY also work.

## Resume tailoring (tailor.py + .claude/skills/tailor-resume)

Runs the `tailor-resume` Claude Code skill headlessly (`claude -p`, user's Max login, no
API key) to produce a tailored DOCX per job. Design mockup: the "JobScout resume
pipeline" artifact (Sep 2026); decisions below are the contract.

```
enqueue(job_ids, task)  ->  ordered queue drained by N worker threads (resume.parallel, 1-3)
   queued -> analyzing --(unknown skills)--> needs_answers --(user answers)--> queued
                       --(nothing unknown)-> generating -> ready | failed
```
**Worker pool (tailor.py):** `_queue` (ordered list, visible/reorderable from the panel),
`_busy` (one task per job at a time), `_target["n"]` (live-adjustable via
`set_parallel`; extra workers retire when idle). Parallel is safe because workers never
write shared files: **`inventory.py`** (lock + backup, ASCII-enforced) writes "Also
remember" answers into `resume/skills-inventory.md` at answer time, and `answers.md` tells
the skill to skip its inventory step. `recover()` at server start re-queues `queued` jobs,
fails resume runs cut off by a restart, and resets Q&A/cover tasks left queued/running
(ready if an earlier result exists, else failed). Every phase is wrapped by `_phase()` which records a
`resume_runs` row (job, task, phase, worker, model, started, finished, seconds, ok, error)
— that table feeds the panel's averages, ETA and the per-job phase timeline. Design
mockup: "JobScout pipeline workers" artifact (Sep 2026). **Decoupling rule:** the future
apply pipeline (`apply.py`, `apply_*` columns, own queue/panel) only reads the job folder
and `resume_state == 'ready'`; nothing in tailor.py will import it.
- **Phase analyze**: writes `job.md` (posting snapshot + JobScout matched skills) into
  the job folder, runs the skill with read-only tools, parses the JSON question list
  (first `{...}` block - the skill sometimes wraps it in prose). Stores questions +
  `session_id`. Questions already answered for this job are filtered out and passed as
  known facts on re-analyze.
- **Phase generate**: writes `answers.md` (each answer tagged `job-only` or `global`),
  resumes the analyze session (`--resume`), lets the skill write markdown + DOCX into
  `.work/`, then rotates the previous version into `history/<ts>_vN/` and moves the new
  files into place. The skill's closing summary is saved as `notes.md` + `resume_notes`.
  Only `global` answers reach `resume/skills-inventory.md` (the skill appends them);
  `resume/_backups/` keeps the last 20 inventory copies, taken before any global write.
- **Folder layout** (`resume.output_dir`, default `resume/output`, gitignored):
  `<Company-slug>/<Role-slug (<=50 chars, word boundary)>/<jobId>/` containing
  `<resume.file_name>.docx` (the upload; name from `resume.file_name`), `resume.md`,
  `notes.md`, `job.md`, `answers.md`, `history/`.
- **Guards**: runner closes stdin, ignores stderr unless exit!=0, per-phase timeout
  (`resume.timeout_min`), `claude` resolved via PATH with fallback to
  `~/.local/bin/claude.exe` (server may predate the PATH change).
- **DB**: `jobs.resume_state/_questions(json)/_session_id/_path/_notes/_error/_updated_at`
  (`db.set_resume`), table `resume_answers(job_id, skill UNIQUE, question, answer
  have|familiar|dont-have, context, scope job|global)`. Resume columns are never touched
  by fetch/rescore.
- **Settings** (`resume:` in settings.yaml, overridable in local.yaml): output_dir,
  file_name, claude_cmd, model (`--model`; default claude-fable-5-1), timeout_min,
  batch_max, parallel (1-3; the panel's ± writes it to local.yaml via `/api/resume/parallel`).
- **Extras (after a resume is ready)**: phase `answer` drafts application-form answers
  (questions + optional limit, optional job-only personal notes, optional feedback +
  previous draft for regenerate) into `qa.md` (`## Qn:` / `### Long` / `### Short`, parsed
  by `_parse_qa`) and stores them in `jobs.resume_qa` (json: questions, notes, feedback,
  answers[], state, summary). Phase `cover` writes `cover-letter.md` (`## Long` / `## Short`)
  and builds `<stem>_Cover_Letter.docx` + `_short.docx` via `build_docx.js --cover`,
  stored in `jobs.resume_cover`. Both go through the same worker (`enqueue(ids, task)` with
  task qa|cover), keep the previous version in `history/`, and follow the writing guide.
  Endpoints: `POST /api/jobs/<id>/resume/qa` {questions:[{text,limit}], notes, feedback},
  `POST /api/jobs/<id>/resume/cover` {notes, feedback}; `open` accepts what=cover|cover_short;
  `/api/jobs` rows carry the parsed `resume_qa` / `resume_cover`; the status feed carries
  only `resume_qa_state` / `resume_cover_state`.
- **CLI**: `python tailor.py analyze|generate|run|status <job_id>`, `answer <id> "Q"...`,
  `cover <id>`, `check 0`.
- Timings (Fable 5.1): analyze ~45s, generate ~4 min. Opus 5.5: ~20s / ~1.5 min.

## Apply pipeline, stage 1: plans + review (apply.py)

Independent of tailoring: reads the job folder + `resume_state`, owns `jobs.apply_state /
apply_plan(json) / apply_error / apply_updated_at`. No browser in this stage.

- **eligibility(job)** gates every plan: refused for jobs already `applied`, already
  `submitted`, citizen-only, or without a ready resume file (`/api/apply/plan` reports them
  under `skipped`; the auto-planner and the UI never offer them).
- **readiness(job)** is the pre-plan hint shown on every ready row (`apply_hint` in the
  resume status feed): ATS, whether the form is readable offline, captcha/account caveats,
  and whether unattended submit is possible (`ATS_HINT` table).
- **plan(job_id)** reads the real application form: Greenhouse via
  `boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>?questions=true` (custom questions +
  `demographic_questions` EEO block; `plan.apply_url` is the standalone form
  `job-boards.greenhouse.io/embed/job_app?for=<board>&token=<id>` whenever the posting's
  `absolute_url` is a careers page, because those embed the form in a cross-origin iframe
  the filler cannot read; the careers page stays in `plan.page_url`), Ashby via the public GraphQL endpoint
  `jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting` (`applicationForm.sections.
  fieldEntries.field` is a JSON scalar: title/type/path/selectableValues), Lever by parsing
  `jobs.lever.co/<site>/<id>/apply` (standard inputs + `cards[..][baseTemplate]` JSON for
  custom questions + `eeo[...]` selects; hCaptcha present → prefill-only). Other ATS
  (Workday…) get an empty field list and a "fill-time discovery" flag.
- **resolve()** maps each field label/type/options to a value from `resume/profile.yaml`
  (gitignored; name/contact/location, authorization, sponsorship=no, salary policy,
  `years_experience` for "years of experience" pickers (`_years_option` picks the range
  containing it, else the open-ended "N+" option; a Yes/No phrased "8+ years" compares the
  threshold; management / leadership experience questions are left to the user), "at least 18" → yes, EEO answers,
  consents), the job's Q&A answers (token-overlap match, long/short by stated limit), or
  the job folder (resume/cover DOCX). Optional custom essay questions stay `None` (note
  "optional - Draft answers writes it") and the server's `_ensure_answers()` (run on every
  `/api/apply/status` and after `/api/apply/plan`, like `_ensure_covers`) queues the Q&A phase for
  them automatically - each question once, no retry loop; Draft answers is the manual re-trigger;
  `refresh_from_qa` re-plans when the answers land. Optional choice/unknown
  fields are blanked. "Currently employed" → yes when a current employer is on file; the
  availability rule needs a start/notice phrasing ("high availability" no longer matches). Ashby's `Location` and `LongText` field types are
  treated as location / free text. Every branch returns `(value, source, note)`. Unresolved required fields and unmatched
  custom questions land in `plan.needs` → state `needs_input`; otherwise `planned`.
- Each plan carries its own execution `mode` (prefill | unattended; default from settings,
  survives re-plans, forced to prefill when the form has a captcha or the ATS isn't in
  `allowed_ats`), set from the review card's selector.
- **review(action, fixes, mode)**: `fix` stores per-field overrides (kept across re-plans)
  and/or the execution mode, and keeps an `approved`/`queued` plan in that state (the UI's
  mode selector posts a fix; it must not dequeue),
  `approve` (refused while `needs` is non-empty; stamps `approved_at`), `skip`, `reset`.
  `plan.form_hash` (labels+types+options) is what the future filler must match before an
  unattended submit.
- States: '' | planned | needs_input | approved | skipped | failed — stage 2 will add
  filling | prefilled | held | submitted.
- Endpoints: `GET /api/apply/status`, `POST /api/apply/plan` {job_ids}, `POST
  /api/jobs/<id>/apply/review` {action, fixes}, `POST /api/apply/approve` {job_ids},
  `POST /api/jobs/<id>/apply/draft_answers` (queues the Q&A phase for the plan's open
  custom questions; `refresh_from_qa()`, run on every status read, re-plans a needs_input
  job once its Q&A is newer than the plan, so the answer lands without a manual re-plan).
  Custom-question answers default to the Q&A version closest to ~200 words that fits any
  stated limit. A form with a cover-letter slot (required or optional) sets
  `plan.wants_cover`; the server's `_ensure_covers()` (on status reads and after planning)
  enqueues the cover task for such jobs — the bridge lives in server.py so apply.py and
  tailor.py never import each other — and `refresh_from_qa()` re-plans once the letter is
  ready, attaching the long DOCX. Settings `apply:` (mode prefill|unattended,
  window, allowed_ats, auto_plan, profile). `auto_plan` runs an apply-side poller
  (`start_auto_planner`) so tailor.py never imports apply.py.
- UI: **Apply** panel under the Pipeline panel in the Resumes tab (count pills double as
  click-to-filter, as do the Pipeline panel's pills for the lists below; "Plan ready
  jobs", "Approve all planned", per-job Review table with editable values → Save fixes,
  Approve/Skip/Re-plan/Draft answers); ready cards get a **Plan apply** button / state pill.
  Held / failed rows offer Retry, Mark submitted and Skip. In the review table, multi-select
  questions (`multi_value_multi_select`, `MultiValueSelect`, list values, "select all that
  apply") render as checkbox groups and save as lists; open editors' values are captured
  before every poll re-render and restored after it (`captureDrafts._plan`). `status()` adds the "Adzuna link"
  flag at read time for any plan whose job URL is on adzuna.com, so older plans show it too.
  Submitted applications are hidden from the panel's list (they live in the Applied tab) and
  only count toward the "submitted" pill - click it to list them; their mode selector is
  disabled, as it is while a plan is `filling`.
- CLI: `python apply.py plan|show <job_id>`.

## Filler helpers: tailored files on demand

`GET /api/jobs/<id>/apply/files` → `{dir, resume, cover, cover_state, resume_state,
cover_text{long,short}}` (cover text parsed from the job folder's `cover-letter.md`). The
`/apply-queue` playbook uses it to (a) always upload the plan's tailored resume, replacing any
profile resume a board pre-attaches (Dice Easy Apply, Workday profiles), and (b) when a form
has a cover-letter slot but no cover exists yet, `POST /api/jobs/<id>/resume/cover {}` and poll
this endpoint until `cover` is a path, then upload or paste it.

## Rejected jobs leave the pipelines

Marking a job **applied** by hand also skips any un-run application plan
(`_drop_queued_plan`; `apply.next_in_queue` double-checks status so a plan queued earlier is
never filled for an applied job).

`POST /api/jobs/<id>/status` with `not_interested` or `hidden` calls `_drop_from_pipelines`:
a queued / needs_answers tailoring is dequeued and its questions cleared (a finished resume
stays `ready` on disk), and an un-run application plan (planned / needs_input / approved /
queued) is skipped. `/api/resume/status` also excludes those statuses, so the Resumes tab never
shows a job the user rejected.

## Source policy knobs (Sep 2026)

- `adzuna.enabled` (Settings → Adzuna card): false skips the Adzuna sweep on refresh. Adzuna
  only ever returns 500-char snippets, so direct boards are the preferred source; Adzuna's
  employer names are the seed list for `discover.py`.
- `filters.max_age_days_boards` (default 60): direct boards keep evergreen reqs open for a
  year or more; postings older than this are dropped on refresh (`_too_old`, previously
  aggregators-only) and the **Inbox** tab hides existing rows older than it
  (`_inbox_age_clause`; other tabs and explicit date filters unaffected).

## New-job notifications (notify.py)

- `db.upsert_jobs()` returns `(new_count, new_ids)`; both refresh paths (server thread and
  `refresh.py`) store `new_ids` in `meta.last_refresh._summary` and call
  `notify.after_refresh(new_ids)` after scoring. It never raises: outcome goes into
  `_summary.notify` and the refresh status line ("Discord: 7 sent" / the error).
- `notify.pick()` keeps new rows with `status='new'`, score >= `notify.min_score`, `loc_class`
  in `notify.loc_classes`, not citizen-only, deduped by fingerprint; `build_embed()` makes one
  Discord embed (best first, `max_items` lines, description capped under the 4096 limit,
  footer with totals, title links to `/?new=1`). `send_test()` posts the 3 best of the last
  refresh. Settings section `notify` (enabled, discord_webhook_url, min_score, max_items,
  loc_classes) is edited on the Settings page → `config/local.yaml`; `POST
  /api/settings/test_notify` sends the sample.
- `GET /api/jobs?new=1` restricts to `new_ids` of the last refresh; the Jobs filter bar has a
  "New this refresh" checkbox, pre-ticked when the page is opened with `?new=1`.

## Apply pipeline, stage 2: queue + filler (apply.py + .claude/skills/apply-queue)

Design mockup: "JobScout apply filler" artifact (Sep 2026). Two halves: JobScout owns the
**queue**; a Claude session with the Chrome extension owns the **hands**, because the
extension is only reachable from a Claude session, not from Flask.

- **Queue**: `apply_state` approved --Apply now / Apply all approved--> `queued`
  (`apply_queued_at` orders it). `GET /api/apply/queue/next?runner=&mode=` locks the next plan
  (`mode=` is a **filter** on the plan's own mode, not an override: to run an unattended plan
  as prefill, switch its selector first)
  (state `filling`, opens an `apply_runs` row, returns plan + files + EEO answers + profile
  basics + run settings); `POST /api/apply/result` {run_id, outcome prefilled|submitted|held|
  failed, reason, screenshot, form_hash_seen, confirmation} closes the run and sets the state;
  `submitted` also marks the job **applied** and writes `applied.md` + `plan.json` in the job
  folder. `POST /api/jobs/<id>/apply/mark_submitted` (user clicked Submit on a prefilled tab),
  `/dequeue`, `POST /api/apply/enqueue` {job_ids} (refuses applied/submitted and unplanned
  jobs), `POST/GET /api/jobs/<id>/apply/screenshot[/<kind>]` (filled|held|confirmation PNG in
  the job folder). Settings: `unattended_enabled` (master switch -> next() downgrades every
  plan to prefill when false), `max_per_run`, `pause_seconds`, `window`.
- **Filler** = `.claude/skills/apply-queue/SKILL.md`: the playbook (pull next, read the live
  form, guards, type-not-set for validated inputs, verify uploads, page-only EEO from the
  profile, prefill leaves the tab open, unattended submits only when the live form matches
  the approved plan, report every outcome). Invoked by the user (`/apply-queue`) or by
  desktop scheduled tasks with a mode filter. Never solves captchas, never logs in.
- States after `queued`: filling -> prefilled | submitted | held | failed; Retry/Re-queue
  re-enter the queue; Mark submitted closes a prefilled/held plan by hand.
- UI: Apply now per approved plan, Apply all approved, queue position + Remove, screenshots
  on prefilled/held/submitted rows, Mark submitted / Retry / Re-queue / Open form, last-run
  line; Settings card gains the unattended switch and run limits.

