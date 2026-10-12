# Installation

## What you need

| Component | Needed for | Notes |
|---|---|---|
| Python 3.11+ and `pip` | everything | 3.11 and 3.13 are tested in CI on Linux and Windows |
| [Claude Code](https://claude.com/claude-code) + a Claude subscription | fit scoring, resume tailoring, the profile learner | used headlessly (`claude -p`); **no API key** |
| Node.js 18+ | resume tailoring | builds the DOCX via the `docx` library |
| Google Chrome + the Claude in Chrome extension | the application filler | runs in *your* Chrome, with your sessions |
| An [Adzuna](https://developer.adzuna.com) key (free) | optional aggregator coverage | paste on the Settings page |

Finding and scoring by keyword works with Python alone.

## Install

```bash
git clone https://github.com/vikaspalakurthi/jobscout.git
cd jobscout
pip install -r requirements.txt
python server.py
```

Open <http://localhost:8765>. The server listens on `127.0.0.1` **and** `::1` - on Windows,
`localhost` resolves to IPv6 first and a server bound to IPv4 only costs two seconds per
click (see [Troubleshooting](../operations/troubleshooting.md)).

!!! note "Windows"
    JobScout is developed on Windows 11 and tested on Windows in CI. PowerShell, Git Bash and
    the desktop Claude Code app all work; file paths in config are relative to the repo root.

## Optional components

=== "Resume tailoring"

    ```bash
    cd .claude/skills/tailor-resume
    npm install
    ```
    Then copy the templates from `templates/resume/` into `resume/` (see
    [Make it yours](make-it-yours.md)) and press **Test** under *Resume tailoring* on the
    Settings page: it runs `claude --version` and a one-line `claude -p` round trip.

=== "Fit scoring"

    Needs the same `claude` binary and `resume/skills-inventory.md`. It is on by default and
    scores gated new jobs after every refresh; switch it off on the Settings page if you do not
    have a subscription yet.

=== "Application filler"

    Install the Claude in Chrome extension, open a Claude Code session with it connected, and
    type `/apply-queue`. Nothing to configure in JobScout beyond `resume/profile.yaml`.

## Running it as a service

JobScout is a single process (`python server.py`). The refresh schedule and fit scoring run
inside it, so keep it running (a terminal tab, a `tmux` window, a Windows scheduled task at
logon). There is no daemon mode and no OS-level scheduler; if the server is down at 06:00,
that run is skipped, not run late.
