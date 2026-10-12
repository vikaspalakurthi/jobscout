---
name: tailor-resume
description: "Tailor the candidate's resume (name and facts in resume/) to a specific job description and produce a final ATS-safe two-page DOCX. Use whenever a job title, company, and job description are provided and a tailored resume is wanted, or to check a JD against their skills inventory. Runs in two phases: analyze (returns JSON questions about unknown requirements) and generate (updates the skills inventory with global answers and builds the DOCX)."
---

# tailor-resume

Tailor the candidate's resume (resume/master-resume.md) for a specific job posting, truthfully, and build the final DOCX
without relying on chat-only tools.

## Files used by this skill

Personal fact files live OUTSIDE the skill folder, in the project's `resume/` folder (repo root,
gitignored - they hold contact info and career details, and headless Claude Code runs cannot write
under `.claude/`). The skill's own files below are relative to the skill folder.

- `resume/master-resume.md` - the complete master resume: the full verified fact bank of roles,
  bullets, dates, education, certifications, and projects. Structured markdown that
  `scripts/build_docx.js` can render directly.
- `resume/skills-inventory.md` - every tool and technology with status have / familiar / dont-have,
  depth, years, where used, context, and overclaim guards. Append new confirmed entries here in the
  documented format.
- `resume/preferences.md` - length, sections, tone, formatting, and rules that must never change.
- `references/writing-guide.md` - the full mandatory resume writing guide (truth rules, character
  rules, bullet rules, ATS rules, final checklist). Read it before drafting; run its final
  validation checklist before delivering.
- `scripts/build_docx.js` - renders a structured markdown resume or cover letter into the final
  ATS-safe DOCX using the Node `docx` library (run `npm install` once inside the skill folder).
  The script IS the template: there is no separate .docx template file; every layout constant was
  extracted from the candidate's actual reference documents and verified to reproduce them at identical
  text coordinates (Calibri throughout, A4 page, 0.6 in margins, 18pt centered name, optional 11pt
  bold headline line, 12pt section headings with a thin gray bottom rule, 10pt body, italic org
  lines, right-tab bold dates, ASCII hyphen bullets). Do not restyle it; change constants only if
  the user asks.

## Non-negotiable ground rules

1. NEVER invent experience. `master-resume.md` and `skills-inventory.md` are the only sources of
   facts. No new employers, dates, metrics, team sizes, tools, or accomplishments, ever.
2. Anything marked `dont-have` in the inventory is simply not claimed. Not hedged, not implied -
   absent. Anything marked `familiar` may appear only as a familiarity/concepts-level mention.
3. Respect every `overclaim-guard` line in the inventory.
4. Rewording, reordering, selecting, and emphasizing real experience is always allowed and is the
   point of tailoring.
5. Apply `preferences.md` and `references/writing-guide.md` to everything, including cover letters
   and conversational blurbs (ASCII only, keyboard hyphen only, no em dashes anywhere).

## Phase 1: analyze

Input: job title, company, the full job description, and optional `matched_skills` /
`missing_skills` lists provided by the caller.

Steps:
1. Read `resume/skills-inventory.md` and `resume/master-resume.md` (project root).
2. Extract from the JD every required and preferred skill, tool, platform, certification,
   responsibility, and experience threshold (years, industry, compliance).
3. Compare each requirement against the inventory. A requirement is KNOWN if the inventory or
   master resume already answers it (as have, familiar, or dont-have - dont-have is a known
   answer, not a question). A requirement is UNKNOWN only if the inventory is silent or explicitly
   says to ask (e.g. "ASK VIKY", "unconfirmed" where the JD makes it material).
4. Output JSON ONLY - no prose before or after:

```json
{
  "questions": [
    {"skill": "<requirement>", "question": "<specific yes/no or depth question for the candidate>"}
  ]
}
```

An empty list (`{"questions": []}`) means nothing is unknown and generate can proceed. Do not ask
about requirements the inventory already covers, and do not re-ask anything answered in a previous
run and appended to the inventory.

## Phase 2: generate

Input: the same job title, company, and job description, plus the candidate's answers to the analyze
questions, each marked `global` (true fact about him, applies to all future resumes) or `job-only`
(a framing choice for this application only), plus an output path for the DOCX given at run time.

Steps:
1. Update the inventory first: for every `global` answer, append or update an entry in
   `resume/skills-inventory.md` using the documented entry format, in the matching category, including an
   overclaim-guard when the answer has limits. `job-only` answers are used for this resume and NOT
   saved. **Exception:** when the answers file says JobScout has already written the global
   answers, skip this step entirely and never open the inventory for writing (several
   generate runs may be executing in parallel; the app owns that file).
2. Build the tailoring map (per the writing guide, Section 16/18): JD requirement -> verified
   evidence -> resume location.
3. Draft the tailored resume as a markdown file in the master-resume.md structure:
   - Headline line (the bold line under the name) rewritten for the target role, e.g.
     "Senior DevOps Engineer | Jenkins CI/CD, Kubernetes, Terraform, and Platform Engineering".
   - Summary rewritten to lead with the target role's stack.
   - Experience job titles retitled to fit the target role, per the retitling rules in
     `preferences.md` (never "Staff"; employer-specific title and client-name rules live there).
   - Select 3 to 6 of the most relevant bullets per recent role, 1 to 3 for older roles; reword to
     place JD keywords truthfully. Two-page budget: roughly 950 - 1100 words of body text fits two
     pages at this layout.
   - Include INDEPENDENT PROJECTS only when `preferences.md` says the role type warrants it.
   - Technical skills lines reordered so JD-relevant categories come first; familiar-only items
     phrased as "Familiar with ...".
4. Build the DOCX (first time only: `npm install` in the skill folder):

```
node scripts/build_docx.js <tailored.md> <output_path_given_at_run_time> --check-pages
```

   The script aborts on any non-ASCII character and reports the DOCX page count (needs LibreOffice
   for the page check; skip the flag if unavailable and verify length another way). If the count is
   not 2, trim or expand bullet selection and rebuild until it is.
5. Run the writing guide's final validation checklist (truth, characters, writing, ATS, relevance).
6. Output: the DOCX at the given path, plus a short summary (a few sentences) of what was
   emphasized, what was de-emphasized, and which JD requirements are intentionally unaddressed
   because they are dont-have.

## Phase 3: answer (application-form questions)

Input: a job folder containing `job.md` (posting), `resume.md` (the tailored resume that was
actually sent) and `notes.md`, plus `qa-input.md` written by the caller with:
- the questions exactly as the application form states them, each with an optional word or
  character limit;
- optional `personal notes` from the user (true things he wants used, e.g. "used Falcon at Freddie
  Mac") - job-only, never saved to the inventory;
- optional `feedback` on a previous draft plus that previous draft (regenerate case).

Rules: same fact sources (master resume, inventory, resume.md, notes.md, personal notes) and the
full `references/writing-guide.md` + `preferences.md` apply - first person, plain, specific, ASCII
only, no filler ("passionate", "excited to"), no invented anecdotes or enthusiasm. "Why company /
why role" answers draw on what the posting itself says (product, scale, team, stack) and the candidate's
genuine overlaps with it. Never exceed a stated limit.

Output: write `qa.md` at the path the caller gives, in exactly this structure (the app parses it):

```
## Q1: <question text verbatim>
### Long
<answer - fits the stated limit, or ~150 words when none is given>
### Short
<about half the length - for tight forms or as a fallback>

## Q2: ...
```

Then reply with one or two sentences on what each answer leans on. When `feedback` is present,
apply it to every answer it concerns and keep everything else stable.

## Phase 4: cover (cover letter)

Input: the same job folder (`job.md`, `resume.md`, `notes.md`) plus `cover-input.md` with
optional `personal notes` and optional `feedback` + previous letter (regenerate case).

Same fact sources and character rules as above; the letter must agree with `resume.md` (same
titles, same emphasis, same numbers). First person, specific to the company and role, no
fabricated anecdotes or enthusiasm. Produce TWO versions:
- long: about one page (roughly 350 - 400 words), 3 - 4 paragraphs;
- short: roughly 150 - 200 words, for forms with a small text box.

Write `cover-letter.md` at the given path with two sections `## Long` and `## Short`, each in the
cover markdown format below, then build BOTH DOCX files with the same script in cover mode:

```
node scripts/build_docx.js <long.md> <dir>/<Resume_Stem>_Cover_Letter.docx --cover
node scripts/build_docx.js <short.md> <dir>/<Resume_Stem>_Cover_Letter_short.docx --cover
```

(the caller gives the exact DOCX paths - `<Resume_Stem>` is the resume file name without .docx - and says where to put the per-version markdown files). Cover markdown: `# Name`, then
the contact line, then one paragraph per line (date, greeting, body paragraphs, closing, name).
Reply with one or two sentences on what the letter leads with.
