"""Resume tailoring pipeline: runs the tailor-resume Claude Code skill headlessly.

States (jobs.resume_state): '' (never) | queued | analyzing | needs_answers | generating
                            | ready | failed
Flow per job (see docs/ARCHITECTURE.md "Resume tailoring"):
    queued -> analyzing -> needs_answers (waits for user) -> queued -> generating -> ready
                        -> generating (no unknowns)                              -> failed
A single worker thread drains the queue one job at a time: runs are serialized so two
generate phases never write resume/skills-inventory.md concurrently, and Max usage stays
predictable.

CLI (for testing without the UI):
    python tailor.py analyze <job_id>          # phase 1 only, prints questions
    python tailor.py generate <job_id>         # phase 2 (unanswered questions = not claimed)
    python tailor.py run <job_id>              # both; skips questions
    python tailor.py answer <job_id> "Q1" ...  # phase 3: application-form answers (long+short)
    python tailor.py cover <job_id>            # phase 4: cover letter (long+short DOCX)
    python tailor.py status <job_id>
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime

import db
import fetchers

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(BASE_DIR, ".claude", "skills", "tailor-resume")

MODELS = [("claude-fable-5-1", "Fable 5.1"), ("claude-opus-5-5", "Opus 5.5"),
          ("claude-sonnet-5", "Sonnet 5"), ("claude-haiku-4-5", "Haiku 4.5")]

ANALYZE_TOOLS = "Read Glob Grep Skill"
GENERATE_TOOLS = "Read Write Edit Glob Grep Skill Bash(node:*) Bash(npm:*)"
ANSWER_TOOLS = "Read Write Glob Grep Skill"          # qa.md only, no inventory edits
def cover_files():
    """(long docx, short docx, markdown) in the job folder; names derive from resume.file_name
    so nothing personal is hard-coded (Jane_Doe.docx -> Jane_Doe_Cover_Letter.docx)."""
    stem = os.path.splitext(settings()["file_name"])[0]
    return (f"{stem}_Cover_Letter.docx", f"{stem}_Cover_Letter_short.docx", "cover-letter.md")


# ---------------------------------------------------------------- settings ----
def settings():
    s = fetchers.load_settings().get("resume", {}) or {}
    out_dir = s.get("output_dir") or os.path.join(BASE_DIR, "resume", "output")
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(BASE_DIR, out_dir)
    return {
        "output_dir": os.path.normpath(out_dir),
        "file_name": s.get("file_name") or "Tailored_Resume.docx",
        "claude_cmd": s.get("claude_cmd") or "claude",
        "model": s.get("model") or MODELS[0][0],
        "timeout_min": int(s.get("timeout_min") or 10),
        "batch_max": int(s.get("batch_max") or 50),
        "parallel": max(1, min(3, int(s.get("parallel") or 2))),
    }


def resolve_claude(cmd):
    """Find the claude executable. The server may have been started from a shell that
    predates the PATH change, so fall back to the native installer's location."""
    found = shutil.which(cmd)
    if found:
        return found
    fallback = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")
    if os.path.exists(fallback):
        return fallback
    return None


def check_claude():
    """Used by Settings 'Test' button and the CLI. Returns dict(ok, version|error)."""
    exe = resolve_claude(settings()["claude_cmd"])
    if not exe:
        return {"ok": False, "error": "claude not found on PATH (install: irm https://claude.ai/install.ps1 | iex)"}
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL).stdout.strip()
        r = _claude(exe, ["-p", "reply with just: ok", "--output-format", "json"], timeout=90)
        if r.get("is_error") or "ok" not in (r.get("result") or "").lower():
            return {"ok": False, "version": v,
                    "error": (r.get("result") or "no reply")[:200] + " (run `claude` and /login?)"}
        return {"ok": True, "version": v, "path": exe}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ paths ----
def slug(s, limit=50):
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) > limit:                      # cut at a word boundary, not mid-word
        s = s[:limit].rsplit("-", 1)[0] if "-" in s[:limit] else s[:limit]
    return s.rstrip("-") or "x"


def job_dir(job):
    """resume/output/<Company>/<Role-slug>/<jobId>/  - the role slug is a shortened title."""
    return os.path.join(settings()["output_dir"], slug(job["company"], 40),
                        slug(job["title"], 50), str(job["id"]))


def write_job_md(job, d):
    """Snapshot of the posting + JobScout's matched skills: the skill's input file."""
    bd = json.loads(job["breakdown"]) if job["breakdown"] else {}
    matched = sorted({t for g in bd.get("skills", {}).get("groups", []) for t in g["terms"]})
    ext = (job["dedupe_key"] or "").split(":", 1)[-1]
    text = (f"Job title: {job['title']}\nCompany: {job['company']}\n"
            f"Location: {job['location']}\nURL: {job['url']}\n"
            f"Source: {job['source']}  (posting id: {ext})\nJobScout id: {job['id']}\n"
            f"Posted: {job['posted_at'] or 'unknown'}\n"
            f"matched_skills: {', '.join(matched)}\n\nJob description:\n{job['description']}\n")
    path = os.path.join(d, "job.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def write_answers_md(job_id, d):
    """Answers file for phase 2. scope=job means framing for this application only;
    scope=global means the skill appends it to the inventory."""
    rows = db.resume_answers(job_id)
    lines = ["# Answers to the analyze questions", "",
             "Each answer is tagged `scope: job-only` (use for this resume only) or `scope: global`",
             "(a true fact about the candidate). JobScout has ALREADY written every global answer into",
             "resume/skills-inventory.md - do NOT edit the inventory in this run (skip step 1 of",
             "the generate phase); just use the answers below as verified facts.", ""]
    for r in rows:
        lines += [f"## {r['skill']}",
                  f"- answer: {r['answer']}",
                  f"- context: {r['context'] or '(none)'}",
                  f"- scope: {'global' if r['scope'] == 'global' else 'job-only'}", ""]
    if not rows:
        lines.append("(no answers given - treat every open requirement as not claimed)")
    path = os.path.join(d, "answers.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path, rows


def rotate_history(d):
    """Move the current resume into history/<timestamp>_vN before regenerating."""
    docx = os.path.join(d, settings()["file_name"])
    if not os.path.exists(docx):
        return
    hist = os.path.join(d, "history")
    os.makedirs(hist, exist_ok=True)
    n = len(os.listdir(hist)) + 1
    dest = os.path.join(hist, f"{datetime.now():%Y-%m-%d_%H%M}_v{n}")
    os.makedirs(dest, exist_ok=True)
    for name in (settings()["file_name"], "resume.md", "notes.md"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            shutil.move(p, os.path.join(dest, name))


# ------------------------------------------------------------- claude -p -----
_tl = threading.local()          # per-worker context: worker id, current task/phase


def _claude(exe, args, timeout, cwd=BASE_DIR):
    """Run claude -p headlessly. stdin is closed (else it waits 3s and warns), stderr is
    ignored unless the exit code is non-zero (it carries unrelated MCP warnings)."""
    p = subprocess.run([exe] + args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                       timeout=timeout)
    if p.returncode != 0 and not p.stdout.strip():
        raise RuntimeError(f"claude exited {p.returncode}: {p.stderr.strip()[-400:]}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude returned non-JSON output: {p.stdout[:300]}")


def _phase(job_id, task, phase, model, exe, args, timeout):
    """Run one claude -p phase with a resume_runs row around it (feeds the pipeline
    panel: durations, averages, ETA) and the worker's live phase marker."""
    worker = getattr(_tl, "worker", None)
    rid = db.start_run(job_id, task, phase, worker, model)
    if worker in state["workers"]:
        state["workers"][worker].update({"phase": phase, "phase_started": db.now_iso()})
    try:
        r = _claude(exe, args, timeout)
        if r.get("is_error"):
            raise RuntimeError(f"{phase} failed: {(r.get('result') or '')[:300]}")
        db.end_run(rid, True)
        return r
    except subprocess.TimeoutExpired:
        db.end_run(rid, False, f"timed out after {timeout // 60} min")
        raise RuntimeError(f"{phase} timed out after {timeout // 60} min")
    except Exception as e:
        db.end_run(rid, False, str(e))
        raise


def _extract_questions(text):
    """The skill is told 'JSON only' but sometimes wraps it in a sentence."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise RuntimeError(f"no JSON in analyze output: {(text or '')[:300]}")
    data = json.loads(m.group(0))
    qs = data.get("questions") or []
    return [{"skill": q.get("skill", "").strip(), "question": q.get("question", "").strip()}
            for q in qs if q.get("skill")]


# ------------------------------------------------------------------ phases ----
def analyze(job_id, log=print):
    job = db.get_job(job_id)
    cfg = settings()
    exe = resolve_claude(cfg["claude_cmd"])
    if not exe:
        raise RuntimeError("claude CLI not found")
    d = job_dir(job)
    os.makedirs(d, exist_ok=True)
    job_md = write_job_md(job, d)
    prior = db.resume_answers(job_id)
    known = ""
    if prior:
        known = ("\n\nAlready answered for THIS application (treat as known, do not re-ask):\n" +
                 "\n".join(f"- {r['skill']}: {r['answer']}" + (f" ({r['context']})" if r['context'] else "")
                           for r in prior))
    db.set_resume(job_id, state="analyzing", error="")
    log(f"[{job_id}] analyze: {job['company']} - {job['title']} ({cfg['model']})")
    r = _phase(job_id, "resume", "analyze", cfg["model"], exe,
               ["-p", f"Use the tailor-resume skill, phase ANALYZE, for the job described "
                      f"in the file {job_md} .{known}\nOutput only the JSON the skill "
                      f"specifies.",
                "--model", cfg["model"], "--allowedTools", ANALYZE_TOOLS,
                "--output-format", "json"], timeout=cfg["timeout_min"] * 60)
    qs = _extract_questions(r.get("result"))
    answered = {a["skill"].lower() for a in prior}
    qs = [q for q in qs if q["skill"].lower() not in answered]
    db.set_resume(job_id, questions=qs, session_id=r.get("session_id"),
                  state="needs_answers" if qs else "queued")
    log(f"[{job_id}] analyze done in {r.get('duration_ms', 0) // 1000}s: {len(qs)} question(s)")
    return qs


def generate(job_id, log=print):
    job = db.get_job(job_id)
    cfg = settings()
    exe = resolve_claude(cfg["claude_cmd"])
    d = job_dir(job)
    os.makedirs(d, exist_ok=True)
    job_md = os.path.join(d, "job.md")
    if not os.path.exists(job_md):
        write_job_md(job, d)
    answers_md, rows = write_answers_md(job_id, d)   # inventory already updated by inventory.py
    work = os.path.join(d, ".work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    out_docx = os.path.join(work, cfg["file_name"])
    out_md = os.path.join(work, "resume.md")
    db.set_resume(job_id, state="generating", error="")
    log(f"[{job_id}] generate: {job['company']} - {job['title']}")
    prompt = (f"Run phase GENERATE of the tailor-resume skill for the job in {job_md} . "
              f"The candidate's answers to the analyze questions are in {answers_md} (scope per answer; "
              f"any analyze question not answered there is simply not claimed). "
              f"Write the tailored markdown to {out_md} and build the DOCX to {out_docx} "
              f"with `node scripts/build_docx.js` (skip --check-pages; LibreOffice is not "
              f"installed). Finish with the short summary as plain text.")
    args = ["-p", prompt, "--model", cfg["model"], "--allowedTools", GENERATE_TOOLS,
            "--output-format", "json"]
    sid = job["resume_session_id"]
    if sid:
        args = ["--resume", sid] + args
    r = _phase(job_id, "resume", "generate", cfg["model"], exe, args,
               timeout=cfg["timeout_min"] * 60)
    if not os.path.exists(out_docx):
        raise RuntimeError(f"generate failed: {(r.get('result') or 'no DOCX produced')[:400]}")
    # success: rotate the previous version, then move the new files into place
    rotate_history(d)
    shutil.move(out_docx, os.path.join(d, cfg["file_name"]))
    if os.path.exists(out_md):
        shutil.move(out_md, os.path.join(d, "resume.md"))
    shutil.rmtree(work, ignore_errors=True)
    summary = (r.get("result") or "").strip()
    with open(os.path.join(d, "notes.md"), "w", encoding="utf-8") as f:
        f.write(f"# {job['company']} - {job['title']}\n\n"
                f"Generated {datetime.now():%Y-%m-%d %H:%M} with {cfg['model']} "
                f"(JobScout job {job_id})\n\n{summary}\n")
    db.set_resume(job_id, state="ready", path=os.path.join(d, cfg["file_name"]),
                  notes=summary, session_id="")
    log(f"[{job_id}] ready in {r.get('duration_ms', 0) // 1000}s -> {d}")
    return d


# -------------------------------------------------- extras: Q&A + cover letter ----
def _extra_state(job, key):
    return json.loads(job[f"resume_{key}"] or "{}")


def _require_resume(job):
    d = os.path.dirname(job["resume_path"] or "")
    if not d or not os.path.exists(os.path.join(d, "resume.md")):
        raise RuntimeError("generate the resume first (needs resume.md + notes.md)")
    return d


def _history_extra(d, names):
    """Move an existing Q&A / cover version into history/ before regenerating."""
    present = [n for n in names if os.path.exists(os.path.join(d, n))]
    if not present:
        return
    dest = os.path.join(d, "history", f"{datetime.now():%Y-%m-%d_%H%M}_{names[0].split('.')[0]}")
    os.makedirs(dest, exist_ok=True)
    for n in present:
        shutil.move(os.path.join(d, n), os.path.join(dest, n))


def _parse_qa(text):
    """qa.md -> [{question, long, short}] (structure fixed in SKILL.md phase 3)."""
    out = []
    for block in re.split(r"^## Q\d+:\s*", text, flags=re.M)[1:]:
        q, _, rest = block.partition("\n")
        m = re.search(r"### Long\s*\n(.*?)\n### Short\s*\n(.*)", rest, re.S)
        out.append({"question": q.strip(),
                    "long": (m.group(1) if m else rest).strip(),
                    "short": (m.group(2) if m else "").strip()})
    return out


def answer(job_id, log=print):
    """Phase 3: draft long+short answers to application-form questions (stored in resume_qa)."""
    job = db.get_job(job_id)
    st = _extra_state(job, "qa")
    cfg = settings()
    d = _require_resume(job)
    qs = st.get("questions") or []
    if not qs:
        raise RuntimeError("no questions given")
    prev = st.get("answers") or []
    lines = ["# Application questions", ""]
    for i, q in enumerate(qs, 1):
        lines.append(f"{i}. {q['text']}" + (f"  (limit: {q['limit']})" if q.get("limit") else ""))
    if st.get("notes"):
        lines += ["", "## personal notes (true, job-only)", st["notes"]]
    if st.get("feedback") and prev:
        lines += ["", "## feedback on the previous draft", st["feedback"], "",
                  "## previous draft"]
        for a in prev:
            lines += [f"### {a['question']}", "Long:", a["long"], "Short:", a["short"], ""]
    with open(os.path.join(d, "qa-input.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    _history_extra(d, ("qa.md",))
    st.update({"state": "running", "error": ""})
    db.set_resume(job_id, qa=st)
    log(f"[{job_id}] answer: {len(qs)} question(s)")
    exe = resolve_claude(cfg["claude_cmd"])
    out = os.path.join(d, "qa.md")
    r = _phase(job_id, "qa", "answer", cfg["model"], exe,
               ["-p", f"Run phase ANSWER of the tailor-resume skill. Job folder: {d} "
                      f"(job.md, resume.md, notes.md, qa-input.md). Write {out} in the "
                      f"exact structure the skill specifies.",
                "--model", cfg["model"], "--allowedTools", ANSWER_TOOLS,
                "--output-format", "json"], timeout=cfg["timeout_min"] * 60)
    if not os.path.exists(out):
        raise RuntimeError(f"answer failed: {(r.get('result') or 'no qa.md written')[:300]}")
    answers = _parse_qa(open(out, encoding="utf-8").read())
    if not answers:
        raise RuntimeError("qa.md could not be parsed")
    st.update({"state": "ready", "answers": answers, "summary": (r.get("result") or "").strip(),
               "updated_at": db.now_iso(), "feedback": ""})
    db.set_resume(job_id, qa=st)
    log(f"[{job_id}] answers ready ({r.get('duration_ms', 0) // 1000}s)")


def cover(job_id, log=print):
    """Phase 4: cover letter, long + short DOCX (stored in resume_cover)."""
    job = db.get_job(job_id)
    st = _extra_state(job, "cover")
    cfg = settings()
    d = _require_resume(job)
    lines = ["# Cover letter input", ""]
    if st.get("notes"):
        lines += ["## personal notes (true, job-only)", st["notes"], ""]
    if st.get("feedback") and st.get("text"):
        lines += ["## feedback on the previous letter", st["feedback"], "",
                  "## previous letter", st["text"]]
    with open(os.path.join(d, "cover-input.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    cf = cover_files()
    _history_extra(d, cf)
    st.update({"state": "running", "error": ""})
    db.set_resume(job_id, cover=st)
    log(f"[{job_id}] cover letter")
    exe = resolve_claude(cfg["claude_cmd"])
    work = os.path.join(d, ".work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    r = _phase(job_id, "cover", "cover", cfg["model"], exe,
               ["-p", f"Run phase COVER of the tailor-resume skill. Job folder: {d} "
                      f"(job.md, resume.md, notes.md, cover-input.md). Write "
                      f"{os.path.join(d, 'cover-letter.md')}; put the per-version markdown "
                      f"in {work} and build the two DOCX files as the skill specifies, to exactly "
                      f"{os.path.join(d, cf[0])} (long) and {os.path.join(d, cf[1])} (short).",
                "--model", cfg["model"], "--allowedTools", GENERATE_TOOLS,
                "--output-format", "json"], timeout=cfg["timeout_min"] * 60)
    docx = os.path.join(d, cf[0])
    if not os.path.exists(docx):
        raise RuntimeError(f"cover failed: {(r.get('result') or 'no DOCX produced')[:300]}")
    shutil.rmtree(work, ignore_errors=True)
    md = os.path.join(d, "cover-letter.md")
    text = open(md, encoding="utf-8").read() if os.path.exists(md) else ""
    st.update({"state": "ready", "text": text, "path": docx,
               "path_short": os.path.join(d, cf[1]) if os.path.exists(os.path.join(d, cf[1])) else "",
               "summary": (r.get("result") or "").strip(), "updated_at": db.now_iso(),
               "feedback": ""})
    db.set_resume(job_id, cover=st)
    log(f"[{job_id}] cover letter ready ({r.get('duration_ms', 0) // 1000}s)")


# ------------------------------------------------------------- worker pool ----
# An ordered queue (so the panel can show/reorder it) drained by N worker threads.
# One task per job at a time (a job's cover letter waits for its resume run); the
# inventory is never written by workers (inventory.py, at answer time), so runs are
# independent of each other.
_cv = threading.Condition()
_queue = []                      # [(job_id, task), ...] in run order
_busy = set()                    # job ids currently being worked on
_paused = threading.Event()
_target = {"n": 0}               # desired worker count (settings.parallel, live-adjustable)
_threads = {}                    # worker id -> Thread
state = {"workers": {}, "message": "", "log": []}


def _log(msg):
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    state["message"] = msg
    state["log"] = (state["log"] + [line])[-200:]


def _run_one(job_id, task="resume"):
    if task in ("qa", "cover"):
        try:
            (answer if task == "qa" else cover)(job_id, _log)
        except Exception as e:
            job = db.get_job(job_id)
            st = _extra_state(job, task)
            st.update({"state": "failed", "error": str(e)[:500]})
            db.set_resume(job_id, **{task: st})
            _log(f"[{job_id}] {task} failed: {e}")
        return
    try:
        job = db.get_job(job_id)
        qs = json.loads(job["resume_questions"] or "[]")
        answered = {a["skill"].lower() for a in db.resume_answers(job_id)}
        pending = [q for q in qs if q["skill"].lower() not in answered]
        if job["resume_session_id"] and not pending:
            generate(job_id, _log)          # questions all answered (or none) -> phase 2
        else:
            if analyze(job_id, _log):
                return                      # waits in needs_answers
            generate(job_id, _log)
    except Exception as e:
        db.set_resume(job_id, state="failed", error=str(e)[:500])
        _log(f"[{job_id}] failed: {e}")


def _take(worker):
    """Next runnable item for this worker, or None (caller waits on _cv)."""
    if _paused.is_set() or worker > _target["n"]:
        return None
    for i, (jid, task) in enumerate(_queue):
        if jid not in _busy:
            _busy.add(jid)
            return _queue.pop(i)
    return None


def _loop(worker):
    _tl.worker = worker
    while True:
        with _cv:
            item = _take(worker)
            while item is None:
                if worker > _target["n"]:            # parallel lowered: retire this worker
                    _threads.pop(worker, None)
                    state["workers"].pop(worker, None)
                    return
                _cv.wait(timeout=2)
                item = _take(worker)
        job_id, task = item
        state["workers"][worker] = {"job_id": job_id, "task": task, "phase": "starting",
                                    "started": db.now_iso(), "phase_started": db.now_iso()}
        try:
            _run_one(job_id, task)
        finally:
            state["workers"][worker] = {"job_id": None}
            with _cv:
                _busy.discard(job_id)
                _cv.notify_all()


def _ensure_workers():
    n = _target["n"] or settings()["parallel"]
    _target["n"] = n
    for w in range(1, n + 1):
        if w not in _threads or not _threads[w].is_alive():
            t = threading.Thread(target=_loop, args=(w,), daemon=True, name=f"tailor-{w}")
            _threads[w] = t
            state["workers"].setdefault(w, {"job_id": None})
            t.start()


def set_parallel(n):
    """Live-adjust the worker count (1-3). Extra workers retire when idle; running
    phases always finish."""
    n = max(1, min(3, int(n)))
    with _cv:
        _target["n"] = n
        _cv.notify_all()
    _ensure_workers()
    return n


def enqueue(job_ids, task="resume"):
    """task: resume (default pipeline) | qa | cover (extras on a job with a ready resume)."""
    _ensure_workers()
    with _cv:
        for jid in job_ids:
            if (jid, task) in _queue:
                continue
            if task == "resume":
                db.set_resume(jid, state="queued", error="")
            else:
                st = _extra_state(db.get_job(jid), task)
                st.update({"state": "queued", "error": ""})
                db.set_resume(jid, **{task: st})
            _queue.append((jid, task))
        _cv.notify_all()


def reorder(job_id, task, front=True):
    with _cv:
        item = (job_id, task)
        if item in _queue:
            _queue.remove(item)
            _queue.insert(0, item) if front else _queue.append(item)
            _cv.notify_all()
            return True
    return False


def dequeue(job_id, task):
    """Remove a waiting item; its state goes back to '' (resume) or is cleared (extras)."""
    with _cv:
        item = (job_id, task)
        if item not in _queue:
            return False
        _queue.remove(item)
    job = db.get_job(job_id)
    if task == "resume":      # a queued regenerate goes back to ready, a first run to blank
        db.set_resume(job_id, state="ready" if job["resume_path"] else "", error="")
    else:
        st = _extra_state(job, task)
        st.update({"state": "ready" if (st.get("answers") or st.get("path")) else "", "error": ""})
        db.set_resume(job_id, **{task: st})
    return True


def pause(flag):
    _paused.set() if flag else _paused.clear()
    with _cv:
        _cv.notify_all()


def recover():
    """On server start: re-queue jobs left 'queued', fail phases cut off mid-run."""
    db.fail_open_runs()
    with db.connect() as con:
        queued = [r["id"] for r in con.execute("SELECT id FROM jobs WHERE resume_state='queued'")]
        stuck = [r["id"] for r in con.execute(
            "SELECT id FROM jobs WHERE resume_state IN ('analyzing','generating')")]
    for jid in stuck:
        db.set_resume(jid, state="failed", error="server restarted mid-run - retry")
    # extras (Q&A / cover) cut off mid-run: keep any earlier result, otherwise mark failed
    extras = []
    with db.connect() as con:
        rows = con.execute("SELECT id, resume_qa, resume_cover FROM jobs "
                           "WHERE resume_qa LIKE '%\"state\": \"running\"%' OR resume_qa LIKE '%\"state\": \"queued\"%' "
                           "OR resume_cover LIKE '%\"state\": \"running\"%' OR resume_cover LIKE '%\"state\": \"queued\"%'").fetchall()
    for r in rows:
        for key in ("qa", "cover"):
            st = json.loads(r[f"resume_{key}"] or "{}")
            if st.get("state") in ("running", "queued"):
                had = bool(st.get("answers") or st.get("path"))
                st.update({"state": "ready" if had else "failed",
                           "error": "" if had else "server restarted mid-run - retry"})
                db.set_resume(r["id"], **{key: st})
                extras.append((r["id"], key))
    if queued:
        enqueue(queued)
    return {"requeued": queued, "failed": stuck, "extras_reset": extras}


def queue_status():
    """Everything the pipeline panel shows: workers, ordered queue, stats, ETA, log."""
    with _cv:
        items = list(_queue)
    stats = db.run_stats()
    avg = stats["avg"]
    per_job = (avg.get("analyze") or 45) + (avg.get("generate") or 150)
    running = [w for w in state["workers"].values() if w.get("job_id")]
    n = max(1, _target["n"] or settings()["parallel"])
    eta = round((len(items) * per_job + len(running) * per_job / 2) / n)
    return {"workers": {str(k): v for k, v in state["workers"].items()},
            "parallel": _target["n"] or settings()["parallel"],
            "queue": [{"job_id": j, "task": t} for j, t in items],
            "queued": len(items), "running": len(running),
            "paused": _paused.is_set(), "message": state["message"],
            "stats": stats, "eta_seconds": eta, "log": state["log"][-50:]}


# --------------------------------------------------------------------- CLI ----
if __name__ == "__main__":
    db.init()
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, jid = sys.argv[1], int(sys.argv[2])
    if cmd == "analyze":
        for q in analyze(jid):
            print(f"  - [{q['skill']}] {q['question']}")
    elif cmd == "generate":
        print(generate(jid))
    elif cmd == "run":
        qs = analyze(jid)
        for q in qs:
            print(f"  (unanswered, not claimed) [{q['skill']}]")
        print(generate(jid))
    elif cmd == "answer":                     # python tailor.py answer <id> "Q1" "Q2 (limit: 150 words)"
        st = _extra_state(db.get_job(jid), "qa")
        st["questions"] = [{"text": q, "limit": ""} for q in sys.argv[3:]] or st.get("questions")
        db.set_resume(jid, qa=st)
        answer(jid)
        for a in _extra_state(db.get_job(jid), "qa")["answers"]:
            print(f"\n## {a['question']}\n--- long ---\n{a['long']}\n--- short ---\n{a['short']}")
    elif cmd == "cover":
        cover(jid)
        print(_extra_state(db.get_job(jid), "cover")["path"])
    elif cmd == "status":
        j = db.get_job(jid)
        print({k: j[k] for k in j.keys() if k.startswith("resume_")})
    elif cmd == "check":
        print(check_claude())
    else:
        sys.exit(__doc__)
