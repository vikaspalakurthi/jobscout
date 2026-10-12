# JobScout

**A local, single-user job-search engine: aggregate → score → tailor → apply, all on your machine.**

JobScout pulls postings from ~530 company career boards and a few aggregators, scores each one
twice (a fast keyword score, then a model-judged *fit* score that knows the difference between
"SRE who runs Kafka" and "Kafka internals expert"), tailors a two-page ATS-safe resume per job,
builds the application plan from your profile, and fills the employer's form in your own
Chrome for you to review or submit. It learns from your thumbs-up/down and from the answers you
give while tailoring.

It is built around one person's search (SRE / DevOps / observability roles, Austin TX or
US-remote) and is honest about that — see [Who this is for](#who-this-is-for-today).

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green)

---

## What it does

| Stage | What happens | Where |
|---|---|---|
| **Find** | Fetches from direct ATS boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Oracle HCM, Phenom, iCIMS, Recruitee, Workable, BambooHR, Breezy) plus Adzuna, Remotive, RemoteOK, The Muse, Hacker News. Boards fetch 8 at a time with a live progress strip. Runs on demand or on a clock (06:00 / 11:00 / 18:00 / 23:00 by default). | `fetchers.py`, `config/companies.yaml` |
| **Score** | Keyword score from `config/skills.yaml` (instant prefilter) and a **fit** score 0–100 with a one-line reason and the gaps, from a cheap Claude model through your Claude subscription (`claude -p`, no API key). | `scoring.py`, `fit.py` |
| **Learn** | Thumbs on fit scores accumulate; after five, a stronger model rewrites your judgement rules (`resume/fit-profile.md`) and affected jobs re-score. Answers you give while tailoring update your skills inventory. | `fit.py`, `inventory.py` |
| **Tailor** | Two-phase Claude Code skill: *analyze* (asks only about requirements your inventory can't answer) → *generate* (two-page DOCX, cover letter long + short, Q&A answers). Worker pool, per-job folders. | `tailor.py`, `.claude/skills/tailor-resume/` |
| **Apply** | Reads the employer's form (Greenhouse / Ashby / Lever), resolves every field from `resume/profile.yaml`, shows you the plan, then a Claude session with the Chrome extension fills it in your Chrome — *prefill* leaves the tab for your Submit; *unattended* submits only when the live form matches the approved plan exactly. Never captchas, never logins, never account creation. | `apply.py`, `.claude/skills/apply-queue/` |
| **Alert** | "New this refresh" filter and an optional Discord digest of new jobs above your score bar. | `notify.py` |

Everything stays on your machine: SQLite database, your resume files, your keys. The only
outbound calls are to the job boards and to Claude through your own subscription.

### Hard rules
- **Legitimate APIs only.** No LinkedIn or Indeed scraping, no bot-protection or login bypass.
  Employers that block are listed on a manual Watchlist page instead.
- **The filler never solves a captcha, never creates an account, never types a password.**
- **Your statuses are sacred.** Star / applied / not-interested / hidden survive every refresh.
- **No fabricated answers.** Form fields the profile can't resolve are shown to you, not guessed.

---

## Quick start

### Prerequisites
- Python 3.11+ and `pip`
- [Claude Code](https://claude.com/claude-code) with a Claude subscription — used headlessly
  (`claude -p`) for fit scoring and resume tailoring. Not needed for find/score-by-keyword.
- Node.js (for the DOCX builder) — only if you use resume tailoring
- Google Chrome + the Claude in Chrome extension — only if you use the application filler
- Optional: a free [Adzuna](https://developer.adzuna.com) key (adds aggregator coverage)

### 1. Install and run
```bash
git clone https://github.com/vikaspalakurthi/jobscout.git
cd jobscout
pip install -r requirements.txt
python server.py            # http://localhost:8765
```
Click **Refresh**. The first full refresh of ~530 boards takes 10–15 minutes; the Jobs page shows
progress. You now have a scored, filterable inbox — that part needs nothing else.

### 2. Make it yours (10 minutes)
1. **Targeting.** Settings → **Home** (your city/state) and **Role preset** (SRE/DevOps or
   QA/SDET today). Fine-tune `config/skills.yaml` — titles, skill weights, location points,
   flags — then **Rescore** (instant). Relevance/search-term changes apply on the next Refresh.
2. **Boards.** `config/companies.yaml` lists the companies fetched directly. Add one from the
   Companies page ("Add a company" probes which ATS it uses), or by hand:
   `python detect.py "Company Name"` / `python detect.py --url <careers page> "Company"`.
3. **Settings page.** Adzuna key, location policy, refresh schedule, notifications, fit-scoring
   model and gate. Saved to `config/local.yaml` (gitignored).

### 3. Enable tailoring, fit scoring and the filler
Copy the templates and fill them in — these files are **gitignored** and never leave your machine
except through your own Claude subscription:
```bash
cp templates/resume/*.example.* resume/      # then rename: drop ".example"
```
| File | What it holds | Used by |
|---|---|---|
| `resume/profile.yaml` | Contact, location, work authorization, salary policy, EEO answers, consents | apply (form filling) |
| `resume/master-resume.md` | Your full, truthful resume in Markdown | tailor |
| `resume/skills-inventory.md` | Every skill with status have / familiar / dont-have and real depth | tailor, fit |
| `resume/preferences.md` | Layout and tone rules for generated documents | tailor |
| `resume/fit-profile.md` | Judgement rules for fit scoring — created automatically, learns from your thumbs | fit |

Then: `cd .claude/skills/tailor-resume && npm install` (DOCX builder), and on the Settings page
press *Test* under Resume tailoring to confirm `claude` is reachable.

For the filler: open a Claude Code session with the Chrome extension connected and type
`/apply-queue`. It drains the queue of plans you approved on the Resumes tab.

---

## Pages
- **Jobs** — the inbox: fit/keyword scores, reasons and gaps, filters (location, work mode, role,
  source, posted/updated age, "new this refresh"), presets, bulk select, tailor and fit-score
  buttons, thumbs for feedback, status toggles. The **Resumes** tab holds the tailoring queue,
  questions, finished resumes, application plans and the apply queue.
- **Companies** — every company with cumulative counts, board config, last-fetch status, per-board
  refresh, and "Add a company" (ATS detection by slug or careers URL).
- **Watchlist** — employers with no fetchable board, with links for manual checks.
- **Settings** — everything above, plus Discord, refresh schedule, fit scoring, apply policy.
- **How it works** — the in-app explanation, kept current with the code.

## Command line
```
python refresh.py                 # fetch all sources + rescore
python detect.py "Company"        # which ATS? prints the companies.yaml line
python discover.py --names list.txt --fingerprint   # bulk board discovery (review the output by hand)
python tailor.py run <job_id>     # tailor one job
python apply.py plan <job_id>     # build/inspect an application plan
python fit.py score <id> [...]    # fit-score jobs synchronously; `fit.py profile` folds feedback in
python notify.py test             # Discord test post
python perf.py                    # endpoint timings; run when the UI feels slow
```

## Who this is for
Anyone running a focused search. Two settings make it yours:
- **Home** (Settings → Home): your metro and state. Location classes (`home`, `home_state`,
  `remote_us`, …) and the Adzuna sweeps follow it.
- **Role preset** (Settings → Role preset): a complete scoring + fetch profile. Shipped:
  *SRE / DevOps / Platform* and *QA / SDET / Test automation*; more are one YAML file each
  (`config/presets/`) — see CONTRIBUTING. `config/skills.yaml` stays editable for anything finer.

Work-authorization answers and the citizenship/clearance exclusion come from your
`resume/profile.yaml`; the two Claude Code skills read the candidate's name and facts from
`resume/`, so they work for anyone once those files exist.

## Documentation
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — data flow, schema, scoring, every endpoint,
  the three categories of employer (documented APIs / undocumented JSON / never)
- [`docs/LEARNINGS.md`](docs/LEARNINGS.md) — the gotchas: board quirks, classifier traps, filler
  behaviour on real forms
- [`docs/STATUS.md`](docs/STATUS.md) — current state and next steps
- [`CLAUDE.md`](CLAUDE.md) — conventions for AI coding sessions (the repo is built with them)

## Contributing
Issues and PRs welcome — new board fetchers, classifier fixes, and the "make the home metro
configurable" work are the best entry points. See [CONTRIBUTING.md](CONTRIBUTING.md); the
[Code of Conduct](CODE_OF_CONDUCT.md) applies. Security concerns: [SECURITY.md](SECURITY.md).

## License
[MIT](LICENSE).
