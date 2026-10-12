# Configuration

Three files, one precedence rule: **`config/local.yaml` overrides `config/settings.yaml`**,
key by key. The Settings page writes `local.yaml`; never edit `settings.yaml` for personal
values. `config/skills.yaml` is the active scoring profile (see [Role presets](presets.md)).

Environment: `JOBSCOUT_PORT` runs a second instance (no scheduler); `ADZUNA_APP_ID` /
`ADZUNA_APP_KEY` can replace the keys in `local.yaml`; `MAX_THINKING_TOKENS` is set by
`fit.py` per call.

## `home`

| Key | Default | Meaning |
|---|---|---|
| `city` | Austin | your metro; postings matching `city_terms` → class `home` |
| `city_terms` | `[austin, travis county]` | lowercase substrings that mean the metro |
| `state`, `state_code` | Texas, TX | state name and code → class `home_state` |
| `state_terms` | DFW/Houston/SA metro cities | other metros in the state → `home_state` |

## `filters`

| Key | Default | Meaning |
|---|---|---|
| `max_age_days` | 45 | drop aggregator postings older than this (when the age is known) |
| `max_age_days_boards` | 60 | same for direct boards; also hides older rows from the Inbox |
| `keep_home_state` | true | keep onsite jobs elsewhere in your state |
| `keep_us_other_cities` | false | keep onsite jobs in other US cities |
| `keep_remote_global` | false | keep remote jobs without a clear US signal |

## `refresh`

| Key | Default | Meaning |
|---|---|---|
| `at` | `"06:00, 11:00, 18:00, 23:00"` | clock times (PC-local); `""` = off |
| `every_hours` | 0 | interval fallback when `at` is empty |
| `board_workers` | 8 | parallel board fetches |

## `adzuna`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true | skip Adzuna entirely when false |
| `app_id`, `app_key` | "" | from developer.adzuna.com (free) |
| `country` | us | |
| `queries` | 12 role queries | each costs (locations + 1) calls per refresh |
| `locations` | home city + state metros | sweep locations |
| `distance_km` | 60 | |
| `companies`, `company_sweep_titles`, `company_sweep_days` | 12 category-3 employers | per-company title sweeps |
| `max_days_old`, `results_per_page`, `pages_per_query` | 30, 50, 1 | keep `pages_per_query` low on the free tier |

## `fit`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true | score gated new jobs after every refresh |
| `model` | claude-haiku-4-5 | the scorer |
| `profile_model` | claude-sonnet-5 | rewrites `fit-profile.md` from feedback |
| `thinking` | full | full / small / off (~90 / 45 / 30 s per batch of 10) |
| `batch` | 10 | jobs per call |
| `parallel` | 2 | concurrent calls |
| `gate_min_score` | 60 | keyword score a job needs before the model looks at it |
| `max_per_run` | 300 | cap per start / refresh |
| `auto_profile_after` | 5 | pending feedback items that trigger a profile update (0 = manual) |
| `timeout_min` | 8 | per call |
| `description_chars` | 1800 | description excerpt sent per job |

## `resume`

| Key | Default | Meaning |
|---|---|---|
| `output_dir` | resume/output | `<Company>/<Role-slug>/<jobId>/` |
| `file_name` | Tailored_Resume.docx | your resume file name; cover letters use the same stem |
| `claude_cmd` | claude | or a full path |
| `model` | claude-fable-5-1 | both phases |
| `timeout_min` | 10 | per phase |
| `batch_max` | 50 | jobs per Tailor click |
| `parallel` | 2 | concurrent runs (1-3) |

## `apply`

| Key | Default | Meaning |
|---|---|---|
| `auto_plan` | false | plan automatically when a resume becomes ready |
| `mode` | prefill | default per plan; `unattended` submits approved exact matches |
| `unattended_enabled` | true | master switch |
| `allowed_ats` | greenhouse, ashby, lever | eligible for unattended (Lever is captcha-locked to prefill anyway) |
| `profile` | resume/profile.yaml | |
| `max_per_run` | 5 | forms per `/apply-queue` run |
| `pause_seconds` | 20 | between forms |
| `window` | 06:00-06:30 | prefill window used by a scheduled task |

## `notify`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | false | |
| `discord_webhook_url` | "" | local.yaml only |
| `min_score` | 50 | |
| `max_items` | 15 | lines per message |
| `loc_classes` | home, home_state, remote_us | |

## `server`

`port: 8765`. The server binds `127.0.0.1` and `::1`.

## `config/skills.yaml`

Sections: `preset`, `relevance` (`title_terms`, `desc_rescue_groups`, `min_groups`,
`search_terms`), `view`, `role_titles`, `title_blockers`, `title_penalties`, `seniority_bonus`,
`skills` (`cap_points`, `groups`), `freshness_bonus`, `source_points`, `location_points`,
`experience_bonus`, `flags`, `fit_profile_default`. Details in [Scoring and fit](../concepts/scoring.md).

## `config/companies.yaml`, `watchlist.yaml`

`companies: [{name, ats, slug}]` and `companies: [{name, careers, platform, austin}]` +
`boards:` (manual search links). Both are appended by the Companies page.
