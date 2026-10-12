# JobScout — project instructions for AI sessions

JobScout is a **local, single-user job aggregator with fit scoring** for its author
(SRE/DevOps/Observability + ELK/Kafka platform-admin roles; Austin TX / Texas hybrid /
US-remote; 10+ yrs experience; work-authorized, no sponsorship needed, so
citizenship/clearance-required jobs are excluded by default). Python + Flask + SQLite +
vanilla-JS frontend. Everything runs and stays on this machine.

## Source of truth — READ THIS FIRST

**The docs in this repo are the source of truth.** Any session (human or AI) that
changes behavior MUST, in the same change:
1. Update `docs/ARCHITECTURE.md` if data flow, schema, endpoints, or scoring changed.
2. Append to `docs/LEARNINGS.md` if an external API quirk or gotcha was discovered.
3. Update this file if conventions or commands changed.
4. Update `static/about.html` (the in-app "How it works" page) if sources/filtering
   changed — the user reads that page, keep it honest.
5. **Commit with git** — one commit per feature/fix, imperative message, and end commit
   messages with the attribution line your environment specifies.

Do NOT rely on chat history or prior-session memory for facts about this codebase —
verify against these docs and the code. If docs and code disagree, the code is right;
fix the docs in the same commit.

**This contract is self-perpetuating.** It binds every session that reads it, including
the obligation to pass it on: when you introduce a new convention, write it into this
file so the next session inherits it; never weaken or remove this section. Knowledge
gained WITHOUT a code change (a probe result, an API quirk, a dead endpoint) still goes
into `docs/LEARNINGS.md` and gets committed — discoveries are deliverables too.

## Commands

```
python server.py                 # dashboard at http://localhost:8765
python refresh.py                # fetch all sources + rescore (CLI; ~5-7 min)
python detect.py "Company Name"  # probe which ATS a company uses by slug guessing; prints config line
python detect.py --url <careers page> "Company"   # fingerprint the page instead (Workday/Oracle HCM/Phenom/iCIMS/...)
python discover.py --from-adzuna --names list.txt   # bulk-probe many names (Adzuna employers + a file); prints YAML to review
python discover.py --names list.txt --fingerprint     # + company-domain lookup and careers-page fingerprinting (category 2)
python tailor.py run <job_id>    # tailor a resume for one job via the skill (CLI test path)
python tailor.py answer <id> "Q" # application-form answers; `cover <id>` = cover letter
python apply.py plan <job_id>    # build/inspect an application plan (apply pipeline, stage 1)
python notify.py test            # post a sample new-jobs digest to the configured Discord webhook
python fit.py score <id> [...]   # model fit score for jobs (synchronous); `gate` counts candidates, `profile` folds feedback in
python perf.py [port]            # time every UI endpoint on localhost + 127.0.0.1; run when the app feels slow
```

- In Claude Code, prefer starting the server via the browser preview using the
  `jobscout` entry in `.claude/launch.json` (port 8765). If the port is busy, the user
  often runs `python server.py` in their own terminal — don't kill their process without
  telling them; use the `jobscout-8766` launch entry (sets `JOBSCOUT_PORT`) to preview
  new code beside it. A restart IS required after editing `server.py`/`fetchers.py`/etc.
- **Rescore vs Refresh:** editing `config/skills.yaml` only needs a Rescore (button in
  UI, or `scoring.rescore_all`) — instant, no network. New/changed fetchers need a full
  refresh.

## Layout

- `fetchers.py` — all job sources + location/work-mode/employment classifiers. See
  `docs/ARCHITECTURE.md` for the source list and normalized-job shape.
- `scoring.py` — keyword scoring engine, driven entirely by `config/skills.yaml`. It is the
  cheap prefilter; `fit.py` is the model score the UI sorts on (batches of gated jobs through
  `claude -p` + the `resume/` fact files; thumbs on cards teach `resume/fit-profile.md`). See
  ARCHITECTURE → "Fit scoring" before changing either.
- `db.py` — SQLite (`data/jobs.db`, gitignored). Migrations = idempotent `ALTER TABLE`
  in `db.init()` (try/except OperationalError). Follow that pattern for new columns.
- `server.py` — Flask API + static pages. `refresh.py` — CLI refresh. `detect.py` — ATS prober
  (`--url` fingerprints a careers page; see ARCHITECTURE → "Sources: three categories" before
  adding any fetcher: category 2 = undocumented JSON endpoints, category 3 = bot-protected, never).
- `notify.py` — post-refresh Discord digest of newly inserted jobs above `notify.min_score`
  (webhook URL in gitignored `config/local.yaml`). `db.upsert_jobs` returns `(count, new_ids)`;
  the ids are kept in `meta.last_refresh._summary.new_ids` and back the "New this refresh" filter.
- `apply.py` — apply pipeline: stage 1 (form readers for Greenhouse/Ashby/Lever, field
  resolver from `resume/profile.yaml` (gitignored), review/approve) and stage 2 queue
  (`enqueue`/`next_in_queue`/`record_result`). Decoupled from tailor.py by design: shares
  only the job folder + `resume_state`; server.py bridges the two where needed.
- `.claude/skills/apply-queue/` — the filler: a Claude session with the Chrome extension
  drains the queue in the user's Chrome (prefill leaves tabs for the user's Submit;
  unattended submits only on an exact form match). Never captchas, never logins.
- `tailor.py` — resume tailoring pipeline (worker pool + `claude -p` runner);
  `inventory.py` — the only writer of `resume/skills-inventory.md` (lock + backup). See
  `docs/ARCHITECTURE.md` → Resume tailoring for states, folder layout, concurrency and
  the decoupling rule for the future apply pipeline.
- `config/settings.yaml` — defaults; `config/local.yaml` (gitignored) — user overrides
  incl. the **Adzuna API key**; written by the in-app Settings page. Never commit keys.
- `discover.py` — bulk board discovery (thread pool over `detect.detect_company`); output is
  reviewed and pasted into companies.yaml by hand, never appended blindly (slug collisions).
- `config/companies.yaml` — direct-board company list. `config/skills.yaml` — all
  scoring weights (user-tunable; treat as config, not code). `config/watchlist.yaml` —
  companies with NO public API (rendered at /watchlist for manual checking).
- `static/` — `index.html` (jobs UI), `companies.html`, `settings.html`, `about.html`
  ("How it works"), `style.css` (shared). Vanilla JS, no build step, no frameworks.
- `.claude/skills/tailor-resume/` — Claude Code skill that tailors a resume to a job
  (phases: analyze → JSON questions; generate → DOCX via `scripts/build_docx.js`, Node
  `docx` lib — run `npm install` once in the skill folder). Its personal fact files live in
  **`resume/`** at the repo root (`master-resume.md`, `skills-inventory.md`,
  `preferences.md`) — **gitignored** (contact info + career details; repo is meant for
  GitHub; never commit them), and outside `.claude/` because headless `claude -p` cannot
  write there. The skill's content is owned by the user's Claude "Resume Builder" project:
  when a new version is dropped in, keep the user's wording (incl. the 2-page rules) and
  only re-apply the path/privacy edits. Not yet wired into the JobScout UI (planned:
  `claude -p` runner + questions page).

## Hard rules & conventions

- **Legit APIs only.** Never scrape LinkedIn/Indeed, never bypass bot protection or auth
  (Tesla 403s, Apple's API 401s, Microsoft's is dead — those stay Adzuna-only). This has
  been decided repeatedly; don't relitigate.
- **User statuses are sacred:** `status` (new/starred/saved/applied/not_interested/
  hidden), `viewed_at`, `applied_at`, `notes` must survive refreshes — upserts update
  job fields only, never status fields. Every status action in the UI is a toggle.
- Jobs are never deleted, only hidden; dedupe collapses by `fingerprint` at read time
  preferring direct sources (`SOURCE_PRIORITY` in server.py).
- Location policy: keep Austin/Texas-metro/US-remote; drop non-US, global-remote and
  non-TX onsite (all toggleable in Settings → stored in filters config).
- Work-auth policy: `citizen_only` jobs are excluded from inbox by default (the profile's
  `needs_sponsorship: false` drives sponsorship answers).
- Frontend texts/labels: keep the no-sponsorship framing ("no sponsorship" is OK, not a rejection).
- After changing classifiers or skills.yaml, run a rescore and sanity-check the top-20
  list before declaring done. When adding a fetcher: route through `_norm()`,
  `is_relevant()`, `_keep_location()`; add the source to `SOURCE_PRIORITY` (server.py);
  facets/filters pick it up automatically.
- Windows machine: PowerShell quirks — write multi-line Python to a script file instead
  of `python -c` one-liners with quotes. Git Bash has no `$TMPDIR` (a path built from it
  lands in `C:/Program Files/Git/`); put temp files in the session scratchpad directory.
  **Never patch regexes through a bash heredoc**: `` inside a Python string literal fed via
  `python - <<EOF` became a literal backspace byte in fetchers.py once (2026-09-27, cost a
  broken NON_US rule and two commits). Write the patch to a `.py` file and run it, or use Edit.

## Public repo conventions (since 2026-10-11, MIT-licensed, github.com/vikaspalakurthi/jobscout)

- **Nothing personal in tracked files.** Candidate facts live only in gitignored `resume/`;
  tracked code and skills say "the candidate" / "the user" and read names from `resume/`
  (`resume.file_name` in local.yaml names the DOCX; cover-letter files derive from its stem).
  Examples go in `templates/resume/*.example.*` — update them when `profile.yaml` gains a key.
- `tests/test_smoke.py` (stdlib unittest, offline, temp DB) runs in CI on Linux + Windows with
  `pyflakes`; keep both green. Add a case when you fix a classifier.
- Community files: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `.github/`
  (issue/PR templates, CODEOWNERS, Dependabot, stale + greeting bots, Claude review bot).
- `docs/LEARNINGS.md` and `docs/STATUS.md` are public: record board quirks and run outcomes,
  not application details that identify the user's situation beyond what `README.md` says.

## Branching strategy

**Trunk-based.** `main` is the only long-lived branch and must always be in a working
state (server starts, refresh runs). The normal flow is: small, verified, single-purpose
commits directly to `main` — this fits a single-user local app with no CI/review gate.
Use a short-lived branch (usually via a session worktree) ONLY when:
- another session may be active at the same time (see Parallel sessions), or
- a change is large/risky enough that `main` would be broken between commits
  (e.g., a schema rework or a server rewrite).
Branch naming: `feat/<slug>` or `fix/<slug>`; merge back to `main` promptly (fast-forward
or a regular merge — no rebasing published history) and delete the branch. Never leave
work stranded on a branch at the end of a session; if it isn't merge-ready, commit it,
note the state in the final message to the user, and say the branch name.
Remote: github.com/vikaspalakurthi/jobscout (origin). Push `main` after each verified commit.

## Parallel sessions

Multiple Claude Code sessions may work on this project simultaneously. Rules:
- **Prefer a git worktree** for any session doing code changes when another session may
  be active (the desktop app offers worktree isolation); merge to `main` when done.
- Shared singletons — coordinate, don't collide: **port 8765** (one server; don't kill a
  server you didn't start without saying so), **data/jobs.db** (fine for concurrent
  reads/status-writes; don't run two refreshes at once), **config/*.yaml** (append-only
  edits reduce conflicts).
- Safe parallel splits: scoring/skills.yaml tuning vs a new fetcher vs a docs task vs a
  standalone page. Risky: two sessions editing server.py or index.html.
- Every session must follow the source-of-truth contract above; commits are the
  hand-off mechanism between sessions.

## Where deeper knowledge lives

- `docs/STATUS.md` — **hand-off: what works, current data state, next steps.** Read first
  in a new session; update it at the end of every session.
- `docs/ARCHITECTURE.md` — data flow, DB schema, scoring pipeline, API endpoints.
- `docs/LEARNINGS.md` — external-API gotchas (Workday facets, Adzuna quirks, EPPA
  false-positive, bare-country locations…). **Check before touching any fetcher.**
- `README.md` — user-facing quickstart.
- User-level context (profile, preferences) also lives in Claude's auto-memory, but the
  repo docs must stand alone — assume a fresh session has ONLY this repo.
