# Make it yours

Everything personal lives in two places, both gitignored: `resume/` (your facts) and
`config/local.yaml` (your settings and keys, written by the Settings page).

## Targeting

| Setting | Where | Effect |
|---|---|---|
| Home metro and state | Settings → Home | Location classes `home` / `home_state` / `remote_us` / `us_other` / `non_us`; the Adzuna sweep locations; the fit gate |
| Role preset | Settings → Role preset | Replaces `config/skills.yaml`: which titles are fetched at all, search terms for keyword-only boards, scoring weights, starting fit rules |
| Location filters | Settings → Location filters | Keep onsite jobs elsewhere in your state / in other US cities / global-remote |
| Fine tuning | `config/skills.yaml` | Role titles and points, blockers, penalties, skill groups, flags - then **Rescore** |

Changes to `relevance` (what gets fetched) apply on the next Refresh; everything else on Rescore.

## Boards

`config/companies.yaml` lists the employers fetched directly. Add one from the Companies page
("Add a company": type the name, or paste the careers URL for Workday / Oracle HCM / Phenom /
iCIMS sites) or by hand:

```bash
python detect.py "Company Name"
python detect.py --url https://careers.example.com "Company Name"
```

Both print the line to paste. Employers with no fetchable board go on the **Watchlist**.

## Your facts (`resume/`)

```bash
cp templates/resume/*.example.* resume/     # then drop ".example" from each name
```

| File | Holds | Used by |
|---|---|---|
| `profile.yaml` | contact, location, work authorization, salary policy, EEO answers, consents | the application filler - every form field comes from here |
| `master-resume.md` | your full, truthful resume | tailoring (the only source of claims) |
| `skills-inventory.md` | every skill with status *have / familiar / dont-have* and real depth | tailoring and fit scoring |
| `preferences.md` | layout and tone rules; location and deal-breakers | tailoring; the fit scorer reads location rules |
| `fit-profile.md` | judgement rules for fit scoring | created from the preset's defaults on first use; learns from your thumbs |

!!! warning "Truth is the feature"
    The tailoring skill never claims anything outside `master-resume.md` and the inventory, and
    the fit scorer judges against the inventory's levels. Overclaiming in these files produces
    resumes you cannot defend and fit scores you cannot trust.

Set `resume.file_name` on the Settings page (e.g. `Jane_Doe.docx`); cover letters are named from
the same stem.

## Keys and notifications

- **Adzuna**: register (free), paste `app_id` / `app_key` on Settings → Adzuna, press Test.
- **Discord**: Settings → Notifications, paste a webhook URL, Send test. New jobs above the
  score bar are posted after each refresh.
- **Schedule**: Settings → Refresh schedule, default 06:00 / 11:00 / 18:00 / 23:00 PC-local.

All of it lands in `config/local.yaml`.
