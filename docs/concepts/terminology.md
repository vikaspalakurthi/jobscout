# Terminology

**ATS (applicant tracking system)**
:   The software an employer rents to publish jobs and collect applications: Greenhouse, Lever,
    Ashby, SmartRecruiters, Workday, Oracle HCM, Phenom, iCIMS, … The careers page you see is
    usually a skin over one of these. JobScout talks to the ATS, not the page.

**Board**
:   One employer's job list inside an ATS. Identified by a **slug** (`greenhouse:stripe`,
    `workday:cisco:wd5:cisco_careers`). `config/companies.yaml` is the list of boards.

**Category 1 / 2 / 3** (of employer)
:   1 = the ATS publishes a documented public API (Greenhouse, Lever, Ashby, …).
    2 = the employer's own site exposes an undocumented but public JSON endpoint (Workday
    CXS, Oracle HCM, Phenom, iCIMS) - fetched. 3 = bot-protected or login-walled (Tesla,
    Apple, LinkedIn, Indeed) - **never** fetched; reached only through aggregators or the
    Watchlist. See [Sources](sources.md).

**Aggregator**
:   A site that republishes many employers' postings: Adzuna (API key), Remotive, RemoteOK,
    The Muse, Hacker News "Who is hiring". Cheaper coverage, lower quality (snippets, stale
    dates, reposts). Source filter "Direct boards (all)" vs "Aggregators (all)".

**Fingerprint / dedupe**
:   The same job seen from several sources collapses to one card (`fingerprint` =
    normalised company + title), preferring the direct board. Nothing is deleted.

**Relevance (prefilter)**
:   Fetch-time title regexes and a description "rescue" rule from the active preset. A posting
    that fails both is not stored. Change the preset or `relevance` in `skills.yaml`, then
    Refresh.

**Keyword score** (`kw`)
:   0-100 from `config/skills.yaml`: title match, seniority, skill groups found in the
    description, location points, freshness, direct-board bonus, minus flag penalties.
    Instant; recomputed by **Rescore**. The prefilter and fallback for the fit score.

**Fit score**
:   0-100 judged by a cheap Claude model against your skills inventory, preferences and
    judgement rules, with a one-line reason and up to three gaps. Only jobs above the gate
    (keyword ≥ 60 by default) are sent. The number the list sorts on. See [Scoring](scoring.md).

**Fit profile**
:   `resume/fit-profile.md` - the judgement rules. Seeded from the preset; rewritten by a
    stronger model from your thumbs (**too high / right / too low** + a note).

**Location class**
:   `home` (your metro), `home_state` (other metros in your state), `remote_us`,
    `remote_global` (remote without a US signal), `us_other` (onsite elsewhere in the US),
    `non_us`, `unknown`. Set under Settings → Home.

**Work mode**
:   remote / hybrid / office, detected from the location text and description.

**Flags**
:   Posting-level warnings with score penalties: citizenship/clearance required, ITAR,
    export-control notice, no sponsorship, contract.

**Status**
:   Your verdict on a job: new, starred, saved, applied, not interested, hidden. Every action
    is a toggle. Statuses, viewed timestamps, applied timestamps and notes survive refreshes.

**Tailoring**
:   The two-phase Claude Code skill: *analyze* asks only about requirements your inventory
    cannot answer; *generate* writes the two-page DOCX, cover letters and Q&A answers.

**Plan**
:   The application plan: every field of the employer's form with the value JobScout resolved
    from `profile.yaml`, the tailored files, or your typed answers - shown for review before
    anything is filled. Unresolved fields are shown as such, never guessed.

**Prefill / unattended**
:   Two filler modes. *Prefill* fills the form in your Chrome and leaves the tab for your
    Submit. *Unattended* submits only when the live form matches the approved plan exactly.

**Filler** (`/apply-queue`)
:   The Claude Code skill that drains the queue of approved plans in your Chrome through the
    Claude in Chrome extension. Never captchas, logins, passwords or account creation.

**Held / failed / prefilled / submitted**
:   Run outcomes. *Held* = stopped by rule (login wall, captcha, an unplanned question).
    *Failed* = the posting is gone or the page broke. *Prefilled* = waiting for your Submit.

**Refresh / Rescore**
:   Refresh fetches every source (minutes). Rescore re-applies `skills.yaml` to stored jobs
    (instant, no network).
