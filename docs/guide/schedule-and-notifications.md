# Schedule and notifications

## Refresh schedule

Settings → Refresh schedule. Two modes, checked once a minute by the running server:

- **Refresh at** `06:00, 11:00, 18:00, 23:00` (default; PC-local clock times). Fires once per
  slot. A slot that passes while the server is down - or before it was started - is skipped,
  not run late.
- **Every N hours** - used only when the list of times is empty.

The Jobs page shows "auto-refresh at … · next 18:00" beside the Refresh button, and the
progress strip while a run is in flight. Each scheduled run does a full refresh, rescores,
posts the Discord digest, and queues fit scoring for the gated new jobs.

A preview instance started with `JOBSCOUT_PORT` never schedules, so two servers on one machine
cannot refresh the same database at once.

!!! note "There is no OS-level task"
    The scheduler lives inside `python server.py`. Keep it running (a terminal tab, a logon
    task). `python refresh.py` is the CLI equivalent if you prefer the OS scheduler - but not
    both at once.

## Discord digest

Settings → Notifications: paste a webhook URL, tick enable, **Send test**. After every
refresh, the new postings at or above `notify.min_score` in your `loc_classes` (home, home
state, US-remote by default) are posted as one embed, `max_items` lines, with a link to the
Jobs page filtered to *New this refresh*. The webhook lives in `config/local.yaml` only.

## "New this refresh"

`db.upsert_jobs` returns the ids it inserted; they are kept with the last-refresh report and
back the checkbox on the Jobs page, so "what arrived since I last looked" is one click even
without Discord.

## Fit scoring after a refresh

New jobs that pass the gate are queued automatically (capped by `fit.max_per_run`); the Jobs
page shows "Fit scoring: N left · ~M min" and reloads as batches land. Switch it off under
Settings → Fit scoring if you want refreshes to stay cheap.
