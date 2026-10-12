# Changelog

All notable changes to JobScout are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semver](https://semver.org) and are tags on `main` (`vX.Y.Z`). Breaking changes also get a
row in the docs site → Operations → Upgrading.

## [Unreleased]

## [0.1.0] - 2026-10-12

First public release.

### Added
- Fetchers for Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Oracle HCM, Phenom, iCIMS,
  Recruitee, Workable, BambooHR, Breezy, Amazon, Oracle, plus Adzuna, Remotive, RemoteOK, The
  Muse and Hacker News; ~530 boards in `config/companies.yaml`; `detect.py` / `discover.py`
  to find which ATS a company uses.
- Keyword scoring from `config/skills.yaml` and model **fit** scoring (0-100 with reason and
  gaps) through the user's own Claude subscription, with a feedback loop that rewrites
  `resume/fit-profile.md`.
- Resume tailoring (two-page DOCX, cover letters, form answers) via the `tailor-resume` skill;
  application plans for Greenhouse / Ashby / Lever and the `apply-queue` filler (prefill or
  exact-match unattended submit, never captchas or logins).
- Settings → **Home** (metro/state drive location classes and Adzuna sweeps) and **Role
  presets** (`sre-devops`, `qa-sdet`); `posted` vs `updated` dates with sort and age filters.
- Refresh on a clock (default 06:00 / 11:00 / 18:00 / 23:00) with a live progress strip;
  Discord digest of new jobs; Companies, Watchlist, Settings and How-it-works pages.
- Public-repo scaffolding: MIT license, CONTRIBUTING / CODE_OF_CONDUCT / SECURITY, issue and
  PR templates, CI on Linux + Windows, Dependabot, stale/greeting bots, Claude review
  workflow, MkDocs Material docs site on GitHub Pages, `VERSION` + this changelog, tagged
  releases via `.github/workflows/release.yml`.

[Unreleased]: https://github.com/vikaspalakurthi/jobscout/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vikaspalakurthi/jobscout/releases/tag/v0.1.0
