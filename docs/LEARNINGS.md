# External-API learnings (check before touching any fetcher)

Hard-won facts. Append new discoveries with a date; do not delete entries.

## ATS platforms

- **Greenhouse** `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` — content
  is HTML-**escaped** (`html.unescape` before stripping). `updated_at` ≈ posted date.
  Boards return ALL departments — prefilter is mandatory. A 200 with 0 jobs can mean a
  decoy/regional slug; verify totals when a big company shows 0.
- **Lever** `api.lever.co/v0/postings/{slug}?mode=json` — `workplaceType` gives
  remote/hybrid/onsite explicitly; `createdAt` is ms epoch.
- **Ashby** `api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` —
  `isRemote`, `compensationTierSummary`, `secondaryLocations`. Slugs may contain dashes
  ("redpanda-data", "grocery-tv").
- **SmartRecruiters** — list API is cheap but descriptions cost ONE EXTRA CALL PER JOB
  (`postings/{id}`); therefore title-only prefilter there.
- **Workday CXS** (2026-09-22): every myworkdayjobs.com site has
  `POST /wday/cxs/{tenant}/{site}/jobs` `{appliedFacets, limit≤20, offset, searchText}`.
  Discover tenant/site by GETting `https://{tenant}.{wdN}.myworkdayjobs.com` and reading
  the redirect path; wd hosts seen: wd1/wd2/wd3/wd5/wd10/wd12/wd101/wd103/wd108.
  US-country facet GUID `bc33aa3152ec42d4995f4791a106ed09` works on MOST tenants but
  some 400 on it — always fall back to `appliedFacets: {}`. Detail
  (`GET cxs .../{externalPath}`) has jobDescription, startDate (real posted date),
  location + additionalLocations (Austin often hides in additionalLocations!),
  externalUrl, jobReqId. `postedOn` in list is human text ("Posted 26 Days Ago") — useless.
- **Recruitee** `{slug}.recruitee.com/api/offers/`; **Workable** widget API
  `apply.workable.com/api/v1/widget/accounts/{slug}?details=true` (details=true inlines
  descriptions); **BambooHR** `{slug}.bamboohr.com/careers/list` + `/careers/{id}/detail`
  (detail costs a call per job → title-only prefilter); **Breezy** `{slug}.breezy.hr/json`
  (top-level array). All public, all no-auth. (2026-09-23)
- **Slug ≠ company name**: Confluent=ashby:confluent, Timescale→TigerData=ashby:tigerdata,
  DigitalOcean=greenhouse:digitalocean98, Broadcom≠avagotech(dead), HashiCorp→IBM (gone).
  Use `python detect.py` instead of guessing.

## Aggregators

- **Adzuna**: `what` = AND of all words ("sre" needs its own query — "site reliability
  engineer" won't return SRE-only ads). `what_or` = any-word. `title_only` = title-scoped
  (the good one for company sweeps). `company` = SUBSTRING match (apple→Applebee's) —
  post-filter with word-boundary regex on display_name. `company=AMD` → HTTP 400
  (parser quirk); wrap every sweep call in try/except. Date-sorted top-50 per query is
  the hard window — big employers get squeezed out of generic queries (that's why
  company sweeps exist). `created` is Adzuna's INDEXING date, not the original posting
  date; agencies repost (one SitusAMC req appeared 21×; fingerprint collapse handles it).
  Salaries often model-estimated (`salary_is_predicted`).
  **Descriptions are 500-char snippets, always** (2,196 of 2,196 Adzuna rows end in "…",
  2026-09-24) - the API has no full-text field. Consequences: skills-in-description
  scoring is weak for Adzuna rows, and the tailor skill may literally ask for the "Full
  job description" (ShipperHQ). Probe of the `redirect_url` targets: `adzuna.com/details/<id>`
  pages carry a JSON-LD JobPosting with the full text (2 of 4), `adzuna.com/land/ad/…`
  returns 403 (bot-protected), expired ads 404. Fetching those pages is site scraping, not
  the API - not done; decision pending with the user. The clean fix is adding the
  employer's own board (`python detect.py "<Company>"`) so the direct source wins the
  fingerprint dedupe.
- **HN Who is hiring** (Algolia, official): find story via
  `tags=story,author_whoishiring`; top-level comments = `tags=comment,story_{id}`,
  filter `parent_id == story_id`. Posts are freeform "Company | Role | Location | ..."
  but order varies — if segment location doesn't classify, classify the whole first line.
- **RemoteOK** needs a User-Agent; first array element is a legal notice, not a job.
- **The Muse** category param can 400 — retry without it.

## Own-platform companies (decided: do NOT scrape or bypass)

- Apple: jobs.apple.com API → 401 (auth/CSRF-gated). Tesla: cua-api → 403 Akamai.
  Microsoft: gcsservices careers API dead (SSL host mismatch, retired). Google/Meta:
  protected. All of these = Adzuna company sweeps only.
- Amazon: `amazon.jobs/en/search.json` public; `loc_query` alone is unreliable — send
  `normalized_country_code[]=USA` and post-filter location anyway.
- Oracle: ORC `recruitingCEJobRequisitions`, siteNumber CX_45001;
  **without `expand=requisitionList...` the list comes back EMPTY** while
  TotalJobsCount is populated.
- Staffing/prime vendors (TEKsystems, Apex, Judge, Insight Global, Kforce, Actalent,
  Randstad, ManpowerGroup) + Big-4 + Indian consultancies (except Accenture=workday):
  Bullhorn/JobDiva/Phenom/Avature portals, no public APIs. Their public postings arrive
  via Adzuna; true C2C stream lives on Dice (no API) and recruiter lists.

## App/runtime gotchas

- (2026-09-22) **Browser caches index.html across server restarts** — Flask static
  serving sends cache-validating headers, so after code updates the user's open browser
  kept running the OLD inline JS (symptom: "status clicks don't update until I reload").
  Fixed with a global `Cache-Control: no-store` after_request hook in server.py. If UI
  behavior ever looks stale again, check that hook first, then hard-reload.
- Status buttons update the UI optimistically before the POST; server remains the truth
  on the next loadJobs.

## Classifier gotchas

- Location "United States"/"USA" alone = distributed/US-wide → classify remote_us
  (BARE_US regex), NOT us_other. "Virtual" = remote. Apple Austin = "Mc Neil, Travis
  County" → austin. NON_US city list must include new spellings (Gurugram≠Gurgaon,
  Karachi, Hong Kong…) — leaks show up as `unknown` class with foreign cities.
- EPPA boilerplate: "Employee Polygraph Protection Act" appears in many US postings —
  the citizenship flag has an `ignore` context for it. Generic "export control"
  compliance text is NOT an ITAR restriction (separate soft flag).
- Dev-role demotion: `title_penalties` with `unless` ops-context guard — don't penalize
  "Software Engineer, Platform" but do penalize "Java Engineer - ... - Elasticsearch".
- Citizenship phrasing varies far more than "must be a US citizen" — fixed Sep 2026 with
  regex `patterns` on the citizenship flag after 24 citizen-only jobs sat in the inbox with
  work_auth='' (incl. #2 overall). Real-world variants seen: MongoDB "Special Requirement:
  Be a US Citizen" (FedRAMP); GitLab "must be a United States Citizen"; OpenAI "must be U.S.
  citizens"; Highmark "MUST BE US Citizen"; LanceSoft "US Citizens ONLY"; ServiceNow "only
  US citizens…or Permanent Residents"; Ontic "who are U.S. citizens"; Okta/Striveworks "must
  be a US person"; CrowdStrike "US Citizen or Green Card holder" and CJIS "requires U.S.
  citizenship or Green Card/permanent resident status". Citizen-or-GC also excludes work-authorized
  non-residents, so it counts as citizen_only. Every pattern requires a requirement cue (be/only/who are/
  "or green card") because bare "citizen(ship)" is mostly EEO lists ("national origin,
  citizenship, age") and `citizens?\b` can't match "citizenship". Deliberately NOT flagged:
  Bright Vision's Adzuna snippets "Sponsorship: U.S. Citizens, Green…" (truncated — the list
  may continue with EAD), and "US-Citizen, H-1B, OPT-EAD, GC-EAD" style lists (EAD OK).
- Blocked titles (`title_blockers`) return before flag evaluation, so they always get
  work_auth='' — harmless (score 0) but don't read work_auth on blocked rows as "OK".

## Headless Claude Code (`claude -p`) for the tailor-resume skill

Found while testing analyze/generate on real jobs (Sep 2026, Claude Code 2.1.280):
- Run from the repo root so the project skill in `.claude/skills/` is discovered.
- **Writes under `.claude/` are blocked headlessly** even with Edit/Write in
  `--allowedTools` — the generate phase could not update `skills-inventory.md` (it dumped
  pending entries to a side file instead). Fixed by moving the fact files to `resume/`
  (verified: a headless Edit there succeeds).
- Redirect stdin (`< /dev/null`, or `stdin=DEVNULL` in subprocess) or it waits 3s and
  prints "no stdin data received" to stderr.
- `--output-format json` gives `result`, `session_id`, `is_error`, `duration_ms`,
  `total_cost_usd` (API-equivalent figure; not billed on Max, counts toward limits).
  `--resume <session_id>` continues analyze → generate with context intact.
- The skill's "JSON only" output sometimes arrives wrapped in a prose sentence — extract
  the first `{...}` block rather than parsing `result` directly.
- Parallel generate runs would each append the same global answers to the inventory —
  serialize inventory writes (one writer), generate DOCX in parallel only after that.
- stderr may carry an unrelated `[mcp-sdk] SEP-2352 ... issuer stamp` warning from the
  user's MCP connector credentials — ignore stderr unless the exit code is non-zero.
- Timing: analyze ~16-22s, generate ~1-2.5 min per job.
- `detect.py` Workday discovery misses tenants whose site name isn't a common pattern:
  CVS Health is `cvshealth.wd1.myworkdayjobs.com/CVS_Health_Careers` (prober reported "no
  public board" on 2026-09-24). When a big employer shows up Adzuna-only, try the Workday
  URL from its careers page by hand: `https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/
  <tenant>/<site>/jobs` (POST {"appliedFacets":{},"limit":5,"offset":0,"searchText":"sre"}).
- Location formats that were silently `unknown` until 2026-09-24: Adzuna's "City, County,
  State" (full state name, no abbreviation - ~325 rows), Workday "ST - City" /
  "ST - Work from home" / "Work At Home-State" (CVS Health), and "City,KOR"-style country
  suffixes. Classifier now knows US state names, state/country prefixes and WFH wording;
  rescore backfills a vague stored class (unknown/remote_global/us_other) when the fresh
  class is more specific, and flips a stale `office` work_mode when the location text itself
  says remote/hybrid/WFH.

## Application forms (apply.py, Sep 2026)

- Greenhouse Job Board API returns the full application form with `?questions=true`
  (`questions[].fields[0].{name,type,values}`, `demographic_questions` for EEO); `compliance`
  can be `null`. Custom questions are named `question_<id>`.
- Ashby: no REST form endpoint, but the apply page's GraphQL (`/api/non-user-graphql?op=
  ApiJobPosting`) returns `applicationForm`; `fieldEntries.field` is a JSON scalar (querying
  sub-fields fails with GRAPHQL_VALIDATION_FAILED). Types: String/Email/File/Boolean/
  ValueSelect / LongText (multi-line custom answers) / Location (`_systemfield_location`,
  a free-text city picker); paths `_systemfield_name/_email/_resume` for the fixed ones.
  Some Ashby forms carry a required "years of relevant experience" ValueSelect ("1-2 years"
  … "8+") and an "Are you at least 18?" Boolean - both resolved from the profile.
- `compensationTierSummary` uses an en dash and a bullet ("$250K – $325K • Offers Equity");
  the Windows console renders them as `�` - that is display only, the stored text is fine.
- Lever apply pages are plain HTML: `name/email/phone/location/org/urls[LinkedIn]/resume`,
  custom cards as `cards[<uuid>][fieldN]` with the question JSON in the hidden
  `[baseTemplate]` input, `eeo[gender|race|veteran]` selects — and an hCaptcha
  (`h-captcha-response`), so Lever is prefill-only.

## Filler: lessons from ~70 live runs on real forms (Sep-Oct 2026)

Consolidated from the run logs, keyed by platform. Company names are omitted on purpose; the
form behaviours are what matter and they repeat across employers on the same ATS.

### Greenhouse (`job-boards.greenhouse.io`, embeds, EU)
- Careers pages that embed Greenhouse (`<careers site>/jobs/?gh_jid=<id>`) render the form in a
  cross-origin `iframe#grnhse_iframe`; the Chrome tools' accessibility tree sees none of it. The
  iframe's own page is a standalone form: `https://boards.greenhouse.io/embed/job_app?for=<board>
  &token=<id>` (redirects to `job-boards.greenhouse.io`). `read_greenhouse()` returns that as
  `apply_url` whenever the API's `absolute_url` is not on greenhouse.io.
- The form loads **reCAPTCHA Enterprise invisibly** (badge bottom-right, hidden
  `g-recaptcha-response`). The Job Board API cannot tell (plan `captcha` stays `unknown`). It
  did not challenge on any unattended submit so far; a confirmation lands on
  `/embed/job_app/confirmation` ("Thank you for applying!").
- Selects are react-style comboboxes (`input[role=combobox]` + "Toggle flyout"): click, type the
  option text, wait, Return. Substring filter ("Male" lists Male then Female). Race/Ethnicity is
  a multi-select of chips; Hispanic/Latino = No reveals a separate `#race` combobox only after it
  is answered. Read the chosen value back from the control's text, not the input's value.
  Synthetic `mousedown`/`keydown ArrowDown` do not open these menus; a real click does - and only
  while the tab is visible.
- Page-only extras the API form omits: the phone **Country** picker (`aria-required=false`, yet
  Submit fails with "Select a country" when empty - set it from `profile.country`; the flag
  `title` is the proof, `#country.value` reads back empty), a required **"Location (City)"**
  (`#candidate-location` autocomplete - very common; the planner now emits it from profile
  `location`), a required **demographic-data consent** checkbox
  (`gdpr_demographic_data_consent_given`, ticked when `eeo.mode == answer`), and on some boards
  required **Employment / Education blocks** (company, title, start/end month+year, "Current
  role"; school/degree/discipline comboboxes that load options asynchronously - select via
  `option.click()`, never Return before the menu opened, which submits the incomplete form and
  leaves stale "This field is required" text). "End date must be in the past" for the current
  month -> tick "Current role". Uploading the resume does NOT autofill these blocks on the embed
  form; type them from the job's `resume.md`. Tagging inputs with a temporary `aria-label` makes
  `find` return exact refs for repeated blocks with identical labels.
- **Autofill echo**: typing First Name / Email can produce a doubled value ("JaneJane"); Last
  Name and Phone are fine. Always read back and fix with `triple_click` + retype. After file
  uploads the form may re-render and text inputs read back empty - coordinate clicks + read-back.
- After a successful `file_upload` Greenhouse removes the `input[type=file]` (reading `.files`
  throws); the proof is the file name rendered next to the slot.
- Greenhouse EU (`job-boards.eu.greenhouse.io`): a closed posting redirects to the board with
  `?error=true` and a "no longer open" banner -> report `failed`.

### Ashby
- The GraphQL `applicationForm` OMITS the EEO survey (gender / race / veteran) and some boards'
  optional diversity survey and future-contact consent; they exist only on the page. The filler
  adds EEO from the profile and the exact-match guard treats that section as a known extra.
- Long-answer questions render as single-line inputs: newlines are dropped and paragraphs run
  together - join paragraphs with a space before filling.
- Programmatic `form_input` can leave the page's own validation stale (Submit flags a correct
  email); type into validated inputs or dispatch input/blur, and expect one benign nag.
- Location is an autocomplete: type the plan value ("Georgetown, TX"), the first suggestion is
  "Georgetown, Texas, United States"; Return selects it. Native radios did not respond to a
  coordinate click from a scaled screenshot; `find` -> click by ref works. Yes/No buttons may
  need a script `click()` (check for the `_active_` class).
- File uploads can silently not register on the first try; verify the file name next to the slot.
- A removed posting shows "Job not found"; a successful submit shows a green "Your application
  was successfully submitted" banner (useful when the Submit happened from the open tab).
- Ashby kept accepting typing, uploads and option clicks while the Chrome window was hidden;
  Greenhouse did not (see "Chrome extension mechanics").

### SmartRecruiters
- `jobs.smartrecruiters.com/<Company>/<id>` -> "I'm interested" ->
  `/oneclick-ui/company/<Company>/publication/<uuid>`: Easy Apply with no login. Page 1 = personal
  info (City autocomplete "City, ST, US"), optional Experience/Education, profiles, Resume*
  dropzone, optional "Message to the Hiring Team"; page 2 (`/screening`) = preliminary questions
  + privacy declaration + Submit. All inputs live in **shadow DOM** (`read_page`/`find` see
  nothing): walk `shadowRoot`s, click by coordinates, upload via a temporary light-DOM
  `<input type=file>` proxy -> `file_upload` -> copy `files` into the shadow input with
  `DataTransfer` and dispatch composed `input`/`change`. The first dropzone is "autofill from
  resume" - use the one under the Resume heading.
- Sessions expire from inactivity (`/oneclick-ui/session/expired`) and nothing is kept - keep SR
  prefills late in a batch or ask the user to submit those first.

### Login walls (always `held`, by rule)
- **Workday**: Apply -> "Autofill with Resume / Apply Manually / Use My Last Application" -> step
  1 of 7 "Create Account/Sign In". Ref clicks on the cookie banner and Apply did not register;
  coordinates did.
- **Oracle Recruiting Cloud** (`<tenant>.fa.oraclecloud.com`, `careers.<company>.com/.../job/<id>`):
  "Apply Now" -> email + one-time code or "Enter your email address" (creates a candidate
  profile) + required terms checkbox. One tenant's page also froze under a session dialog.
- **iCIMS** (`careers-<company>.icims.com`): the job page is a cross-origin iframe; the apply
  entry is `/jobs/<id>/<slug>/login` ("Privacy Notice and Sign-In", email + phone). Closed
  postings: "The requested job could not be found" / redirect to `/jobs/search?...notFound=1`.
- **amazon.jobs**: "Apply now" -> passport.amazon.jobs "Log in or create account".
- **Phenom** native wizards (`/us/en/apply?jobSeqNo=<seq>`, 6 steps: My Information, My
  Experience, Application Questions, Supplementary Questions, Voluntary Disclosures, Review) have
  no login on step 1 but the planner has no reader for them -> fill-time discovery, no fields,
  `held` until a reader exists.

### Aggregator landings (Adzuna)
- `adzuna.com/land/ad/<id>` (Appcast-sourced) usually **redirects to Dice.com**. Dice Easy Apply
  is a 2-step wizard (`/job-applications/<uuid>/wizard`): step 1 = the **profile** resume (not the
  tailored file) + optional cover letter, step 2 = review + Submit. Clicking Dice's own "Apply"
  opens the wizard in a tab outside the MCP tab group (uncontrollable) - navigate the current tab
  straight to the wizard URL instead.
- **Rule: on Dice always swap in the tailored resume.** The wizard's only visible
  `input[type=file]` is the cover letter's. The resume slot's ⋯ menu (Replace / Delete) mounts a
  dedicated resume input when Replace is chosen; Replace calls `input.click()` (native picker ->
  CDP screenshots time out), so stub `HTMLInputElement.prototype.click` first, then `file_upload`
  to the new input. The slot then reads "New file" / "Uploaded to application on <date>". The
  swap is stored per application; the profile resume is untouched (verified). The ⋯ button did
  not open on `left_click` by ref; a coordinate click opened it every time.
- `adzuna.com/details/<id>` with the EASY APPLY badge (CEIPAL-sourced agency reqs) is Adzuna's
  own flow and **requires an Adzuna account** (`/authenticate/register`) -> `held`; an email-alert
  interstitial ("No thanks, take me to the job") sits in front of it. Plans for such jobs carry an
  "Adzuna link" flag. `land/ad` links also go dead (redirect to "no longer available", a generic
  ZipRecruiter search, or "Cannot find page") -> `failed`.
- Agencies repost the same req under several names ("X Systems" vs "X Systems LLC"); fingerprint
  dedupe does not catch it - check the Applied tab before queuing a look-alike.
- Cover letters on demand work here: `POST /api/jobs/<id>/resume/cover` + polling `apply/files`
  returns `ready` in ~2-4 min; start it right after pulling the plan so it overlaps the swap.

### Chrome extension mechanics
- `ctrl+a` sent to an **empty** input leaks a literal "a" ("aDoe") - `triple_click` to select
  existing text, type straight into empty fields, then read back and retype.
- `read_page` (filter interactive) returns only the elements near the viewport on long forms;
  `find` reaches off-screen fields, and a `javascript_tool` sweep over
  `input, select, textarea, [role=combobox]` gives the full inventory in one call.
- `javascript_tool` redacts result keys that look sensitive (`authorized`, `token`, an iframe
  `src` with a query string) - use neutral key names when reading values back.
- `computer screenshot save_to_disk:true` returned no path; filled-form screenshots are skipped.
- **Minimized / covered Chrome window**: every tab reports `visibilityState: hidden`, ref clicks
  and screenshots time out ("renderer may be frozen"), and React updates do not flush (calling
  react-select's `selectOption` returns without rendering). Only fix: the user restores the
  window. Typing by ref after file uploads can also land nowhere while a tab is in the background;
  a screenshot (which fronts the tab) then coordinate clicks fixes it.
- Tabs the site itself opens are listed by `tabs_context_mcp` but every action on them fails
  with "not in the same group". Closing the last tab of the MCP group dissolves it - create the
  next tab first. Never reuse a tab that holds a filled form (navigating it wipes the form).

### Queue and planner behaviour learned from the runs
- `GET /api/apply/queue/next?mode=prefill` filters by plan mode; it does not override. Switching
  a plan's mode keeps its approved/queued state. The per-run cap is `apply.max_per_run`
  (default 5): prefilled tabs pile up and some ATS sessions expire while waiting.
- `record_result` accepts a second report for the same run id (a plan filled by the skill and
  submitted from the open tab is re-reported as `submitted`). `mark_submitted` refuses when the
  job is already `submitted`.
- Resolver bugs caught by reading plans before approving (all fixed or guarded since): referral
  questions resolving to the candidate's last name; "previously employed here?" resolving to the
  location; race options matched by substring ("Asian" inside "Indian"); work-permit / visa /
  right-to-work questions needing the same mapping as sponsorship questions (free-text variants
  want the country, not "Yes"); attachment slots labelled "Cover Letter" treated as resume slots;
  questions mentioning "employment"/"education" falling into the employer/school/years rules and
  producing non-option values. Rule for the filler: a plan value that is not one of the
  question's options is a resolver bug - leave it for the user, name it in `reason`.
- Multi-checkbox consents ("privacy statement" + "third-party background check") planned as a
  bare "Yes": the filler ticks only the privacy box and leaves the rest for the user; the planner
  should list each box separately.
- Unattended plans with any unplanned page-only field (Location, a generated essay) cannot
  auto-submit; they run as prefill.

## Bulk board discovery (discover.py, 2026-09-24)

- 592 names (805 Adzuna employers filtered of agencies + a 300-name Austin/infra list) →
  436 probed → 156 hits in 29 min with 10 threads; 132 kept after review. Hit rate on real
  employers is ~35%; the misses are own-platform (Dell, Apple, Tesla, Samsung, Oracle,
  IBM…), iCIMS/Phenom/Taleo, and staffing agencies.
- **Slug collisions are real**: Recruitee ("google", "meta", "ey", "samsung" → 1-3 posting
  boards of unrelated firms), Breezy and BambooHR (1-5 postings) and SmartRecruiters
  ("Uber" 1, "Thales" 2) all matched the wrong company. Rule: drop Recruitee/Breezy/Workable
  hits, drop anything with <= 2 postings unless the slug is obviously the company, and drop
  Workday sites reporting 0 postings (wrong site name: Qualcomm, ThoughtSpot).
- The 132 boards fetched in 501 s (Workday tenants dominate) and added 460 relevant rows
  (183 texas / 181 remote_us / 48 austin), 87 scoring >= 50; 67 of 132 boards had 0 relevant
  jobs today but cost ~1 call each, so they stay.

## Headless `claude -p --chrome` cannot drive Chrome (probe, 2026-09-24)

- `claude -p ... --chrome` does expose the `mcp__claude-in-chrome__*` tools headlessly, but
  every real call fails with "Claude in Chrome requires permission to be granted" - the
  extension grants access per interactive session (desktop app / `claude` terminal), and a
  headless run has no place to grant it. So a JobScout "Run queue" button that spawns
  `claude -p "/apply-queue"` from the server is not possible today; the filler must run from
  an interactive Claude session or a desktop-app scheduled task (STATUS.md next step 3).
- Note the flag order: the prompt must come BEFORE `--allowedTools`, otherwise it is
  swallowed as another allowed-tool name and `-p` complains that no input was provided.

## Large Austin employers probe (2026-09-25)

- 51 names with guessed Workday tenants / board slugs: 16 hits, 11 real. Workday tenants found:
  Applied Materials `amat:wd1:External`, Visa `visa:wd5:Visa`, Intel `intel:wd1:External`,
  Expedia `expedia:wd108:Search`, Motorola Solutions `motorolasolutions:wd5:Careers`, Abbott
  `abbott:wd5:abbottcareers`; boards: Flex (Greenhouse), Bazaarvoice/Everlywell (Lever),
  Bumble `bumbleinc`/Amplify (Ashby). Collisions again on Recruitee (Samsung), BambooHR (Rex)
  and SmartRecruiters (Wayfair 1 posting).
- Still no public feed (category 2/3, see the source explainer in ARCHITECTURE): AMD, Arm,
  Cirrus Logic, Infineon, H-E-B, PayPal, Fidelity, JPMorgan Chase, Texas Instruments, Indeed,
  Dell, Cisco, Samsung, Ericsson, Emerson/NI, Jabil, Q2, Enverus, Workrise, Hippo, Ambiq,
  Keller Williams, YETI, Kendra Scott, Charles Schwab. Most sit on Phenom, Oracle HCM,
  Eightfold, iCIMS or SuccessFactors - readable JSON endpoints, fetchers not written yet.

## Category-2 platform endpoints (2026-09-26)

- **Oracle HCM** (Recruiting Cloud): `GET https://<apihost>/hcmRestApi/resources/latest/
  recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=
  findReqs;siteNumber=<site>,keyword="...",limit=50,offset=0,sortBy=POSTING_DATES_DESC`; details
  via `recruitingCEJobRequisitionDetails?finder=ById;siteNumber=<site>,Id="<id>"` (8 KB
  descriptions). Site numbers: `recruitingCESites?onlyData=true` (JPMC has several `CX_1001`
  active). Some tenants proxy through a custom domain (careers.ti.com → edbz.fa.us2, site
  code `CX`); Dell runs it on its own host `enterpriseplatform.dell.com` (`CX_1001`) - jobs.dell.com
  is Akamai-blocked but the API host is not.
- **Phenom**: `POST https://<host>/widgets` with `ddoKey: refineSearch` (fields reqId,
  jobSeqNo, title, cityStateCountry, postedDate, RemoteType, applyUrl, descriptionTeaser);
  no details ddoKey works, but the job page `<host>/<prefix>/job/<jobSeqNo>` embeds
  `phApp.ddo = {...}` with `jobDetail.data.job.description` (11 KB). Cisco's Phenom search
  hands off to Workday for apply (`cisco:wd5:Cisco_Careers`, use that directly); BSW hands
  off to Taleo. Some Phenom hosts sit behind Akamai (jobs.dell.com 403, jobs.ericsson.com 401).
- **iCIMS** has two flavours: "Attract" sites (`careers.<co>.com/careers-home`) expose
  `GET /api/jobs?keywords=&page=1` JSON with full descriptions and `country_code`
  (AMD 249 jobs, H-E-B); classic portals (`career-schwab.icims.com`) are HTML - `/jobs/search?
  ss=1&searchKeyword=<term>&in_iframe=1` lists `<a class="iCIMS_Anchor" href=".../jobs/<id>/
  <slug>/job"><h3>title</h3>`, the job page has `Job Locations</span><span>US-TX-Austin |
  US-IL-Chicago</span>` and `Posted Date</dt><dd><span title="9/24/2026 4:22 PM">`. A classic
  portal that redirects to an Attract site returns a 140-byte `window.top.location` stub.
- **Eightfold** (`<co>.eightfold.ai/api/apply/v2/jobs?domain=`): every variant answers 403
  `Not authorized for PCSX`; the page injects a per-session token. Not fetched (Infineon,
  Ericsson, PayPal). **SuccessFactors** career sites (jobs.infineon.com/search) redirect to SSO.
- Fingerprints for `detect.py --url`: `phenompeople.com` scripts, `*.icims.com/jobs` links,
  `*.fa.*.oraclecloud.com` or `/hcmUI/CandidateExperience`, `*.wd*.myworkdayjobs.com` links
  (login/introduceYourself URLs still carry the site name), `eightfold.ai`, `successfactors.com`.
- Arm's `experienced-arm.icims.com` portal redirects to `careers.arm.com/search-jobs` (a
  Radancy/TalentBrew site: `/search-jobs` URL pattern, no JSON found) - removed again; Fidelity
  (`jobs.fidelity.com`) shows no known fingerprint either. Both stay Adzuna-only for now.

## Discovery pass 2 with fingerprinting (2026-09-26)

- `discover.py --fingerprint`: name → domain via the free Clearbit autocomplete
  (`autocomplete.clearbit.com/v1/companies/suggest?query=`, no key) → try `careers.<d>`, `jobs.<d>`,
  `www.<d>/careers`… → `detect.detect_url`. 233 names (Adzuna employers + watchlist + earlier
  misses), 60 hits in 48 min, 16 kept after review. New boards: Toyota, Fiserv, Vistra, Actalent
  (Phenom); Texas Mutual, Invesco, DXC, Digital Turbine, WP Engine (Workday); Perficient (Oracle
  HCM); Saronic, Dimensional, Chronosphere (Ashby); Hippo, GoDaddy, Wise (Greenhouse).
- False positives to expect from fingerprinting: a marketing page that merely *links* to a vendor
  (JPMorgan's page → `www.jpmorganchase.com:CX_1001`, wrong API host - the Oracle rule now only
  uses the page host when the final URL contains `/sites/`); the wrong company's board linked on
  a page (NI → `nike`, Veloc → `pebl`, Activesoft → `isaac`); Greenhouse `embed`/Ashby `The`
  captured as slugs (fixed: slugs are probed before being reported, `embed`/`the` skipped);
  foreign tenants (Deloitte → `deloitteie`); Recruitee/Breezy/BambooHR collisions as before.
- Recognised, still unfetchable: Eightfold (PayPal, Infineon, Ericsson), SuccessFactors (Southern
  Glazer's), Taleo (Virtusa), Jobvite (Ayla, Enverus). Fidelity and Cirrus Logic show no
  fingerprint at all on their careers hosts.
- Vistra's careers site loads Phenom assets but `/widgets` answers HTML (not a Phenom search
  site) - removed. Fiserv's Phenom search works (25 DevOps hits) but its US roles are onsite
  outside Texas, so 0 kept - expected, the board stays.

## Enterprise discovery pass (2026-09-27)

- Input: a 446-name list of Austin/Texas largest employers by sector (healthcare, semis, defense,
  energy, banks/insurers, retail, telecom, IT services, agencies) - names, not job postings, are
  what the prober was missing. 388 probed with `--fingerprint --include-agencies` (48 min, 6
  threads): 166 hits, 121 kept → 397 boards. By platform of the kept set: Workday 54, Phenom 20,
  iCIMS 14, Oracle HCM 12, SmartRecruiters 8, Greenhouse 9, Ashby 2, Lever 2.
- Drops, same rules as before: every Recruitee/BambooHR/Breezy hit (all collisions: Walmart,
  Ford, SAP, Bosch… on 4-posting boards), AT&T → Ashby `attio`, Splunk → Cisco's Phenom host,
  Slack → Salesforce's Workday, Kyndryl early-careers site, KPMG Canada, Deloitte Ireland,
  0-posting Workday sites (ManTech, Qualcomm), and duplicates of configured boards.
- Recognised but unfetchable, now a sizeable block: **SuccessFactors** (Skyworks, Qorvo,
  Teradyne, L3Harris, Halliburton, TechnipFMC, ExxonMobil, CenterPoint, CPS Energy, Lincoln
  Financial, PACCAR, LyondellBasell, Cargill, XPO, Bentley, Celestica, PwC), **Eightfold**
  (Micron, Lam Research, NetApp, Northrop Grumman, Lockheed Martin, Fluor, Chevron, Citi, John
  Deere, Autodesk, NVIDIA, Ericsson, Infineon), **Taleo** (MD Anderson, UT Southwestern, Kaiser,
  Textron, Valero, Enterprise Products, TxDOT, Virtusa), Jobvite (Nutanix). Fetchers for the
  first two are the next coverage step.

## Location classifier: enterprise-board formats (2026-09-27)

- Workday/SmartRecruiters/Phenom enterprise tenants write locations as ISO-2 country code
  first (`BR, São Paulo`, `CN-BEIJING-…`, `IT-FI-FLORENCE`) or last (`Kaunas, LT`, `Graz, AT`,
  `Telford, GB`), plus `International`, `TLV`, `EU`, `San Juan, Puerto Rico`. `NON_US_PREFIX`
  now covers these. **Never put a code that doubles as a US state in that list** (CA, IN, DE,
  PA, CO, IL, AR, ID, MA, TN, AZ, MD, MT, ME…): a first draft did, flagged `Foster City, CA`
  and `Indianapolis, IN` as non-US, and the rescore rule of the time could not undo it.
- `india` (and `pune`) in NON_US need word boundaries: `Indianapolis` contains `india`.
- `scoring.rescore_all` now also moves a `non_us` row back to whatever the classifier says
  today, so a classifier fix self-heals on the next rescore (previously non_us was sticky).
- US state code first (`CT, Bloomfield, 900 Cottage Grove Rd`) is accepted by
  `US_STATE_PREFIX` (comma or dash).

## Remote-first discovery pass (2026-09-27)

- 439 names of remote-hiring infrastructure/security/observability/data/devtools/SaaS/fintech/
  healthtech companies; 289 probed (rest already configured), 145 hits in 29 min, 131 kept →
  528 boards. Hit rate 50%, twice the enterprise list's, because this segment rents
  Greenhouse (67), Ashby (37) and Lever (13) where slug guessing works.
- New collision shapes: 8x8 → Greenhouse `xai`, Headspace → `hs`, env0 → `envato-2`, Clari →
  `careers.clarivate.com`, Redfin → `careers.rocket.com` (domain lookup returned a similarly
  named company); Hotjar and Contentsquare share one Lever board (same company).
- Recognised, unfetchable: Eightfold (Instaclustr, SendGrid, NetApp, Stytch), Jobvite (Nutanix,
  Egnyte), SuccessFactors (Optimizely); Red Hat is on Workday but its tenant is not linked from
  the careers page; Pulley/Catchpoint link Greenhouse but no slug answered.
- Ashby slug collisions can look plausible: `dimensional` on Ashby is not Dimensional Fund
  Advisors (they are on Workday `dimensional:wd5:DFA_Careers`, 2 Austin SRE postings). A
  small-postings hit for a large company is the tell - verify a large employer's platform
  with `detect.py --url` on its careers page before trusting a name-guessed slug.

## Refresh throughput and the Companies page freeze, 2026-10-02

- With 531 boards the serial `run_all` loop was the bottleneck, not the network: boards
  average ~15 s each (Workday/iCIMS fetchers issue one request per BIGBOARD term, 30 s
  timeout each), so a full refresh was pushing 2 hours with a status line that only ever
  showed the latest board name. 8 parallel workers bring the board phase to ~18 min.
- `ThreadPoolExecutor.map` yields in submission order, so a single slow board (Blue Origin's
  Workday tenant) froze the done-counter at 128 for minutes while the other workers kept
  going. Use `as_completed`; nothing downstream needs board order.
- While the pool runs, ordinary requests to the Flask dev server crawl (`/api/companies`
  took 13 s vs well under a second idle): the fetch threads hold the GIL while parsing.
  Acceptable for a background refresh; don't raise `board_workers` past ~12 on this laptop.
- Companies page: 1,500+ rows re-rendered with innerHTML on every keystroke in the search
  box. The JS itself is ~35 ms; the browser's layout of the 9-column table afterwards is what
  froze the tab. Cap the painted rows (300, "show all" link) and debounce the input.

## "Every click takes seconds" - three causes, 2026-10-03

Measured with `python perf.py` (added the same day). None of it was SQL.
- **2 s per request floor on `localhost`**: Windows resolves `localhost` to `::1` first;
  the server listened only on `127.0.0.1`, so each browser request burned ~2 s failing
  over IPv6 before IPv4 answered (2,040 ms vs 14 ms on 127.0.0.1 - even a 404). Werkzeug
  cannot dual-stack one socket (`host="::"` here served IPv6 only), so `server.serve()`
  runs one `make_server` per loopback address in the same process.
- **`/api/resume/status` = 3.3 s**: `apply.readiness` -> `_board_for` -> `load_companies`
  parsed `companies.yaml` (531 entries, pure-Python PyYAML ~250 ms) 12x per poll. YAML
  loads are now cached by file mtime in `fetchers._yaml_cached` (CSafeLoader when
  available; callers get a deep copy). 3.3 s -> 0.1 s.
- **Rendering 1,500 cards**: `renderJobs` built every card's HTML and bound 5 listeners per
  card = 0.45-0.9 s of main-thread work, and a status click did it twice (optimistic
  flip + reload). Now: 300 cards per page ("Show 300 more"), one delegated click/change
  handler on `#list`, and a status click redraws only its own card (2 ms to paint) before
  syncing. Full reload of the inbox: 946 ms -> ~210 ms.

## Model fit scoring via `claude -p` (fit.py), 2026-10-03

- A long prompt as the `-p` argument fails on Windows (command-line length/quoting); pipe
  it on stdin (`subprocess.run([...,"-p","--model",m,"--output-format","json"], input=prompt)`).
  The json envelope then reports `input_tokens` ~10 (the stdin prompt is counted as cache).
- Haiku 4.5 through the CLI: 10 jobs (title + 1,800 chars of description + ~8 KB of
  inventory/rules) = 82-110 s with thinking (~7.7k output tokens, mostly thinking), 30 s with
  `MAX_THINKING_TOKENS=0` (1.4k output). Judgements with thinking are sharper (DBA-flavoured
  SRE 63 vs 72); the user chose full thinking. Env var `MAX_THINKING_TOKENS` is honoured by
  `claude -p`.
- The model follows the profile rules literally: one feedback note about single-engine DB
  internals produced a "## Learned adjustments" rule and the next pass scored every DBA
  role at exactly the stated 50-55 cap. Keep rules concrete (role pattern -> band + why).
- Sonnet rewrite of the ~60-line profile from one feedback item: ~1 min. Guard the write
  (minimum length + required heading) and keep a `.bak-<ts>` next to it.

## watchlist.yaml has two top-level lists, 2026-10-05

`companies:` is followed by `boards:` (Dice/LinkedIn/Wellfound/Built In links). Appending a
company line to the file puts it under `boards` - that is where the Chronosphere entry added
on 2026-09-28 had been sitting, invisible on the Watchlist page. `/api/companies/add_watch`
inserts before `boards:` and asserts the parsed entry is under `companies`.

## Greenhouse `updated_at` is not a posting date, 2026-10-06

Pinterest's board showed "Staff Software Engineer, Observability" as posted 2026-10-05; its
`first_published` is 2026-05-26. Greenhouse bumps `updated_at` on every edit and some
employers bulk-edit their boards (Pinterest: most reqs moved to 10-02, then 10-05), so the
24h/7d filters and the freshness bonus were rewarding stale reqs. `fetch_greenhouse` now
uses `first_published` (present on every job of the boards API) and falls back to
`updated_at`. Expect older Greenhouse reqs to leave the inbox after the next refresh (the
60-day board age limit now sees their real age).

Related: a job marked **applied by hand** while its plan sat in the apply queue stayed
`queued` - the filler would have applied a second time. Marking applied now skips the
queued plan (server) and `next_in_queue` refuses rows whose status is applied.

