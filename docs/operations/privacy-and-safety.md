# Privacy and safety

JobScout handles the most sensitive data a person has about their career. These are the
guarantees, where they are enforced, and what they do not cover.

## Where your data is

| Data | Location | In git? | Leaves the machine? |
|---|---|---|---|
| Postings, statuses, plans, runs | `data/jobs.db` | no | no |
| Profile, master resume, skills inventory, preferences, fit profile, tailored output | `resume/` | no | only inside prompts to Claude via your own subscription (`claude -p`) |
| API keys, Discord webhook, settings | `config/local.yaml` | no | Adzuna key to Adzuna; webhook URL to Discord |
| Board list, scoring profile, presets | `config/*.yaml` | yes | no |

The repository's `.gitignore` excludes the first three. The CI and the review bot check PRs
for personal data; the maintainers reject any that carries it.

## Outbound connections

1. The configured job sources (ATS APIs, employer careers endpoints, aggregators).
2. Discord, only if you set a webhook, only the digest.
3. `claude -p` through your Claude subscription: fit-scoring prompts (rules, inventory,
   preferences, job excerpts), tailoring prompts, profile rewrites.

Nothing else. The server binds `127.0.0.1` and `::1` only; there is no telemetry.

## The filler's rules

Enforced in `.claude/skills/apply-queue/SKILL.md` and `apply.py`, not by settings:

- Fills **only** values from the approved plan; reads every field back before moving on.
- **Prefill** never clicks Submit. **Unattended** submits only when the live form matches the
  plan exactly; any extra required field, changed option set, captcha or login wall → `held`.
- Never solves a captcha, never logs in, never creates an account, never types a password,
  never guesses an answer. A plan value that is not one of the form's options is a bug and is
  left for you.
- Runs in your Chrome with your sessions through the Claude in Chrome extension, which grants
  access per interactive session - it cannot run headlessly or in the background.
- Marking a job applied removes its plan from the queue, so nothing is submitted twice.

## Legitimate sources only

No scraping of LinkedIn or Indeed, no bypassing bot protection or logins - in fetching or in
applying. Employers behind those walls are reached through aggregators or listed on the
Watchlist. This is a project rule, repeated in CONTRIBUTING and in the review bot's prompt.

## Truthfulness

The tailoring skill claims only what `master-resume.md` and `skills-inventory.md` support;
*familiar* appears as familiarity, *dont-have* never appears. The fit scorer judges against
the inventory's levels. Resolvers never fabricate: unresolved fields are shown, referral
questions never resolve to your own name, EEO answers match the form's option wording or are
declined.

## What this does not protect you from

- Employers' own data handling once you submit.
- Mistakes in your `profile.yaml` - every value there can end up on a form.
- Anyone with access to your machine. There is no encryption at rest beyond what your OS does.

## Reporting

Private reports via GitHub's "Report a vulnerability" or the maintainer's email
(see [SECURITY.md](https://github.com/vikaspalakurthi/jobscout/blob/main/SECURITY.md)).
Never post personal data in a public issue.
