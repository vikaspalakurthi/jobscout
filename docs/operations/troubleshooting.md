# Troubleshooting

First reflex for anything slow or odd:

```bash
python perf.py
```

It times every endpoint on `localhost` and `127.0.0.1` and flags anything over 500 ms.

## Every click takes seconds

| Symptom | Cause | Fix |
|---|---|---|
| `localhost` ~2 s per call, `127.0.0.1` fast | Windows resolves `localhost` to IPv6 first; a server bound to IPv4 only makes the browser wait for the fallback | fixed in `server.serve()` (binds both); if you run an older version, open `http://127.0.0.1:8765` |
| `/api/resume/status` slow | YAML re-parsed on every call | fixed (mtime-cached loads); upgrade |
| Everything slow during a refresh | the 8 fetch threads compete with the web server for the GIL | expected for ~11 minutes; lower `refresh.board_workers` if it bothers you |
| The Companies page freezes while typing | 1,500 rows re-rendered per keystroke | fixed (debounced, 300-row pages); upgrade |

## Refresh

| Symptom | Cause / fix |
|---|---|
| Progress counter stuck at the same number for minutes | one slow board (Workday/iCIMS make several requests with 30 s timeouts); the counter now advances in completion order, so this means a *single* board is still running - wait |
| "Refresh already running" | one at a time; wait for the strip to finish |
| A board shows **failed** on the Companies page | hover the badge for the error; `python detect.py --url` the careers page - slugs change when employers migrate ATS |
| Adzuna HTTP 429 | free tier limit; trim `adzuna.queries` or `companies`, or switch Adzuna off |
| Jobs disappeared after a refresh | the direct-board age limit (`filters.max_age_days_boards`, 60 days) hides old reqs - and Greenhouse dates are now first-published dates. They are still in **All**. |

## Scoring and fit

| Symptom | Cause / fix |
|---|---|
| Fit scores never appear | Settings → Fit scoring on? `claude` on PATH (Settings → Resume tailoring → Test)? Jobs below the gate (keyword ≥ 60) are never sent - lower the gate or tick and press **Fit-score** |
| "model skipped this job" | the batch reply omitted an id; it is retried on the next Fit-score run |
| Fit scores look stale (`*`) | scored under an older profile version; the next run re-scores them |
| A whole role family scores wrong | fix the rule, not the job: thumbs with a note, or edit `resume/fit-profile.md` by hand |
| Keyword scores all 0 after loading a preset | the preset's `role_titles` do not match your titles - expected when switching families; Refresh to fetch the new family |

## Tailoring

| Symptom | Cause / fix |
|---|---|
| "claude not found" | install Claude Code or set `resume.claude_cmd` to the full path |
| `claude exited 1` / non-JSON output | run `claude -p "reply with just: ok" --output-format json` in a terminal; log in if asked |
| Stuck in *analyzing* after a restart | runs cut off by a restart are marked failed on start; press Retry |
| DOCX build fails | `cd .claude/skills/tailor-resume && npm install` |

## Applying

| Symptom | Cause / fix |
|---|---|
| Plan "needs input" | a field the profile cannot answer; type it on the review card |
| Run **held** | by rule: login wall, captcha, an unplanned required field, or a plan value that is not one of the form's options. The reason is on the card; prefill what you can by hand |
| Filler says "requires permission" | the Chrome extension grants access per interactive session; run `/apply-queue` from an interactive Claude Code session, not headlessly |
| Clicks in Chrome do nothing | the Chrome window is minimized or covered - restore it (hidden tabs do not render React updates) |
| Same job applied twice | fixed: marking a job applied skips its queued plan, and the queue refuses applied jobs. Upgrade |

## Windows notes

Write multi-line scripts to files instead of quoting them on the PowerShell command line;
Git Bash has no `$TMPDIR`; the desktop app's preview server uses port 8766 beside your 8765.

## Still stuck

Open an issue with the output of `python perf.py`, the server log line, and the commit
(`git rev-parse --short HEAD`). Never include your resume, profile or application details.
