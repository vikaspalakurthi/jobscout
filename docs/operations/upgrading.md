# Upgrading

JobScout is trunk-based: `main` is always runnable. Upgrading is a pull and a restart.

```bash
git pull
pip install -r requirements.txt      # only changes occasionally
python server.py
```

- **Database migrations run automatically** on start (`db.init()`: idempotent `ALTER TABLE`
  plus any one-off data fixes). Your statuses, notes, plans and runs are never touched by a
  migration.
- **Your files are never touched**: `resume/`, `config/local.yaml`, `data/` are gitignored.
- After upgrading, click **Rescore** once if the release notes mention classifiers or
  `skills.yaml`; **Refresh** if they mention fetchers.

## What a restart does and does not need

| Change | Needs |
|---|---|
| Python files (`server.py`, `fetchers.py`, …) | restart |
| `config/skills.yaml` weights | Rescore (no restart) |
| `config/skills.yaml` → `relevance` | next Refresh (no restart) |
| Settings page (anything) | nothing; read on the next use |
| `config/companies.yaml` by hand | next Refresh |
| `static/*` | reload the page |

## Breaking changes log

| Date | Change | What to do |
|---|---|---|
| 2026-10-12 | Location classes `austin` / `texas` became `home` / `home_state`; `filters.keep_texas_non_austin` became `keep_home_state` | Migration renames stored classes; the old filter key is still honoured. Set Settings → Home. |
| 2026-10-12 | Fetch-time relevance moved from `fetchers.py` into `config/skills.yaml` → `relevance` | If you hand-edited `skills.yaml` before this date, load the `sre-devops` preset once (backup kept) and re-apply your edits, or add a `relevance:` section - without it the built-in SRE rules apply. |
| 2026-10-11 | `resume.file_name` default is `Tailored_Resume.docx`; cover-letter names derive from it | Set your name on the Settings page if it is not in `local.yaml` already. |
| 2026-10-06 | Greenhouse `posted_at` is now `first_published` | Older reqs leave the inbox after the next Refresh (the age limit sees their real age). |
| 2026-10-03 | Server listens on `127.0.0.1` and `::1` | Nothing; `localhost` is fast again on Windows. |

## Downgrading

`git checkout <older commit>` and restart. Columns added by newer migrations are ignored by
older code; data is not lost.
