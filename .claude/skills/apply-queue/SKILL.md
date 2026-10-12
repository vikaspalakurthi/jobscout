---
name: apply-queue
description: Drain JobScout's application queue in the user's Chrome. Pulls each queued, user-approved application plan from the local JobScout API, fills the employer's form with exactly the planned values and files using the Claude in Chrome tools, and either leaves the tab on the review page for the user (prefill) or submits it when the live form matches the approved plan (unattended). Use when the user says "run the apply queue", "/apply-queue", "fill the queued applications", or a scheduled task asks for it.
---

# apply-queue

You are the hands for JobScout's apply pipeline. JobScout (Flask on http://localhost:8765)
owns the queue and the approved plans; you drive the user's real Chrome through the
Claude in Chrome tools (`mcp__claude-in-chrome__*`). Load those tools first (one ToolSearch
call: tabs_context_mcp, tabs_create_mcp, tabs_close_mcp, navigate, read_page, find,
form_input, file_upload, computer, get_page_text, javascript_tool, browser_batch). Use
`browser_batch` for every predictable sequence - one round trip per field block, not per key.

## Hard rules

1. **Only what was approved.** Fill exactly the values in the plan (`plan.fields[].value`).
   Never invent or "improve" an answer. If a required field on the page has no planned value,
   the run for that job stops as `held` - except the page-only extras listed per ATS below.
2. **Never solve or bypass a captcha, never create an account, never enter a password.** A
   login wall, "create account" step, or an interactive captcha challenge → `held` with that
   reason. The user handles those by hand.
3. **Prefill mode: never click Submit.** Leave the filled tab open, report `prefilled`.
4. **Unattended mode: submit only when the guard passes** (section 4). Otherwise `held`.
5. One form at a time. Respect `settings.max_per_run` and `settings.pause_seconds`.
6. Report every outcome to JobScout (`POST /api/apply/result`) before moving on, and always
   report something - a job left in `filling` is a bug.
7. **Verify, don't trust.** After each block of typing, read the live values back with
   `javascript_tool` (`document.getElementById(id).value`, the select control's text, the
   checkbox's `checked`) and retype anything that differs from the plan.

## The loop

```
GET  http://localhost:8765/api/apply/queue/next?runner=<label>[&mode=prefill|unattended]
     -> {done:true}  |  {run_id, job_id, company, title, mode, plan, files, eeo,
                          profile_basics, settings}
... fill (below) ...
POST http://localhost:8765/api/apply/result
     {run_id, outcome: prefilled|submitted|held|failed, reason, screenshot, form_hash_seen,
      confirmation}
```
Use the Bash tool with a small python script (urllib) or `curl` for these calls (they are
local; write JSON to the session scratchpad, not to `$TMPDIR`, which is unset in Git Bash).
`runner` = "manual" when a person asked, "scheduled-prefill" / "scheduled-unattended" from
scheduled tasks; scheduled tasks pass the matching `mode=` filter.

`mode=` is a **filter on each plan's own mode, not an override**: with `mode=prefill` an
unattended plan is skipped and `next` may answer `{done:true}` while the queue is non-empty.
To run an unattended plan as prefill (first live test of a board, user asked to watch), switch
the plan first - `POST /api/jobs/<id>/apply/review {action:"fix", mode:"prefill"}` keeps it
queued - then pull again. Check `GET /api/apply/status` (`queue.queue`, `queue.last_run`)
when `next` says done unexpectedly.

Stop when `done`, when `max_per_run` forms have been handled, or on the first error you
cannot classify (report it as `failed` first).

## Filling one plan

1. `tabs_create_mcp` → new tab; `navigate` to `plan.apply_url`; wait 3-4 s. Cookie banner →
   the decline / "Essential Only" button. If `read_page` shows no form fields but the page has
   an `iframe#grnhse_iframe` (careers page embedding Greenhouse), the form is cross-origin and
   invisible to the tools: navigate to
   `https://job-boards.greenhouse.io/embed/job_app?for=<plan.board>&token=<plan.external_id>`
   (newer plans already carry this as `apply_url`; the careers page is `plan.page_url`).
2. **Read the live form**: `read_page` with filter "interactive" returns only elements near
   the viewport on Greenhouse - scroll and re-read, or `find` the field by label. For a full
   inventory in one call, `javascript_tool` over `input, select, textarea, [role=combobox]`
   with each element's `label[for=id]` text works on every ATS. Build the list of live fields
   (label, type, options, required).
3. **Guard, part 1 (both modes)**: a login / create-account wall, an "already applied"
   notice, or an **interactive** captcha widget (a visible checkbox/puzzle: hCaptcha,
   reCAPTCHA v2, Turnstile) → screenshot, `held`, next job. An **invisible** reCAPTCHA
   (v3 / Enterprise: only the small badge bottom-right, `grecaptcha` scripts, hidden
   `g-recaptcha-response` textarea) is not a blocker: in prefill the user submits anyway; in
   unattended proceed to the guard in section 4 and hold only if a challenge appears after
   clicking Submit.
4. **Fill, field by field**, matching each live field to a plan field by label:
   - Text/email/phone/URL: click the field and **type** the value (typing triggers the site's
     validation; `form_input` alone can leave a stale "invalid email" error). Greenhouse's
     autofill echo can double a typed value ("JaneJane") - the read-back catches it. To replace
     existing text use `triple_click` on the field, then type. **Never send ctrl+a to an
     empty field** - the chord can leak a literal "a" into the input ("aDoe").
   - Long answers: if the input is single-line, join paragraphs with a single space first.
   - Native `<select>`: `form_input` with the option text. React-style comboboxes (new
     Greenhouse `job-boards.greenhouse.io`, Ashby): click the combobox, type the option
     text, wait 1 s, press Return; the filter is a substring match, so type enough to make
     the planned option the first highlighted one (e.g. "Male" lists Male above Female;
     "I am not a protected" isolates the veteran answer). Greenhouse Race/Ethnicity is a
     multi-select (chips) - one Return per planned value.
     **A stray Enter submits the form**: if the menu has not opened (async lists such as
     School/Degree show "Loading..."), Return lands on the form and triggers Submit. Wait
     until `.select__menu [role=option]` lists the planned option, then click that option
     element via `javascript_tool` (`option.click()`) instead of pressing Return.
   - Yes/No buttons (Ashby): click the button whose text matches.
   - Multi-select: tick each planned value.
   - Files: `file_upload` on the `input[type=file]` ref with `files.resume`. **Always the
     tailored file from the plan, never a resume the site already holds**: boards with a
     candidate profile (Dice Easy Apply, LinkedIn-style profiles, Workday "use my profile",
     any "Autofill with resume" pre-selection) pre-attach the profile resume - click
     "Upload a new resume" / "Replace" / the × on the pre-attached file, then upload
     `files.resume`, and confirm the rendered file name is the plan's file (the plan's `files.resume` name, i.e. settings `resume.file_name`
     from `files.dir`), not the profile one. Greenhouse removes the file input after a
     successful upload, so **verify by the rendered file name** in the page text
     (`get_page_text` or `document.body.innerText`), not by reading `.files`; retry once if
     the name is missing.
   - Cover letter: if the form has a cover-letter slot (file or text), attach `files.cover`.
     **If `files.cover` is null, generate it now instead of skipping**:
     `POST /api/jobs/<job_id>/resume/cover {}` (one call; ~2 min on Fable), then poll
     `GET /api/jobs/<job_id>/apply/files` every 20 s until `cover` is a path (or
     `cover_state` is `failed` → note it in `reason` and go on without). Fill the rest of the
     form while waiting, come back to the slot, upload, verify the name. For a text-only
     cover slot paste the letter's text: the same `apply/files` response carries
     `cover_text.short` / `cover_text.long` (short fits most boxes; long only when the box
     asks for a full letter).
   - **Employment / Education blocks** (Greenhouse "Employment" and "Education" sections with
     Company/Title/Start-End month+year, School/Degree/Discipline + "Add another"): the user's
     rule - fill them from the job's tailored `resume.md` in `files.dir` (every role in
     PROFESSIONAL EXPERIENCE, every degree in EDUCATION), since Greenhouse does not parse the
     uploaded resume. Current employer (`profile_basics.current_employer`): tick "Current
     role" (an end month equal to the current month is rejected as "must be in the past").
     A school missing from the list (VIT University) → "0 - Other".
   - EEO sections that are on the page but not in the plan (Ashby renders them page-only):
     fill from `eeo` - gender, race (choose the "Asian" option, "Not Hispanic or Latino"
     variant when offered), hispanic no, veteran "not a protected veteran", disability "no".
     If an EEO question has no matching option, choose the decline option.
   - Consents/acknowledgements planned as True/Yes: tick or click Yes. Greenhouse's required
     "I consent to <company> collecting ... my responses to the demographic data surveys"
     checkbox is page-only: tick it when `eeo.mode` is `answer` (the user chose to answer
     those questions), otherwise leave it and `held` in unattended mode.
   - Greenhouse's phone **Country** picker is marked optional in the DOM but Greenhouse
     validates it on Submit ("Select a country") - typing the phone does not set it. Click
     the picker, type `profile_basics.country` ("United States"), wait 1 s, Return; verify
     the flag / "+1" shows. Other optional page-only widgets stay at their default.
   - Greenhouse's required **Location (City)** (`#candidate-location`, city autocomplete) is
     a page-only extra (the user agreed 2026-09-25): type the city from `profile_basics.location`
     and click the "<City>, <State>, United States" option; in unattended mode it still counts
     as unplanned, so run such a plan as prefill.
   - **Plan values that contradict the profile are resolver bugs - do not type them**: a race
     option that is not the profile's (`eeo.race`), a location answer to an employment
     question, a "Yes" to "do you require a work permit / visa support" while the profile
     says no sponsorship. Leave that one question for the user (or use the
     profile's value when it maps to exactly one option) and name it in `reason`.
     Exception: when the plan carries the user's own explanation for the answer (source `you`,
     or a follow-up text in which the user explains their own authorization), it is
     user-authored - fill it as planned. A plan value that is
     not one of the question's options (e.g. "Apple" for a Yes/No) is always a resolver bug.
   - `javascript_tool` redacts values whose key name looks sensitive ("authorized", "token",
     ...): name the keys in your read-back object neutrally (`work_auth`), or confirm those
     from the screenshot.
5. Take a screenshot of the filled form (top). Saving it to disk via `save_to_disk` may
   return no path; if you get a file, `POST /api/jobs/<job_id>/apply/screenshot?kind=filled`
   with the image bytes as the body, otherwise skip the upload and note it in `reason`.
6. **Prefill mode** → `POST result {outcome: prefilled}` with a `reason` that lists page-only
   extras you ticked or left; `form_hash_seen` = `plan.form_hash` when every plan label was
   found unchanged; leave the tab open; continue. Before reporting, sanity-check that no
  live **required-on-submit** widget is still empty (Greenhouse: Country).
7. **Unattended mode** → guard part 2, then submit (section 4).

## 4. Unattended guard and submit

Submit only if ALL hold:
- Every plan field was found on the page and filled and read back correctly; no required live
  field is unplanned (page-only EEO, the demographic-consent checkbox and privacy-policy
  consents are the allowed extras).
- Live labels/options match the plan's (the form did not change since approval). Compute a
  quick fingerprint of live labels for `form_hash_seen`; if it differs materially from
  `plan.form_hash` (new question, changed options), `held`.
- No login appeared and no interactive captcha is shown; uploads verified.
- `settings` allow unattended (JobScout already downgrades to prefill when disabled).

Then: click Submit → wait 3 s → `get_page_text`. Success text ("Thanks for applying",
"application submitted", confirmation page) → screenshot `kind=confirmation` →
`POST result {outcome: submitted, confirmation: <the success sentence>}`. Validation error
on a field → click into that field, retype the same planned value, submit once more; if it
still errors → `held` with the error text. A captcha challenge appearing after Submit →
`held` "captcha challenge on submit". Anything else unexpected → `held`.

Close the tab after a `submitted` outcome, and also after any `held`/`failed` outcome where
**nothing was filled** (Adzuna job/login pages, expired postings, account walls) - the user's
rule: an unfilled tab is clutter. Leave open only tabs holding a filled form (prefilled, or
held with one field left for the user). Never close the last tab of the MCP group before
creating the next one: the group dissolves and remaining tabs drop out of reach.
**Never navigate a tab that holds a filled form** (prefilled, or held with fields filled) to
the next job - that wipes the user's pending application. Reuse only tabs where nothing was
filled; otherwise `tabs_create_mcp` first.

## Per-ATS notes

- **Greenhouse** (`job-boards.greenhouse.io/embed/job_app?for=<board>&token=<id>`, older
  `boards.greenhouse.io/<board>/jobs/<id>`, careers pages with `gh_jid` = iframe, see step 1):
  standard fields at the top (First/Last/Email, Country + Phone - set Country, see step 4,
  Resume/CV and Cover Letter
  with Attach/Dropbox/Enter manually), custom questions below as text inputs and
  comboboxes, "US Standard Question Set w/ OFCCP" EEO comboboxes (Gender, Race/Ethnicity
  multi-select, Veteran Status) and the demographic-consent checkbox at the bottom, then
  "Submit application". reCAPTCHA Enterprise is loaded invisibly (badge only) - not a
  prefill blocker; in unattended mode expect a possible challenge on submit → held.
- **Ashby** (jobs.ashbyhq.com/<org>/<id>/application): Yes/No are buttons, uploads render the
  file name in the slot, long answers are single-line inputs, EEO radios are page-only,
  success banner reads "Thanks for applying! Your application was successfully submitted."
- **Lever** (jobs.lever.co/<site>/<id>/apply): hCaptcha on submit → prefill only, always.
- **Dice** (dice.com Easy Apply, when the user asks for it by hand - not a planned ATS):
  the flow pre-attaches the Dice profile resume; replace it with `files.resume` (see Files),
  answer only from the plan / profile, and stop before the final "Submit" in prefill mode.
- **Adzuna "fill-time discovery" plans** (no fields): `adzuna.com/land/ad/<id>` redirects to
  **Dice.com**. Dice Easy Apply is a 2-step wizard at `dice.com/job-applications/<uuid>/wizard`
  (navigate the tab there directly - Dice's Apply button opens a tab outside the MCP group):
  step 1 = the Dice **profile** resume + optional cover letter, step 2 = review + Submit.
  **Always replace the resume with the tailored file (`files.resume`)** - the user's rule, no
  matter what Dice shows. Procedure: the only visible `input[type=file]` belongs to the
  **cover letter** (uploading there attaches the resume as a cover letter - if that happens,
  open that slot's "File options" menu → Delete). Open the resume's "File options" (⋯)
  button (a second click is sometimes needed; `read_page` shows a `dialog` with two
  unnamed menuitems = Replace, Delete). Before choosing Replace, stub
  `HTMLInputElement.prototype.click` via `javascript_tool` so Replace does not open the
  native file picker (it would stall the tab); Replace then mounts a dedicated resume
  `input[type=file]` (first in DOM order) - `file_upload` to it, restore the prototype,
  confirm the slot reads "<resume file name> New file" and the cover letter slot is
  empty. Click Next; the review must show Resume / Cover Letter "No cover letter uploaded"
  (unless `files.cover`) / Work Authorization / Current Location. Report `prefilled`
  naming what Dice will send. `adzuna.com/details/<id>` ("EASY APPLY" badge) is Adzuna's own flow
  behind an account wall (an email-alert popup first: "No thanks, take me to the job") →
  `held`.
- **SmartRecruiters** (`jobs.smartrecruiters.com/<Company>/<id>` → "I'm interested" →
  `/oneclick-ui/company/<Company>/publication/<uuid>`, no login): page 1 personal info +
  Resume* + optional "Message to the Hiring Team" (a text cover slot), page 2 `/screening`
  = preliminary questions + privacy declaration + Submit. Inputs are in **shadow DOM**:
  read values by walking `shadowRoot`s, click by screenshot coordinates, and upload the
  resume through a temporary light-DOM `<input type=file>` proxy (`file_upload` to it, then
  copy `.files` into the shadow input under the Resume heading via `DataTransfer` and
  dispatch composed `input`/`change`; skip the first "autofill" dropzone).
  SmartRecruiters **closes an idle form** ("Due to inactivity, the form has been closed") -
  prefilled SR tabs do not wait long; refill from the publication URL when asked. On a
  background tab screenshots time out: set text via the native `value` setter + composed
  `input`/`change` events and click `spl-button` → its shadow `button` by script (validated
  fine on page 1).
- **Oracle Recruiting Cloud** (careers.oracle.com) and **iCIMS** (`*.icims.com`): both gate
  the form behind candidate sign-in (Oracle: email + one-time code; iCIMS: "Privacy Notice and
  Sign-In" email/phone at `/jobs/<id>/<slug>/login`) -> `held`, close the tab. iCIMS shows
  "The requested job could not be found" for closed postings -> `failed`.
- **Background tabs**: when the Chrome window is not showing the tab
  (`document.visibilityState == 'hidden'`), react-select menus do not open and typed text can
  be lost; one `computer screenshot` fronts the tab and fixes it.
- **Workday / others**: account required → not handled here (prefill only, and the plan is
  marked "fill-time discovery"); stop with `held` "account/login required".

## Report back to the user

End with a short table: job, mode, outcome, reason. Remind them which tabs are waiting for
their Submit (prefilled) and which are held and why, and name any page-only extras you
ticked (consents) or set from the profile (Country) so they can check them before submitting.
