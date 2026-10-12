"""Apply pipeline, stage 1: application PLANS + review.

Independent of tailor.py: it only reads the job folder (resume/cover paths) and
jobs.resume_state; it owns the jobs.apply_* columns. Nothing here runs a browser.

    plan(job_id)   read the real application form (Greenhouse / Ashby public APIs,
                   Lever apply-page HTML) and map every field -> value from
                   resume/profile.yaml, the job's Q&A answers, or the files in the job
                   folder. Unresolvable required fields are listed in plan["needs"].
    review(...)    approve / skip / reset, with per-field fixes from the review card.

States (jobs.apply_state): '' | planned | needs_input | approved | skipped
                           | (stage 2, filler:) filling | prefilled | held | submitted | failed

Plan shape (jobs.apply_plan, json): see _new_plan(). plan["form_hash"] fingerprints the
form (labels + types + options) so the filler can refuse to submit if the live form differs
from what was approved.
"""
import hashlib
import html as htmllib
import json
import os
import re
from datetime import datetime

import requests
import yaml

import db
import fetchers

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (JobScout apply planner)"}
DECLINE = re.compile(r"don'?t wish|decline|prefer not|not to answer|not specified", re.I)


# ---------------------------------------------------------------- settings ----
def settings():
    s = fetchers.load_settings().get("apply", {}) or {}
    return {"auto_plan": bool(s.get("auto_plan")), "mode": s.get("mode") or "prefill",
            "window": s.get("window") or "06:00-06:30",
            "allowed_ats": s.get("allowed_ats") or ["greenhouse", "ashby"],
            "profile": s.get("profile") or "resume/profile.yaml",
            "unattended_enabled": s.get("unattended_enabled", True) is not False,
            "max_per_run": int(s.get("max_per_run") or 5),
            "pause_seconds": int(s.get("pause_seconds") or 20)}


def load_profile():
    p = settings()["profile"]
    if not os.path.isabs(p):
        p = os.path.join(BASE_DIR, p)
    if not os.path.exists(p):
        raise RuntimeError(f"profile not found: {p} (create resume/profile.yaml)")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ------------------------------------------------------------ form readers ----
def _board_for(job):
    """(ats, slug) from companies.yaml by company name, else from the URL / dedupe key."""
    for c in fetchers.load_companies():
        if c["name"].lower() == (job["company"] or "").lower():
            return c["ats"], str(c["slug"])
    src = job["source"]
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", job["url"] or "")
    if src == "greenhouse" and m:
        return "greenhouse", m.group(1)
    m = re.search(r"ashbyhq\.com/([^/]+)/", job["url"] or "")
    if src == "ashby" and m:
        return "ashby", m.group(1)
    m = re.search(r"lever\.co/([^/]+)/", job["url"] or "")
    if src == "lever" and m:
        return "lever", m.group(1)
    return src, ""


def _ext_id(job):
    return (job["dedupe_key"] or "").split(":", 1)[-1]


def read_greenhouse(board, ext_id):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{ext_id}",
                     params={"questions": "true"}, headers=UA, timeout=30)
    r.raise_for_status()
    d = r.json()
    fields = []
    for q in d.get("questions") or []:
        f = q["fields"][0]
        fields.append({"key": f["name"], "label": q["label"], "type": f["type"],
                       "required": bool(q.get("required")),
                       "options": [v["label"] for v in (f.get("values") or [])]})
    for q in (d.get("demographic_questions") or {}).get("questions") or []:
        fields.append({"key": f"demographic_{q['id']}", "label": q["label"], "type": "eeo",
                       "required": bool(q.get("required")),
                       "options": [a["label"] for a in q.get("answer_options") or []]})
    for c in d.get("compliance") or []:
        for q in c.get("questions") or []:
            f = q["fields"][0]
            fields.append({"key": f["name"], "label": q["label"], "type": "eeo",
                           "required": bool(q.get("required")),
                           "options": [v["label"] for v in (f.get("values") or [])]})
    # Careers pages with ?gh_jid= render the form in a cross-origin iframe the filler cannot
    # see into; the embed URL below is that iframe's own page (standalone, same form).
    page_url = d.get("absolute_url") or ""
    apply_url = page_url if "greenhouse.io" in page_url else         f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={ext_id}"
    return {"apply_url": apply_url, "page_url": page_url, "fields": fields, "captcha": "unknown"}


ASHBY_Q = ("query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) "
           "{ jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, "
           "jobPostingId: $jobPostingId) { id title applicationForm { sections { title "
           "fieldEntries { field isRequired } } } } }")


def read_ashby(slug, ext_id):
    r = requests.post("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting",
                      headers={**UA, "Content-Type": "application/json"}, timeout=30,
                      json={"operationName": "ApiJobPosting", "query": ASHBY_Q,
                            "variables": {"organizationHostedJobsPageName": slug, "jobPostingId": ext_id}})
    r.raise_for_status()
    post = (r.json().get("data") or {}).get("jobPosting") or {}
    if not post:
        raise RuntimeError("Ashby returned no posting (closed?)")
    fields = []
    for sec in (post.get("applicationForm") or {}).get("sections") or []:
        for fe in sec.get("fieldEntries") or []:
            f = fe["field"]
            fields.append({"key": f.get("path"), "label": f.get("title") or "",
                           "type": f.get("type") or "String", "required": bool(fe.get("isRequired")),
                           "options": [o.get("label") for o in (f.get("selectableValues") or [])]})
    return {"apply_url": f"https://jobs.ashbyhq.com/{slug}/{ext_id}/application",
            "fields": fields, "captcha": "none_seen"}


def read_lever(site, ext_id):
    url = f"https://jobs.lever.co/{site}/{ext_id}/apply"
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    h = r.text
    fields = [
        {"key": "name", "label": "Full name", "type": "input_text", "required": True, "options": []},
        {"key": "email", "label": "Email", "type": "input_text", "required": True, "options": []},
        {"key": "phone", "label": "Phone", "type": "input_text", "required": False, "options": []},
        {"key": "location", "label": "Current location", "type": "input_text", "required": False, "options": []},
        {"key": "org", "label": "Current company", "type": "input_text", "required": False, "options": []},
        {"key": "resume", "label": "Resume/CV", "type": "input_file", "required": True, "options": []},
    ]
    if 'name="urls[LinkedIn]"' in h:
        fields.append({"key": "urls[LinkedIn]", "label": "LinkedIn URL", "type": "input_text",
                       "required": False, "options": []})
    # custom question cards: the hidden baseTemplate input holds the question JSON
    for m in re.finditer(r'<input[^>]+name="cards\[([^\]]+)\]\[baseTemplate\]"[^>]*>', h):
        v = re.search(r'value="([^"]*)"', m.group(0))
        if not v:
            continue
        try:
            tpl = json.loads(htmllib.unescape(v.group(1)))
        except json.JSONDecodeError:
            continue
        for i, fq in enumerate(tpl.get("fields") or []):
            fields.append({"key": f"cards[{m.group(1)}][field{i}]", "label": fq.get("text") or "",
                           "type": fq.get("type") or "text", "required": bool(fq.get("required")),
                           "options": [o.get("text") for o in (fq.get("options") or [])]})
    for k in ("gender", "race", "veteran", "disability"):
        if f'name="eeo[{k}]"' in h:
            fields.append({"key": f"eeo[{k}]", "label": k.title(), "type": "eeo", "required": False,
                           "options": []})
    captcha = "hcaptcha" if "h-captcha" in h else ("recaptcha" if "recaptcha" in h.lower() else "none_seen")
    return {"apply_url": url, "fields": fields, "captcha": captcha}


# --------------------------------------------------------------- resolvers ----
def _pick(options, *wanted, default=None):
    """First option whose text matches any wanted regex (case-insensitive)."""
    for w in wanted:
        for o in options:
            if re.search(w, o or "", re.I):
                return o
    return default


def _yes_no(options, yes):
    if not options:
        return "Yes" if yes else "No"
    return _pick(options, r"^yes" if yes else r"^no", default=options[0])


def _month_or_year(label, opts, ym):
    """ym = 'YYYY-MM'. Year-only fields get the year; month pickers get the month name."""
    if not ym:
        return None
    y, m = ym.split("-")
    month = datetime(int(y), int(m), 1).strftime("%B")
    if opts:
        return _pick(opts, rf"^{y}$", rf"^{month}") or y
    return y if "year" in label else f"{month} {y}"


def _salary(pro, job):
    pol = (pro.get("salary") or {})
    rng = job["salary_text"] or ""
    if rng and pol.get("policy") == "negotiable_within_range":
        return f"Negotiable, within the posted range ({rng})", "profile", "salary policy"
    return pol.get("fallback_text") or "Negotiable", "profile", "salary policy (no posted range)"


def _years_option(options, years):
    """Pick the option whose numeric range contains `years` ('6-8 years', '8+', '10 or more')."""
    if years is None:
        return None
    y = float(years)
    best = None
    for o in options:
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(o))]
        if not nums:
            continue
        lo, hi = nums[0], (nums[1] if len(nums) > 1 else None)
        openended = re.search(r"\+|or more|more than|over|greater", str(o).lower())
        if hi is not None and lo <= y <= hi:
            return o
        if hi is None and (openended or "less" in str(o).lower()) and (y >= lo if openended else y < lo):
            best = best or o
    return best


def _match_answer(label, answers, limit=None):
    """Best Q&A answer for a custom question by token overlap of the question text."""
    words = set(re.findall(r"[a-z]{4,}", (label or "").lower()))
    best, score = None, 0
    for a in answers or []:
        aw = set(re.findall(r"[a-z]{4,}", (a.get("question") or "").lower()))
        s = len(words & aw) / max(1, len(words | aw))
        if s > score:
            best, score = a, s
    if best and score >= 0.35:
        # default: the version closest to ~200 words (user wants 150-300) that fits any limit
        def fits(t):
            return not limit or len(t) <= limit
        cands = [t for t in (best.get("long"), best.get("short")) if t and fits(t)] or \
                [best.get("short") or best.get("long")]
        pick = min(cands, key=lambda t: abs(len(t.split()) - 200))
        return pick, f"qa ({int(score*100)}% match)"
    return None, None


def resolve(field, pro, job, answers, files):
    """-> (value, source, note). value None = unresolved."""
    L = (field["label"] or "").lower()
    t = field["type"] or ""
    opts = field.get("options") or []
    eeo = pro.get("eeo") or {}
    con = pro.get("consents") or {}
    decline = _pick(opts, DECLINE.pattern) or "I don't wish to answer"

    if t in ("input_file", "File") or re.search(r"resume|cv\b|cover letter", L):
        if "cover" in L:
            if files.get("cover"):
                return files["cover"], "file", ""
            # the form offers a cover-letter slot: always generate one and attach it
            return None, "cover", "cover letter being generated - attached when ready"
        return (files.get("resume"), "file", "") if files.get("resume") else (None, "file", "resume not ready")
    if t == "eeo" or re.search(r"gender|race|ethnicit|veteran|disabilit|hispanic|pronoun", L):
        if (eeo.get("mode") or "decline") != "answer":
            return decline, "eeo", ""
        key = ("pronouns" if "pronoun" in L else "gender" if "gender" in L
               else "hispanic_latino" if "hispanic" in L and not re.search(r"race|ethnic", L)
               else "race" if re.search(r"race|ethnic", L)
               else "veteran" if "veteran" in L else "disability" if "disabilit" in L else None)
        val = str(eeo.get(key) or "").strip() if key else ""
        if not val:
            return decline, "eeo", f"profile eeo.{key} blank -> declined"
        if not opts:
            return val, "eeo", ""
        # match the form's own wording: exact-ish first, then the meaning of the answer
        pats = {"gender": [r"^male$|^man$"] if val.lower() == "male" else [re.escape(val)],
                "race": [r"^asian\b(?!.*(black|white|hispanic))", r"south asian|indian(?! or alaska)", r"\basian"],
                "hispanic_latino": [r"^no\b", r"not hispanic"] if val.lower().startswith("n") else [r"^yes"],
                "veteran": [r"not a protected veteran|not a veteran|^no"],
                "disability": [r"^no\b|do not have|don'?t have"],
                "pronouns": [r"he/him"]}.get(key, [re.escape(val)])
        picked = _pick(opts, *pats)
        return (picked or decline), "eeo", ("" if picked else f"no option matching '{val}' -> declined")
    if re.search(r"first name", L):
        return pro.get("preferred_name" if "preferred" in L else "first_name"), "profile", ""
    if re.search(r"last name", L):
        return pro.get("last_name"), "profile", ""
    if re.search(r"preferred name", L):
        return pro.get("preferred_name") or pro.get("first_name"), "profile", ""
    if re.search(r"^(full )?name$", L) or L == "name":
        return pro.get("full_name"), "profile", ""
    if "email" in L:
        return pro.get("email"), "profile", ""
    if "phone" in L:
        return pro.get("phone"), "profile", ""
    if "linkedin" in L:
        return pro.get("linkedin"), "profile", ""
    if re.search(r"github", L):
        return pro.get("github") or "", "profile", ""
    if re.search(r"website|portfolio|stackexchange|stack overflow|personal site", L):
        return pro.get("website") or "", "profile", ""
    if re.search(r"zip|postal", L):
        return pro.get("zip"), "profile", ""
    if re.search(r"(which|what) (u\.?s\.? )?state|state (do|of|are)|state you", L):
        return (_pick(opts, pro.get("state_name", "Texas"), r"\btx\b") or pro.get("state_name")), "profile", ""
    if t == "Location" or re.search(r"^location$|current location|where (are you|do you) (located|reside|live)|city", L):
        return pro.get("location"), "profile", ""
    if re.search(r"at least \d{2} years (of age|old)|18\+|over 18|age of majority", L):
        return (True if t == "Boolean" else (_pick(opts, r"^yes") or "Yes")), "profile", "age >= 18"
    if re.search(r"years? of .*experience|experience.*years|\d+\+? years", L) and opts             and not re.search(r"manag|leadership|people|lead|director", L):
        yrs = pro.get("years_experience")
        v = _years_option(opts, yrs)
        if v is None and yrs is not None:      # yes/no phrased as "Do you have 8+ years ...?"
            m = re.search(r"(\d+)\s*\+?\s*(?:or more\s*)?years", L)
            if m:
                v = _yes_no(opts, float(yrs) >= float(m.group(1)))
        if v is None:
            return None, "choice", "pick an option"
        return v, "profile", f"{yrs} years"
    if re.search(r"current(/previous)? employer|most recent employer|current company|employer", L):
        return pro.get("current_employer"), "profile", ""
    if re.search(r"university|school|college|education|degree|major|field of study|graduat", L):
        edu = pro.get("education") or {}
        if re.search(r"degree|level of education|highest", L):
            v = _pick(opts, r"master", r"graduate") if opts else edu.get("degree")
            return (v, "profile", "") if v else (None, "choice", "pick the degree option")
        if re.search(r"major|field|discipline", L):
            return edu.get("field"), "profile", ""
        if re.search(r"graduat|\bend\b|completion", L):
            return _month_or_year(L, opts, edu.get("end")), "profile", "spring 2016"
        if re.search(r"\bstart", L):
            return _month_or_year(L, opts, edu.get("start")), "profile", "fall 2014"
        v = edu.get("school") or pro.get("last_university")
        return (v, "profile", "") if v else (None, "profile", "profile.education blank")
    if re.search(r"sponsor", L):
        # "Will you require sponsorship?" -> No.  (Boolean fields get a bool.)
        needs = bool(pro.get("needs_sponsorship"))
        if t == "Boolean":
            return needs, "profile", "profile: needs_sponsorship"
        return _yes_no(opts, needs), "profile", "profile: needs_sponsorship"
    if re.search(r"time ?zone", L):
        return (_pick(opts, r"central", r"\bct\b|cst|chicago") or "Central"), "profile", "Austin = Central"
    if re.search(r"stay connected|status updates|text message|sms", L):
        return (_pick(opts, r"^yes|agree|consent|opt.?in") or (True if t == "Boolean" else "Yes")), "profile", "consent"
    if re.search(r"authori[sz]ed to work|legally (able|eligible)|work authorization|eligible to work|"
                 r"right to work|permitted to work", L):
        ok = bool(pro.get("work_authorized", True))
        return (ok if t == "Boolean" else _yes_no(opts, ok)), "profile", ""
    if re.search(r"relocat", L):
        ok = bool(pro.get("willing_to_relocate"))
        return (ok if t == "Boolean" else _yes_no(opts, ok)), "profile", ""
    if re.search(r"previously worked|worked (at|for) .* before|former employee|current(ly an?)? .*employee", L):
        return (False if t == "Boolean" else _pick(opts, r"^no") or "No"), "profile", ""
    if re.search(r"how did you (first )?(hear|learn)|where did you (hear|learn|find)|source", L):
        v = _pick(opts, r"careers? (site|page|website)|company website|company site", r"other")
        return (v or pro.get("how_heard")), "profile", ""
    if re.search(r"select all that apply", L) and re.search(r"learn|hear", L):
        v = _pick(opts, r"website|careers", r"other")
        return ([v] if v else None), "profile", ""
    if re.search(r"salary|compensation|pay expectation", L):
        return _salary(pro, job)
    if re.search(r"currently employed|employment status", L):
        emp = bool(pro.get("current_employer"))
        return (emp if t == "Boolean" else _yes_no(opts, emp)), "profile", "current employer on file"
    if re.search(r"start date|available to start|when (can|could) you start|earliest.*start|notice period|availability (date|to start)|availability\s*\??$", L):
        return pro.get("start_availability"), "profile", ""
    if re.search(r"referr", L):
        return pro.get("referral") or "", "profile", ""
    if re.search(r"stay up to date|job alert|newsletter|marketing|similar jobs", L):
        no = not con.get("marketing_emails")
        return (False if t == "Boolean" else (_pick(opts, r"^no") or "No")) if no else \
               (True if t == "Boolean" else (_pick(opts, r"^yes") or "Yes")), "profile", ""
    if re.search(r"\bai\b.*(consent|polic)|consent.*\bai\b|use of ai", L):
        yes = bool(con.get("ai_evaluation", True))
        return (yes if t == "Boolean" else _yes_no(opts, yes)), "profile", "consent"
    if re.search(r"privacy|personal data|acknowledge|consent|agree|terms", L):
        return (True if t == "Boolean" else (_pick(opts, r"acknowledge|confirm|agree|yes") or "Yes")), "profile", "consent"
    # custom free-text question -> Q&A answers (existing) or needs a draft
    if t in ("input_text", "String", "LongText", "text", "textarea") and not opts:
        limit = None
        m = re.search(r"(\d{2,4})\s*(words|characters|chars)", L)
        if m:
            limit = int(m.group(1)) * (6 if m.group(2).startswith("w") else 1)
        v, src = _match_answer(field["label"], answers, limit)
        if v:
            return v, src, ""
        return None, "answer", "needs an answer (draft in Application Q&A)"
    if opts:                                       # a select we don't understand
        return None, "choice", "pick an option"
    return None, "unknown", "unrecognised field"


# ---------------------------------------------------------------- planning ----
def _new_plan(job, ats, slug, form):
    fp = hashlib.sha1(json.dumps([[f["label"], f["type"], f.get("options") or []]
                                  for f in form["fields"]], sort_keys=True).encode()).hexdigest()[:12]
    return {"ats": ats, "board": slug, "external_id": _ext_id(job), "apply_url": form["apply_url"],
            "page_url": form.get("page_url") or form["apply_url"],
            "captcha": form.get("captcha", "unknown"), "form_hash": fp, "fields": [],
            "needs": [], "flags": [], "built_at": db.now_iso(), "approved_at": None,
            "fixes": {}, "mode": settings()["mode"]}


def eligibility(job):
    """Why a job must NOT be planned (None = fine). Applied/submitted jobs and jobs without a
    ready resume are refused: a plan is only meaningful once there is something to send."""
    if job["status"] == "applied":
        return "already applied"
    if (job["apply_state"] or "") == "submitted":
        return "already submitted by the apply pipeline"
    if (job["work_auth"] or "") == "citizen_only":
        return "citizen-only posting"
    if job["resume_state"] != "ready" or not (job["resume_path"] and os.path.exists(job["resume_path"])):
        return "resume not ready - tailor it first"
    return None


ATS_HINT = {   # what to expect before any plan is built (shown on ready rows)
    "greenhouse": ("single-page form, read offline", True),
    "ashby":      ("single-page form, read offline", True),
    "lever":      ("single-page form, captcha: prefill only", True),
    "workday":    ("multi-step, account needed: prefill only", False),
    "smartrecruiters": ("form read at fill time", False),
    "recruitee":  ("form read at fill time", False),
    "workable":   ("form read at fill time", False),
    "bamboohr":   ("form read at fill time", False),
    "breezy":     ("form read at fill time", False),
    "amazon":     ("own portal, account needed: prefill only", False),
    "oracle":     ("own portal, account needed: prefill only", False),
    "adzuna":     ("Adzuna link: Easy Apply needs an Adzuna account - apply on the employer's site", False),
}


def readiness(job):
    """Pre-plan hint for a ready resume: {ok, ats, note}. ok=False means the job cannot enter
    the apply phase at all (applied / citizen-only / no resume)."""
    why = eligibility(job)
    if why:
        return {"ok": False, "ats": job["source"], "note": why}
    ats, _ = _board_for(job)
    note, offline = ATS_HINT.get(ats, ("aggregator posting: apply on the employer's site", False))
    return {"ok": True, "ats": ats, "offline": offline, "note": note,
            "unattended_ok": ats in settings()["allowed_ats"] and ats != "lever"}


def plan(job_id):
    job = db.get_job(job_id)
    if not job:
        raise RuntimeError("job not found")
    why = eligibility(job)
    if why:
        raise RuntimeError(f"not planned: {why}")
    pro = load_profile()
    ats, slug = _board_for(job)
    ext = _ext_id(job)
    prev = json.loads(job["apply_plan"] or "{}")
    fixes = prev.get("fixes") or {}
    if ats == "greenhouse":
        form = read_greenhouse(slug, ext)
    elif ats == "ashby":
        form = read_ashby(slug, ext)
    elif ats == "lever":
        form = read_lever(slug, ext)
    else:
        form = {"apply_url": job["url"], "fields": [], "captcha": "unknown"}
    p = _new_plan(job, ats, slug, form)
    p["fixes"] = fixes
    p["mode"] = prev.get("mode") or p["mode"]          # per-plan choice survives re-plans
    if form.get("captcha") in ("hcaptcha", "recaptcha") or ats not in settings()["allowed_ats"]:
        p["mode"] = "prefill"                          # unattended submit impossible here
    files = {}
    if job["resume_path"] and os.path.exists(job["resume_path"]):
        files["resume"] = job["resume_path"]
    cv = json.loads(job["resume_cover"] or "{}")
    if cv.get("path") and os.path.exists(cv["path"]):
        files["cover"] = cv["path"]
    answers = (json.loads(job["resume_qa"] or "{}")).get("answers") or []
    for f in form["fields"]:
        if f["key"] in fixes:
            val, src, note = fixes[f["key"]], "you", ""
        else:
            val, src, note = resolve(f, pro, job, answers, files)
        optional_open = val is None and not f["required"]
        if optional_open and src in ("choice", "unknown"):
            val, note = "", "optional - left blank"          # never blocks the plan
        elif optional_open and src == "answer":              # optional essay: still worth drafting
            note = "optional - Draft answers writes it, or leave blank"
        entry = {**f, "value": val, "source": src, "note": note}
        p["fields"].append(entry)
        if val is None and not optional_open:
            p["needs"].append({"key": f["key"], "label": f["label"], "why": note})
    p["wants_cover"] = any(f["source"] == "cover" and f["value"] is None for f in p["fields"])
    if ats not in ("greenhouse", "ashby", "lever"):
        p["flags"].append("form not readable offline: fill-time discovery" +
                          (" + account required" if ats == "workday" else ""))
    if job["source"] == "adzuna" and "adzuna.com" in (job["url"] or ""):
        p["flags"].append("Adzuna link: Easy Apply needs an Adzuna account (the filler holds); "
                          "find the same posting on the employer's own site")
    if p["captcha"] in ("hcaptcha", "recaptcha"):
        p["flags"].append(f"{p['captcha']} on the form: prefill mode only")
    state = "needs_input" if p["needs"] else "planned"
    db.set_apply(job_id, state=state, plan=p, error="")
    return p


def review(job_id, action, fixes=None, mode=None):
    """approve | skip | reset | fix, with optional {field_key: value} fixes applied first and an
    optional per-plan execution mode (prefill | unattended)."""
    job = db.get_job(job_id)
    p = json.loads(job["apply_plan"] or "{}")
    if job["status"] == "applied" or job["apply_state"] == "submitted":
        raise RuntimeError("already applied - nothing to review")
    if mode in ("prefill", "unattended"):
        locked = p.get("captcha") in ("hcaptcha", "recaptcha") or p.get("ats") not in settings()["allowed_ats"]
        p["mode"] = "prefill" if locked else mode
    if fixes:
        p.setdefault("fixes", {}).update({k: v for k, v in fixes.items() if v is not None})
        for f in p.get("fields", []):
            if f["key"] in fixes and fixes[f["key"]] is not None:
                f["value"], f["source"], f["note"] = fixes[f["key"]], "you", ""
        p["needs"] = [n for n in p.get("needs", []) if n["key"] not in fixes]
    if action == "approve":
        if p.get("needs"):
            raise RuntimeError("cannot approve: unresolved fields " +
                               ", ".join(n["label"][:30] for n in p["needs"]))
        p["approved_at"] = db.now_iso()
        db.set_apply(job_id, state="approved", plan=p)
    elif action == "skip":
        db.set_apply(job_id, state="skipped", plan=p)
    elif action == "reset":
        p["approved_at"] = None
        db.set_apply(job_id, state="needs_input" if p.get("needs") else "planned", plan=p)
    elif action == "fix":
        # A mode change or value fix on an approved/queued plan keeps its place (the UI's mode
        # selector posts a fix; dropping a queued job back to planned would silently dequeue it).
        keep = job["apply_state"] in ("approved", "queued")
        db.set_apply(job_id, state="needs_input" if p.get("needs") else
                     (job["apply_state"] if keep else "planned"), plan=p)
    else:
        raise RuntimeError("bad action")
    return p


def pending_covers():
    """Job ids whose plan wants a cover letter that isn't generated, queued or running yet.
    The server enqueues the cover task for these (apply.py itself never imports tailor)."""
    with db.connect() as con:
        rows = con.execute("SELECT id, apply_plan, resume_cover FROM jobs "
                           "WHERE apply_state='needs_input'").fetchall()
    out = []
    for r in rows:
        p = json.loads(r["apply_plan"] or "{}")
        cv = json.loads(r["resume_cover"] or "{}")
        if p.get("wants_cover") and cv.get("state") not in ("queued", "running", "ready"):
            out.append(r["id"])
    return out


def refresh_from_qa():
    """A plan waiting on custom-question answers or a cover letter re-plans itself once the
    job's Q&A / cover phase has produced a result newer than the plan."""
    with db.connect() as con:
        rows = con.execute("SELECT id, apply_plan, resume_qa, resume_cover FROM jobs "
                           "WHERE apply_state='needs_input'").fetchall()
    done = []
    for r in rows:
        p = json.loads(r["apply_plan"] or "{}")
        built = p.get("built_at", "")
        qa = json.loads(r["resume_qa"] or "{}")
        cv = json.loads(r["resume_cover"] or "{}")
        qa_new = qa.get("state") == "ready" and (qa.get("updated_at") or "") > built and \
            any(n.get("why", "").startswith("needs an answer") for n in p.get("needs", []))
        cv_new = p.get("wants_cover") and cv.get("state") == "ready" and (cv.get("updated_at") or "") > built
        if not (qa_new or cv_new):
            continue
        try:
            plan(r["id"])
            done.append(r["id"])
        except Exception as e:
            db.set_apply(r["id"], state="failed", error=str(e)[:300])
    return done


def status():
    refresh_from_qa()
    with db.connect() as con:
        rows = con.execute(
            """SELECT id, company, title, url, resume_state, apply_state, apply_plan, apply_error,
                      apply_updated_at FROM jobs WHERE COALESCE(apply_state,'') != ''
               ORDER BY apply_updated_at DESC""").fetchall()
    runs = db.apply_runs_for([r["id"] for r in rows])
    qpos = {jid: i + 1 for i, jid in enumerate(queue_status()["queue"])}
    out = []
    for r in rows:
        d = dict(r)
        p = json.loads(d.pop("apply_plan") or "{}")
        if "adzuna.com" in (d.get("url") or "") and not any("Adzuna link" in f for f in p.get("flags", [])):
            p.setdefault("flags", []).append("Adzuna link: Easy Apply needs an Adzuna account (the filler "
                                             "holds); find the same posting on the employer's own site")
        d["plan"] = p
        d["needs"] = len(p.get("needs") or [])
        d["queue_pos"] = qpos.get(d["id"])
        last = (runs.get(d["id"]) or [None])[-1]
        d["last_run"] = last
        out.append(d)
    return {"settings": settings(), "jobs": out, "queue": queue_status()}


# ------------------------------------------------ stage 2: queue for the filler ----
# JobScout owns the queue (apply_state 'queued', ordered by apply_queued_at). A Claude session
# with Chrome runs the /apply-queue skill, which pulls plans through the API below, fills the
# form in the user's browser and reports the outcome. Nothing in this module opens a browser.

QUEUEABLE = ("approved", "held", "failed", "prefilled")


def enqueue(job_ids):
    """approved (or held/failed/prefilled for a retry) -> queued. Returns {queued, refused}."""
    queued, refused = [], {}
    for jid in job_ids:
        j = db.get_job(jid)
        if not j:
            refused[jid] = "not found"
        elif j["status"] == "applied" or j["apply_state"] == "submitted":
            refused[jid] = "already applied"
        elif j["apply_state"] not in QUEUEABLE:
            refused[jid] = f"state is {j['apply_state'] or 'unplanned'} - approve it first"
        else:
            db.set_apply(jid, state="queued", queued_at=db.now_iso(), error="")
            queued.append(jid)
    return {"queued": queued, "refused": refused}


def dequeue(job_id):
    j = db.get_job(job_id)
    if j and j["apply_state"] == "queued":
        db.set_apply(job_id, state="approved", queued_at=None)
        return True
    return False


def _job_files(job):
    d = os.path.dirname(job["resume_path"] or "")
    cv = json.loads(job["resume_cover"] or "{}")
    return {"dir": d, "resume": job["resume_path"],
            "cover": cv.get("path") if cv.get("path") and os.path.exists(cv["path"]) else None}


def next_in_queue(runner="manual", mode_filter=None):
    """Lock and hand out the next queued plan: state -> filling, apply_runs row opened.
    mode_filter limits a run to 'prefill' or 'unattended' plans (scheduled tasks)."""
    cfg = settings()
    with db.connect() as con:
        rows = con.execute("SELECT id, apply_plan FROM jobs WHERE apply_state='queued' "
                           "ORDER BY apply_queued_at, id").fetchall()
    for r in rows:
        p = json.loads(r["apply_plan"] or "{}")
        mode = p.get("mode") or "prefill"
        if not cfg["unattended_enabled"]:
            mode = "prefill"
        if mode_filter and mode != mode_filter:
            continue
        job = db.get_job(r["id"])
        if job["status"] == "applied":          # applied by hand after queueing: never re-apply
            db.set_apply(job["id"], state="skipped", error="marked applied by hand - plan not run")
            continue
        run_id = db.start_apply_run(job["id"], mode, runner)
        db.set_apply(job["id"], state="filling", run_id=run_id, error="")
        pro = load_profile()
        return {"run_id": run_id, "job_id": job["id"], "company": job["company"],
                "title": job["title"], "mode": mode, "plan": p, "files": _job_files(job),
                "eeo": pro.get("eeo") or {}, "profile_basics": {k: pro.get(k) for k in
                    ("full_name", "first_name", "last_name", "email", "phone", "linkedin",
                     "location", "country", "current_employer")},   # country: phone pickers
                "settings": {"pause_seconds": cfg["pause_seconds"], "max_per_run": cfg["max_per_run"]}}
    return None


def record_result(run_id, outcome, reason="", screenshot="", form_hash_seen="", confirmation=""):
    """Filler reports back: prefilled | submitted | held | failed. submitted also marks the job
    applied and writes applied.md in the job folder."""
    with db.connect() as con:
        run = con.execute("SELECT * FROM apply_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise RuntimeError("unknown run")
    if outcome not in ("prefilled", "submitted", "held", "failed"):
        raise RuntimeError("bad outcome")
    jid = run["job_id"]
    db.end_apply_run(run_id, outcome, reason, screenshot, form_hash_seen)
    db.set_apply(jid, state=outcome, error=reason if outcome in ("held", "failed") else "")
    if outcome == "submitted":
        _mark_submitted(jid, run["mode"], screenshot, confirmation)
    return {"job_id": jid, "state": outcome}


def _mark_submitted(jid, how, screenshot="", confirmation=""):
    job = db.get_job(jid)
    db.set_status(jid, "applied")
    d = os.path.dirname(job["resume_path"] or "")
    if d and os.path.isdir(d):
        with open(os.path.join(d, "applied.md"), "w", encoding="utf-8") as f:
            f.write(f"# Applied\n\nSubmitted {db.now_iso()} ({how}).\n"
                    + (f"Confirmation: {confirmation}\n" if confirmation else "")
                    + (f"Screenshot: {screenshot}\n" if screenshot else ""))
        with open(os.path.join(d, "plan.json"), "w", encoding="utf-8") as f:
            json.dump(json.loads(job["apply_plan"] or "{}"), f, indent=1)


def mark_submitted(job_id):
    """You clicked Submit on a prefilled tab yourself."""
    j = db.get_job(job_id)
    if not j or j["apply_state"] not in ("prefilled", "held", "filling"):
        raise RuntimeError("only a prefilled/held plan can be marked submitted")
    db.set_apply(job_id, state="submitted", error="")
    _mark_submitted(job_id, "prefill - submitted by hand")
    return True


def queue_status():
    with db.connect() as con:
        q = [r["id"] for r in con.execute("SELECT id FROM jobs WHERE apply_state='queued' "
                                           "ORDER BY apply_queued_at, id")]
    last = db.last_apply_run()
    return {"queue": q, "last_run": last, "settings": {k: settings()[k] for k in
            ("unattended_enabled", "max_per_run", "pause_seconds", "window")}}


def auto_plan_once():
    """Plan every job whose resume is ready and that has no apply state yet (only when
    apply.auto_plan is on). Runs from a poller so tailor.py never has to know about us."""
    if not settings()["auto_plan"]:
        return []
    with db.connect() as con:
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM jobs WHERE resume_state='ready' AND COALESCE(apply_state,'')='' "
            "AND status != 'applied' AND COALESCE(work_auth,'') != 'citizen_only'")]
    done = []
    for jid in ids:
        try:
            plan(jid)
            done.append(jid)
        except Exception as e:
            db.set_apply(jid, state="failed", error=str(e)[:300])
    return done


def start_auto_planner(interval=60):
    import threading
    import time

    def loop():
        while True:
            try:
                auto_plan_once()
            except Exception:
                pass
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True, name="apply-autoplan").start()


if __name__ == "__main__":
    import sys
    db.init()
    if len(sys.argv) < 3:
        sys.exit("usage: python apply.py plan|show <job_id>")
    cmd, jid = sys.argv[1], int(sys.argv[2])
    if cmd == "plan":
        p = plan(jid)
    else:
        p = json.loads(db.get_job(jid)["apply_plan"] or "{}")
    print(f"{p.get('ats')} {p.get('apply_url')}  captcha={p.get('captcha')}  hash={p.get('form_hash')}")
    for f in p.get("fields", []):
        v = f["value"]
        v = (os.path.basename(v) if isinstance(v, str) and os.path.sep in v else v)
        print(f"  [{'req' if f['required'] else 'opt'}] {f['label'][:58]:58} = {str(v)[:60]!s:60} <{f['source']}> {f['note']}")
    print("needs:", [n["label"][:40] for n in p.get("needs", [])], "| flags:", p.get("flags"))
