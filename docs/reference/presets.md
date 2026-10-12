# Role presets

A preset is a complete `config/skills.yaml` for one role family. Loading one (Settings → Role
preset, or `POST /api/presets/load`) copies it over `skills.yaml`, keeps a timestamped backup
beside it, and rescores. Fetch-time rules apply from the next Refresh.

## Shipped presets

| Id | Name | Covers |
|---|---|---|
| `sre-devops` | SRE / DevOps / Platform | site reliability, DevOps, platform, infrastructure, observability, Kafka/Elastic platform administration |
| `qa-sdet` | QA / SDET / Test automation | SDET, test automation, quality engineering, performance/release testing |

## Anatomy

```yaml
preset:                 # shown in the Settings dropdown
  name: QA / SDET / Test automation
  description: ...

relevance:              # what is fetched at all (regexes, case-insensitive)
  title_terms: [ '\bqa\b|quality assurance|\bsdet\b', ... ]
  desc_rescue_groups: [ 'selenium|playwright|cypress', ... ]
  min_groups: 3         # description groups needed to rescue a non-matching title
  search_terms: [qa engineer, sdet, ...]   # queries for keyword-only boards (Workday, iCIMS, Phenom, Oracle HCM, amazon.jobs)

view:                   # the Jobs page's one-click view
  label: QA view
  roles: [SDET, QA, Automation]
  days: 30

role_titles: ...        # scoring, exactly as in skills.yaml
title_blockers: ...
title_penalties: ...
seniority_bonus: ...
skills: ...
freshness_bonus: ...
source_points: ...
location_points: ...    # keys: home, home_state, remote_us, remote_global, us_other, non_us, unknown
experience_bonus: ...
flags: ...

fit_profile_default: |  # seed for resume/fit-profile.md on first use; never overwrites an existing one
  # Fit profile ...
```

## Writing a new preset

1. Copy the closest shipped preset to `config/presets/<id>.yaml` (lowercase, hyphens).
2. Rewrite `relevance.title_terms` first - it decides what exists in the inbox at all. Keep it
   broad; blockers and penalties can trim later, but a posting the prefilter rejected is gone.
3. `search_terms`: 5-9 short queries. Each costs one request per keyword-only board per
   refresh.
4. `role_titles` labels become the Roles filter; name them the way you'd say them.
5. `fit_profile_default`: describe core / adjacent / specialist / product bands for the family
   in the candidate's voice. This is what the fit scorer reads until feedback rewrites it.
6. Run `python -m unittest discover -s tests` - add a case like `test_qa_preset_relevance`
   asserting a canonical title is relevant and an off-family title is not.
7. Open a PR; the preset is the whole change. Adzuna `queries` (Settings → Adzuna) are
   separate and worth updating in the same PR's description.

Nothing in a preset may reference a person, a city or a work-authorization status - those
live in `resume/` and Settings → Home.
