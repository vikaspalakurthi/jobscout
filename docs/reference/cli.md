# Command line

All commands run from the repo root with the same Python environment as the server.

| Command | What it does |
|---|---|
| `python server.py` | the dashboard at <http://localhost:8765> (scheduler, fit workers and tailoring workers run inside it) |
| `python refresh.py` | fetch every source and rescore from the CLI; same code path as the Refresh button, prints progress |
| `python detect.py "Company"` | probe every ATS slug pattern for a company; prints the `companies.yaml` line |
| `python detect.py --url <careers page> "Company"` | fingerprint a careers page (Workday / Oracle HCM / Phenom / iCIMS / …) and print the line |
| `python discover.py --from-adzuna --names list.txt` | bulk-probe many names (Adzuna employers plus a file); prints YAML to review by hand |
| `python discover.py --names list.txt --fingerprint` | the same with company-domain lookup and careers-page fingerprinting |
| `python tailor.py run <job_id>` | tailor a resume for one job (CLI test path) |
| `python tailor.py answer <job_id> "Question"` | draft an application-form answer; `cover <job_id>` builds the cover letters |
| `python apply.py plan <job_id>` | build and print an application plan |
| `python fit.py score <id> [<id> …]` | fit-score jobs synchronously and print the results |
| `python fit.py gate` | count the jobs that currently pass the fit gate |
| `python fit.py profile` | fold pending feedback into `resume/fit-profile.md` now |
| `python fit.py status` | queue and profile state as JSON |
| `python notify.py test` | post a sample digest to the configured Discord webhook |
| `python perf.py [port]` | time every UI endpoint on `localhost` and `127.0.0.1`; exits 1 over 500 ms |
| `python -m unittest discover -s tests` | the offline smoke tests |
| `python -m pyflakes .` | the lint CI runs |
| `mkdocs serve` | this documentation locally (`pip install -r requirements-docs.txt`) |

## Environment variables

| Variable | Effect |
|---|---|
| `JOBSCOUT_PORT` | run a second instance on another port; it never schedules refreshes |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | alternative to the keys in `config/local.yaml` |

## Exit codes

`perf.py` and the tests return non-zero on failure; the other commands print and return 0
unless they crash.
