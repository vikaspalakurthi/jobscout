# Quickstart

Fifteen minutes from clone to a scored inbox.

## 1. Start and refresh

```bash
python server.py
```

Open <http://localhost:8765> and click **Refresh**. The first run fetches ~530 boards (8 in
parallel) plus the aggregators; the progress strip under the header shows the phase, `N/M`,
elapsed time and failures. Expect 10-15 minutes. The Report button lists every source with its
kept count or error.

## 2. Look at the inbox

The **Inbox** tab shows every stored posting that passes your location policy, sorted by score.
Each card carries:

- the **score box** - the keyword score (`kw`) until a fit score exists, then the fit with the
  keyword score beneath it;
- location class, work mode, posted / updated dates, salary when the board gives one;
- flag chips (citizenship/clearance, ITAR, no-sponsorship, contract) and matched skill groups;
- after fit scoring: a one-line reason, the gaps, and the thumbs.

Click **Role view** (named after the active preset, e.g. *SRE view*) for the honest
LinkedIn-equivalent cut: home + home state + US-remote, the preset's role labels, direct
boards only, last 30 days.

## 3. Set Home and a role preset

Settings → **Home**: your city, state and the other metros in your state. Settings → **Role
preset**: *SRE / DevOps / Platform* (default) or *QA / SDET / Test automation*. Loading a
preset rescores stored jobs immediately; the fetch rules apply from the next Refresh.

## 4. Act on jobs

Every action button is a toggle: ★ star, 🔖 save, ✓ applied, 👎 not interested, ✕ hide.
Statuses survive every refresh. The detail chevron opens the score breakdown and the full
description.

## 5. Optional: fit scores, tailoring, applying

- With a Claude subscription, fit scoring runs automatically after each refresh for jobs above
  the gate (keyword ≥ 60 by default). Use ▼ / ✓ / ▲ on a fit line to teach it.
- Tick jobs and press **✎ Tailor resumes**; answer the few questions it asks on the Resumes
  tab; the DOCX lands in `resume/output/<Company>/<Role>/<id>/`.
- From a ready resume, **Plan** reads the employer's form; review the plan, approve, queue; then
  `/apply-queue` in a Claude Code session with the Chrome extension fills it.

Continue with [Make it yours](make-it-yours.md).
