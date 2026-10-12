# Contributing to JobScout

Thanks for looking. JobScout is small, local-first, and opinionated; contributions that keep
it that way are very welcome.

## Good first contributions
- **A new board fetcher** for an ATS not yet covered, or a fix to an existing one. Read
  `docs/ARCHITECTURE.md` → "Sources: three categories" first: category 1 = documented public
  APIs, category 2 = an employer's own undocumented JSON endpoint (fine), category 3 =
  bot-protected or login-walled (never — this is a hard rule, don't open a PR for it).
- **Classifier fixes** (`fetchers.py`: location, work mode, employment type). Add the failing
  string to the test in `tests/test_smoke.py`.
- **Making the home metro configurable** — today Austin/Texas are wired into `fetchers.py`.
  Open an issue to discuss the shape before writing code.
- **Companies.** Adding a board is config, not code: `config/companies.yaml`. Please include
  the `python detect.py "Name"` output in the PR and watch for slug collisions (a small
  unrelated startup answering on a big company's guessed slug — see `docs/LEARNINGS.md`).

## Ground rules (not negotiable)
- Legitimate APIs only. No scraping of LinkedIn/Indeed, no captcha or bot-protection bypass,
  no login automation, no account creation.
- The application filler never submits a form that differs from the plan the user approved,
  and never fabricates an answer.
- No personal data in the repo: `resume/`, `data/`, `config/local.yaml` are gitignored and
  stay that way. Templates go in `templates/`.
- User statuses (`status`, `viewed_at`, `applied_at`, `notes`) survive every refresh.

## The flow at a glance

```
issue/idea → fork + branch → change + tests → docs in the same PR → pull request
          → CI (pyflakes + tests, Linux & Windows) + review bot → maintainer review → squash-merge
```

The long version with a diagram: https://vikaspalakurthi.github.io/jobscout/contributing/

## Workflow
1. Fork, branch from `main` (`feat/<slug>` or `fix/<slug>`).
2. `pip install -r requirements.txt`, then `python -m unittest discover -s tests` — keep it green.
3. If behaviour changes, update the docs **in the same PR**: `docs/ARCHITECTURE.md` (data flow,
   schema, endpoints), `docs/LEARNINGS.md` (any external-API quirk you hit), and
   `static/about.html` if sources or filtering changed. The repo treats its docs as the source
   of truth; CI will not catch a stale doc, reviewers will.
4. One concern per PR, imperative commit messages. Fill in the PR template.
5. A maintainer (and the automated review bot) will comment. Expect questions about the
   three-category rule and about privacy before anything else.

## Developing
- `python server.py` → http://localhost:8765. A restart is needed after editing Python files.
- `python perf.py` times every endpoint; keep calls under 500 ms.
- Windows is a first-class platform (the author's machine). Watch for PowerShell quoting and
  the `localhost` → IPv6 quirk documented in `docs/LEARNINGS.md`.
- AI-assisted development is how this repo is built; `CLAUDE.md` holds the conventions. If you
  use Claude Code, it will read it. If you don't, the same rules apply to you.

## Reporting a security or privacy concern
See [SECURITY.md](SECURITY.md). Don't open a public issue for anything involving a user's data.
