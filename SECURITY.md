# Security and privacy

JobScout runs entirely on the user's machine. The things worth protecting are the user's own:
`resume/` (contact details, work history, EEO answers, salary policy), `data/jobs.db`, and
`config/local.yaml` (API keys, Discord webhook). All three are gitignored by design.

## Reporting
Please **do not open a public issue** for anything involving leaked personal data, a way to
make the filler submit something the user did not approve, or a way to make the app send
data anywhere other than the job boards and the user's own Claude subscription.

Use GitHub's private vulnerability reporting on this repository ("Security" tab → "Report a
vulnerability"), or email the maintainer (address on the GitHub profile of
@vikaspalakurthi). You'll get an acknowledgement within a few days.

## Scope
In scope: the Flask server (`server.py`), the fetchers' handling of remote data, the
application filler's guarantees (never submits a form that differs from the approved plan;
never fills captchas, logins, or account creation), and anything that would put personal
data into a tracked file or an outbound request.

Out of scope: the job boards themselves, Claude Code, the Chrome extension.

## Hard guarantees we intend to keep
- No outbound calls except to configured job sources, Discord (if the user sets a webhook),
  and `claude -p` through the user's subscription.
- The server binds loopback only (`127.0.0.1` and `::1`).
- Form values come only from `resume/profile.yaml`, the user's typed answers, or the
  tailored files; anything unresolved is shown, never guessed.
