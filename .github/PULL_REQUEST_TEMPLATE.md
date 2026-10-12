## What this changes

<!-- one paragraph; link the issue if there is one -->

## Checklist

- [ ] `python -m unittest discover -s tests` passes locally
- [ ] Docs updated in this PR where behaviour changed: `docs/ARCHITECTURE.md` (flow / schema / endpoints), `docs/LEARNINGS.md` (any external quirk), `static/about.html` (sources or filtering)
- [ ] No personal data, keys, or `resume/` / `data/` / `config/local.yaml` content in the diff
- [ ] For a new or changed fetcher: it is category 1 (documented API) or 2 (employer's own public JSON) - not a login wall, captcha, or bot-protected page; routed through `_norm()`, `is_relevant()`, `_keep_location()`; source added to `SOURCE_PRIORITY`
- [ ] For `config/companies.yaml` additions: `detect.py` output included and slug collisions checked

## How I tested it

<!-- what you ran, against which boards / data -->
