# JobScout — status hand-off

Read this first in a fresh session, then `CLAUDE.md`, then `docs/ARCHITECTURE.md`.
Everything below is committed on `main` (github.com/vikaspalakurthi/jobscout). This file
describes the state of the software, not of anyone's job search.

## What exists and works (as of 2026-10-11)

| Piece | State | Notes |
|---|---|---|
| Fetching: category-1 boards (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable, BambooHR, Breezy) + category-2 (Workday, Oracle HCM, Phenom, iCIMS) + aggregators | stable | ~530 direct boards; full refresh ~11 min (8 parallel board fetches); Eightfold / SuccessFactors / Radancy / Jobvite recognised by `detect.py` but not fetched |
| Keyword scoring (`scoring.py`, `config/skills.yaml`) | stable | Rescore is instant; used as the prefilter for fit scoring |
| Model fit scoring (`fit.py`) | working since 2026-10-03 | Haiku via `claude -p`, batches of 10, gate keyword ≥ 60; thumbs → `resume/fit-profile.md` rewrite by Sonnet; first learned rule moved DB-specialist SRE roles from the 60s to 50 |
| Scheduled refresh | working | 06:00 / 11:00 / 18:00 / 23:00 PC-local while the server runs; Settings → Refresh schedule |
| Discord digest of new jobs | working | Settings → Notifications; webhook in local.yaml |
| Resume tailoring (`tailor.py`, skill `tailor-resume`) | working | analyze → questions → generate; ~35 s + ~2.5 min per job on Fable 5.1; Q&A and long/short cover letters |
| Apply stage 1: plans + review (`apply.py`) | working | Greenhouse/Ashby via APIs, Lever via page parse; eligibility, EEO, education, location resolvers; website/portfolio from the profile |
| Apply stage 2: queue + `/apply-queue` skill | working, unattended verified | ~70 live runs across Greenhouse, Ashby, SmartRecruiters, Dice; Workday / Oracle / iCIMS / amazon.jobs / Phenom end at login walls or missing readers (`held`); lessons consolidated in LEARNINGS → "Filler: lessons" |
| Performance | fixed 2026-10-03 | dual-loopback server (the `localhost` IPv6 2 s floor), cached YAML loads, 300-card pages; `python perf.py` checks it |
| Public repo scaffolding | 2026-10-11 | MIT, README, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, templates/resume, tests + CI (Linux/Windows), Dependabot, stale/greeting bots, Claude review workflow |

## Local state that is NOT in the repo

`data/jobs.db` (jobs, statuses, plans, runs), `resume/` (profile, master resume, skills
inventory, preferences, fit profile, per-job output folders), `config/local.yaml` (keys,
webhook, overrides). A fresh clone starts empty: run a refresh, copy `templates/resume/`.

## Next steps, in order

1. **Make targeting configurable**: home metro / location classes (today Austin + Texas metros
   + US-remote live in `fetchers.py`), role-family presets for `skills.yaml` and the fetch-time
   relevance prefilter (`TITLE_RELEVANT`), and neutral work-authorization wording. This is the
   gate to being useful for other roles and cities.
2. **Docs site**: architecture, quickstart, usage, CLI, terminology (ATS, category 1/2/3,
   keyword vs fit score), features, configuration, upgrades.
3. **Claude review bot**: the maintainer runs `/install-github-app` once to store
   `CLAUDE_CODE_OAUTH_TOKEN`; until then the review jobs skip.
4. **Scheduled apply runs** from the desktop app (`/apply-queue` prefill in the morning,
   unattended at night) - the filler still needs an interactive Claude session with Chrome.
5. **Readers for more apply flows**: Phenom's native wizard (no login on step 1), Dice's
   profile-driven wizard; SuccessFactors and Eightfold fetchers if a clean, public path appears.
6. Smaller planner items from the runs: list each consent checkbox separately; plan-time
   detection of Greenhouse's page-only demographic-consent box; a `force=prefill` option on
   `queue/next`; Ashby EEO survey in the form reader; aggregator re-post dedupe across company
   spellings; make the filled-form screenshot land.

## Rules that must survive the hand-off

- The filler never clicks Submit in prefill mode, never solves captchas, never creates
  accounts or types passwords; unattended submit only on an exact match with the approved plan.
- `resume/`, `data/` and `config/local.yaml` stay out of git. Only `SKILL.md`/scripts of the
  skills are committed. Nothing personal in tracked files (see CLAUDE.md → Public repo).
- The user's server runs on 8765 in their terminal; preview new code on 8766
  (`jobscout-8766` launch entry). Restart their server after Python changes.
- Update ARCHITECTURE / LEARNINGS / CLAUDE.md / about.html with every behaviour change; one
  commit per change (see CLAUDE.md).
