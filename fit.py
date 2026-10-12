"""Model fit scoring - a second score beside the keyword score.

The keyword scorer (scoring.py) is a cheap prefilter: it cannot tell "SRE who runs
Kafka" from "Kafka internals expert". fit.py sends batches of gated jobs to a cheap model
through the user's Claude subscription (`claude -p`, no API key) together with the
personal fact files in resume/ and stores fit (0-100), a one-line reason and the gaps.

Learning loop: the user thumbs a score up/down (db.fit_feedback); update_profile() has a
stronger model fold that feedback into resume/fit-profile.md (the judgement rules), bumps
the profile version, and jobs scored under an older version are re-scored on the next pass.
resume/skills-inventory.md (written by inventory.py from tailoring Q&A) is the other input,
so answers given while tailoring also sharpen fit scores.

Worker pattern mirrors tailor.py (condition variable, N daemon threads).
"""
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime

import db
import fetchers
import tailor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESUME_DIR = os.path.join(BASE_DIR, "resume")
PROFILE_PATH = os.path.join(RESUME_DIR, "fit-profile.md")
GATE_LOCS = ("home", "home_state", "remote_us")

DEFAULT_PROFILE = """# Fit profile - judgement rules for scoring jobs (maintained by fit.py, editable)

Scores are 0-100: how well I could do THIS job on day one AND get hired for it.

## Role fit
- Core match (80-95): SRE, DevOps, platform engineering, observability, cloud infrastructure,
  ELK/Kafka platform administration, production/operations engineering.
- Adjacent (55-75): systems engineering, cloud operations, release/build engineering,
  infrastructure-flavoured software roles where operating the platform is most of the job.
- Specialist roles (35-65): a role that is really about ONE technology's internals
  (database engine internals, network fabric, a vendor product) scores on my depth in
  that technology, not on SRE generalism. Broad distributed-systems and relational-database
  concepts are NOT the same as single-engine internals expertise.
- Product development (20-45): writing the product itself (backend/full-stack/data
  engineering/ML) unless operations is explicitly the larger half.

## Seniority
- Senior / Staff / Lead IC titles are fine (10+ years). Manager titles: lower unless hands-on.

## Depth calibration
- See the skills inventory for levels. "familiar" means I can work with it with ramp-up,
  not that I can be the expert.

## Learned adjustments
(none yet - feedback from the Jobs page lands here)
"""

_cv = threading.Condition()
_queue = []                       # job ids waiting, in order
_busy = set()
_target = {"n": 0}
_threads = {}
state = {"workers": {}, "message": "", "log": [], "done": 0, "failed": 0}


def settings():
    s = fetchers.load_settings().get("fit", {}) or {}
    return {
        "enabled": s.get("enabled", True) is not False,
        "model": s.get("model") or "claude-haiku-4-5",
        "profile_model": s.get("profile_model") or "claude-sonnet-5",
        "thinking": s.get("thinking", "full"),          # full | small | off
        "batch": max(1, min(20, int(s.get("batch") or 10))),
        "parallel": max(1, min(3, int(s.get("parallel") or 2))),
        "gate_min_score": int(s.get("gate_min_score") if s.get("gate_min_score") is not None else 50),
        "max_per_run": int(s.get("max_per_run") or 300),
        "auto_profile_after": int(s.get("auto_profile_after") or 5),
        "timeout_min": int(s.get("timeout_min") or 8),
        "description_chars": int(s.get("description_chars") or 1800),
    }


def _log(msg):
    state["message"] = msg
    state["log"] = (state["log"] + [f"{datetime.now():%H:%M:%S}  {msg}"])[-200:]


# ------------------------------------------------------------------ inputs ----
def profile_text():
    if not os.path.exists(PROFILE_PATH):
        os.makedirs(RESUME_DIR, exist_ok=True)
        import scoring
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            f.write(scoring.load_config().get("fit_profile_default") or DEFAULT_PROFILE)
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return f.read()


def profile_version():
    return int(db.get_meta("fit_profile_version") or 1)


def _read(name, limit):
    p = os.path.join(RESUME_DIR, name)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return f.read()[:limit]


def _prompt(jobs):
    cfg = settings()
    return f"""You score job postings for ONE candidate. Output ONLY a JSON array, no prose, no markdown fence.

JUDGEMENT RULES (the candidate's own calibration - follow them over general intuition):
{profile_text()[:5000]}

SKILLS INVENTORY (ground truth of what the candidate knows; the levels matter):
{_read("skills-inventory.md", 7000)}

PREFERENCES (location, work mode, deal-breakers):
{_read("preferences.md", 1500)}

For each job give:
- "fit": integer 0-100 per the rules above. Depth matters: a role that needs expert internals of one
  specific technology scores on the candidate's depth in THAT technology, not on general SRE strength.
- "reason": one concrete sentence naming the deciding match or gap.
- "gaps": up to 3 short phrases of what the job needs that the inventory lacks ([] if none).

Jobs (description truncated to {cfg["description_chars"]} chars):
{json.dumps(jobs, ensure_ascii=False)}

Return exactly: [{{"id": <job id>, "fit": <int>, "reason": "...", "gaps": ["..."]}}, ...] covering every id."""


# ------------------------------------------------------------------ scoring ---
def _claude(prompt, model, timeout, thinking="full"):
    """claude -p with the prompt on stdin (long prompts break the command line on Windows).
    Returns the parsed --output-format json envelope."""
    exe = tailor.resolve_claude(tailor.settings()["claude_cmd"])
    if not exe:
        raise RuntimeError("claude not found on PATH")
    env = dict(os.environ)
    if thinking == "off":
        env["MAX_THINKING_TOKENS"] = "0"
    elif thinking == "small":
        env["MAX_THINKING_TOKENS"] = "2048"
    p = subprocess.run([exe, "-p", "--model", model, "--output-format", "json"],
                       input=prompt, cwd=BASE_DIR, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    if not p.stdout.strip():
        raise RuntimeError(f"claude exited {p.returncode}: {p.stderr.strip()[-400:]}")
    out = json.loads(p.stdout)
    if out.get("is_error"):
        raise RuntimeError(str(out.get("result"))[:400])
    return out


def _parse_scores(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise RuntimeError(f"no JSON array in model output: {text[:200]}")
    out = {}
    for d in json.loads(m.group(0)):
        try:
            out[int(d["id"])] = {"fit": max(0, min(100, int(d["fit"]))),
                                 "reason": str(d.get("reason") or "")[:400],
                                 "gaps": [str(g)[:80] for g in (d.get("gaps") or [])][:3]}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def score_jobs(job_ids, log=_log):
    """Score one batch synchronously. Returns {job_id: {fit, reason, gaps}}."""
    cfg = settings()
    rows = [db.get_job(j) for j in job_ids]
    rows = [r for r in rows if r]
    jobs = [{"id": r["id"], "company": r["company"], "title": r["title"], "location": r["location"],
             "description": re.sub(r"\s+", " ", r["description"] or "")[:cfg["description_chars"]]}
            for r in rows]
    if not jobs:
        return {}
    version = profile_version()
    out = _claude(_prompt(jobs), cfg["model"], cfg["timeout_min"] * 60, cfg["thinking"])
    scores = _parse_scores(out.get("result") or "")
    for r in rows:
        s = scores.get(r["id"])
        if s:
            db.set_fit(r["id"], fit=s["fit"], reason=s["reason"], gaps=s["gaps"], version=version,
                       state="", error="")
        else:
            db.set_fit(r["id"], state="failed", error="model skipped this job")
    log(f"scored {len(scores)}/{len(jobs)} in {out.get('duration_ms', 0) // 1000}s")
    return scores


# ------------------------------------------------------------------ gate ------
def gate_ids(min_score=None, only_ids=None, include_stale=True):
    """Jobs worth a model call: inbox-ish status, not citizen-only, in-policy location,
    keyword score >= gate, and (no fit yet | scored under an older profile)."""
    cfg = settings()
    min_score = cfg["gate_min_score"] if min_score is None else min_score
    ver = profile_version()
    sql = """SELECT id FROM jobs
             WHERE status IN ('new','starred','saved') AND COALESCE(work_auth,'') != 'citizen_only'
               AND loc_class IN ('home','home_state','remote_us') AND score >= ?
               AND COALESCE(fit_state,'') NOT IN ('queued','scoring')"""
    args = [min_score]
    if include_stale:
        sql += " AND (fit IS NULL OR COALESCE(fit_version,0) < ?)"
        args.append(ver)
    else:
        sql += " AND fit IS NULL"
    if only_ids:
        sql += f" AND id IN ({','.join('?' * len(only_ids))})"
        args.extend(only_ids)
    sql += " ORDER BY score DESC"
    with db.connect() as con:
        return [r["id"] for r in con.execute(sql, args)]


# ------------------------------------------------------------------ workers ---
def _take(worker):
    if worker > _target["n"] or not _queue:
        return None
    cfg = settings()
    batch = []
    for jid in list(_queue):
        if jid in _busy:
            continue
        _queue.remove(jid)
        _busy.add(jid)
        batch.append(jid)
        if len(batch) >= cfg["batch"]:
            break
    return batch or None


def _loop(worker):
    while True:
        with _cv:
            batch = _take(worker)
            while batch is None:
                if worker > _target["n"]:
                    _threads.pop(worker, None)
                    state["workers"].pop(worker, None)
                    return
                _cv.wait(timeout=2)
                batch = _take(worker)
        state["workers"][worker] = {"jobs": batch, "started": db.now_iso()}
        for jid in batch:
            db.set_fit(jid, state="scoring")
        try:
            scored = score_jobs(batch)
            state["done"] += len(scored)
            state["failed"] += len(batch) - len(scored)
        except Exception as e:
            state["failed"] += len(batch)
            for jid in batch:
                db.set_fit(jid, state="failed", error=str(e)[:300])
            _log(f"batch failed: {e}")
        finally:
            state["workers"][worker] = {"jobs": []}
            with _cv:
                for jid in batch:
                    _busy.discard(jid)
                _cv.notify_all()


def _ensure_workers():
    n = settings()["parallel"]
    _target["n"] = n
    for w in range(1, n + 1):
        if w not in _threads or not _threads[w].is_alive():
            t = threading.Thread(target=_loop, args=(w,), daemon=True, name=f"fit-{w}")
            _threads[w] = t
            state["workers"].setdefault(w, {"jobs": []})
            t.start()


def enqueue(job_ids):
    _ensure_workers()
    added = []
    with _cv:
        for jid in job_ids:
            if jid in _queue or jid in _busy:
                continue
            db.set_fit(jid, state="queued", error="")
            _queue.append(jid)
            added.append(jid)
        _cv.notify_all()
    if added:
        _log(f"queued {len(added)} job(s)")
    return added


def start(only_ids=None, min_score=None, limit=None):
    """Queue gated jobs (optionally restricted to ids / a different gate). Returns ids queued."""
    cfg = settings()
    ids = gate_ids(min_score=min_score, only_ids=only_ids)[: (limit or cfg["max_per_run"])]
    return enqueue(ids)


def after_refresh(new_ids):
    """Called by the server after a refresh: score the new jobs that pass the gate."""
    if not settings()["enabled"] or not new_ids:
        return {"queued": 0}
    ids = gate_ids(only_ids=list(new_ids), include_stale=False)
    return {"queued": len(enqueue(ids[: settings()["max_per_run"]]))}


def recover():
    """Server start: anything left queued/scoring goes back to unscored."""
    with db.connect() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM jobs WHERE fit_state IN ('queued','scoring')")]
    for jid in ids:
        db.set_fit(jid, state="", error="")
    return ids


def status():
    with _cv:
        queued = len(_queue)
    running = sum(len(w.get("jobs") or []) for w in state["workers"].values())
    cfg = settings()
    per_batch = {"full": 90, "small": 45, "off": 30}.get(cfg["thinking"], 60)
    with db.connect() as con:
        scored = con.execute("SELECT COUNT(*) FROM jobs WHERE fit IS NOT NULL").fetchone()[0]
        stale = con.execute("SELECT COUNT(*) FROM jobs WHERE fit IS NOT NULL AND COALESCE(fit_version,0) < ?",
                            (profile_version(),)).fetchone()[0]
        unapplied = con.execute("SELECT COUNT(*) FROM fit_feedback WHERE applied=0").fetchone()[0]
    return {"queued": queued, "running": running, "done": state["done"], "failed": state["failed"],
            "message": state["message"], "log": state["log"][-30:],
            "eta_seconds": round((queued + running) / cfg["batch"] * per_batch / max(1, cfg["parallel"])),
            "scored": scored, "stale": stale, "gate_candidates": len(gate_ids()),
            "profile_version": profile_version(), "feedback_pending": unapplied,
            "auto_profile_after": cfg["auto_profile_after"], "model": cfg["model"],
            "thinking": cfg["thinking"]}


# ------------------------------------------------------------- learning loop ---
def update_profile(log=_log):
    """Fold unapplied feedback into resume/fit-profile.md with a stronger model. Keeps a
    backup, bumps the profile version (jobs scored before it become 'stale' and are
    re-scored by the next start()). Returns {changed, applied, version}."""
    cfg = settings()
    fb = db.fit_feedback(unapplied_only=True)
    if not fb:
        return {"changed": False, "applied": 0, "version": profile_version(), "note": "no new feedback"}
    current = profile_text()
    items = [{"company": r["company"], "title": r["title"], "model_fit": r["fit"], "model_reason": r["reason"],
              "verdict": r["verdict"], "user_note": r["note"]} for r in fb]
    prompt = f"""You maintain a short markdown file of judgement rules used to score job postings for one
candidate. Rewrite it to incorporate the feedback below. Keep the existing structure and every rule
that is not contradicted; put new or changed calibration under "## Learned adjustments" as concrete,
reusable rules (role pattern -> score band, with the why). Never invent skills the candidate did not
claim; a "too_high" verdict with a note usually means a depth or role-type miscalibration. Keep the
file under 120 lines. Output ONLY the new markdown file, nothing else.

CURRENT FILE:
{current}

SKILLS INVENTORY (for reference, do not copy it in):
{_read("skills-inventory.md", 6000)}

FEEDBACK (verdict is the candidate's judgement of the model's score):
{json.dumps(items, ensure_ascii=False, indent=1)}
"""
    out = _claude(prompt, cfg["profile_model"], cfg["timeout_min"] * 60)
    text = (out.get("result") or "").strip()
    text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text)
    if len(text) < 200 or "## Learned adjustments" not in text:
        raise RuntimeError("profile rewrite looked wrong; file left unchanged")
    shutil.copy(PROFILE_PATH, PROFILE_PATH + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    db.mark_fit_feedback_applied([r["id"] for r in fb])
    ver = profile_version() + 1
    db.set_meta("fit_profile_version", ver)
    log(f"fit profile updated to v{ver} from {len(fb)} feedback item(s)")
    return {"changed": True, "applied": len(fb), "version": ver}


def feedback(job_id, verdict, note=""):
    """Record a verdict; when enough are pending, refresh the profile in the background
    and re-queue the stale jobs."""
    if verdict not in ("too_high", "too_low", "right"):
        raise ValueError("verdict must be too_high | too_low | right")
    db.add_fit_feedback(job_id, verdict, note)
    pending = len(db.fit_feedback(unapplied_only=True))
    auto = settings()["auto_profile_after"]
    if auto and pending >= auto:
        threading.Thread(target=_auto_profile, daemon=True, name="fit-profile").start()
        return {"ok": True, "pending": pending, "profile_update": "started"}
    return {"ok": True, "pending": pending}


def _auto_profile():
    try:
        r = update_profile()
        if r.get("changed"):
            start()                      # re-score what is now stale (within max_per_run)
    except Exception as e:
        _log(f"profile update failed: {e}")


# --------------------------------------------------------------------- CLI ----
if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "score":                                   # fit.py score <id> [<id>...]
        ids = [int(x) for x in sys.argv[2:]]
        for jid, s in score_jobs(ids, print).items():
            print(jid, s["fit"], s["reason"], s["gaps"])
    elif cmd == "gate":
        ids = gate_ids()
        print(f"{len(ids)} job(s) pass the gate (min keyword score {settings()['gate_min_score']})")
    elif cmd == "profile":
        print(update_profile(print))
    else:
        print(json.dumps(status(), indent=1))
