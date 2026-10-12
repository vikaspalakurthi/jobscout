"""Local web server for the job dashboard.  Run:  python server.py"""
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory

import apply as applier
import db
import detect as detector
import fetchers
import fit
import inventory
import notify
import scoring
import tailor

app = Flask(__name__, static_folder="static")


@app.after_request
def no_cache(resp):
    # Local single-user app that changes often: never let the browser cache a stale
    # page or API response (stale index.html made status clicks look broken).
    resp.headers["Cache-Control"] = "no-store"
    return resp

SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0, "smartrecruiters": 0, "workday": 0,
                   "amazon": 0, "oracle": 0, "recruitee": 0, "workable": 0, "bamboohr": 0,
                   "breezy": 0, "oraclehcm": 0, "phenom": 0, "icims": 0, "adzuna": 1, "themuse": 1, "remotive": 2,
                   "remoteok": 2, "hn": 2}
DIRECT_SOURCES = list(fetchers.DIRECT_SOURCES)

PAGE_CAP = 1500          # cards sent to the UI per query, applied AFTER duplicate collapse
# Progress of the running refresh, polled by /api/status. `phase` walks
# boards → aggregators → saving → scoring; done/total count within the phase.
refresh_state = {"running": False, "message": "", "log": [], "phase": "", "done": 0,
                 "total": 0, "failed": 0, "started": None, "trigger": ""}
_refresh_lock = threading.Lock()
_auto_state = {"next": None}          # ISO time of the next scheduled auto-refresh (None = off)


def _do_refresh(only_companies=None):
    try:
        def progress(msg, done=None, total=None, failed=None, phase=None):
            refresh_state["message"] = msg
            refresh_state["log"].append(msg)
            if phase is not None and phase != refresh_state["phase"]:
                refresh_state.update({"phase": phase, "done": 0, "total": 0})
            if done is not None:
                refresh_state["done"] = done
            if total is not None:
                refresh_state["total"] = total
            if failed is not None:
                refresh_state["failed"] = failed

        jobs, stats = fetchers.run_all(progress, only_companies=only_companies)
        progress(f"Saving {len(jobs)} jobs...", phase="saving")
        new_count, new_ids = db.upsert_jobs(jobs)
        progress("Scoring...", phase="scoring")
        scored_count = scoring.rescore_all(db)
        note = notify.after_refresh(new_ids)
        fitq = fit.after_refresh(new_ids)           # model fit scores for the gated new jobs (background)
        summary = {"fetched": len(jobs), "new": new_count, "new_ids": new_ids,
                   "scored": scored_count, "at": db.now_iso(), "notify": note, "fit": fitq}
        if only_companies:
            # partial refresh: merge into the existing report instead of replacing it
            summary["partial"] = ", ".join(only_companies)
            prev = db.get_meta("last_refresh") or {}
            merged = {c["name"]: c for c in prev.get("companies", [])}
            for c in stats.get("companies", []):
                merged[c["name"]] = c
            prev["companies"] = list(merged.values())
            prev["_summary"] = summary
            db.set_meta("last_refresh", prev)
        else:
            stats["_summary"] = summary
            db.set_meta("last_refresh", stats)
        extra = (f" Discord: {note['notified']} sent." if note.get("notified")
                 else f" Discord: {note['error']}" if note.get("error") else "")
        refresh_state["message"] = f"Done: {len(jobs)} fetched, {new_count} new." + extra
    except Exception as e:
        refresh_state["message"] = f"Refresh failed: {e}"
    finally:
        refresh_state.update({"running": False, "phase": "", "started": None})


def start_refresh(only=None, trigger="manual"):
    """Kick off a background refresh. Returns False when one is already running."""
    with _refresh_lock:
        if refresh_state["running"]:
            return False
        refresh_state.update({"running": True, "message": "Starting...", "log": [], "phase": "",
                              "done": 0, "total": 0, "failed": 0, "started": db.now_iso(),
                              "trigger": trigger})
        threading.Thread(target=_do_refresh, args=(only,), daemon=True).start()
    return True


def _auto_refresh_loop():
    """Background scheduler, runs while the server is up. Two modes from settings.refresh:
    `at: ["06:00", "11:00", ...]` fires a full refresh at those PC-local clock times (checked
    once a minute; a slot that passed while the server was down is NOT run late); otherwise
    `every_hours > 0` fires that long after the previous run. Settings are re-read each tick,
    so the Settings page takes effect without a restart."""
    import time
    from datetime import datetime, timedelta
    last = datetime.now()                   # local time; slots are local too
    while True:
        cfg = fetchers.load_settings().get("refresh") or {}
        slots = parse_slots(cfg.get("at"))
        hours = float(cfg.get("every_hours") or 0)
        now = datetime.now()
        if slots:
            today = [now.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in slots]
            due = [t for t in today if last < t <= now]
            upcoming = [t for t in today if t > now] or [today[0] + timedelta(days=1)]
            _auto_state["next"] = min(upcoming).astimezone().isoformat(timespec="minutes")
            if due and start_refresh(trigger="auto"):
                last = now
        elif hours > 0:
            due_at = last + timedelta(hours=hours)
            _auto_state["next"] = due_at.astimezone().isoformat(timespec="minutes")
            if now >= due_at and start_refresh(trigger="auto"):
                last = now
        else:
            _auto_state["next"] = None
            last = now                      # don't fire the moment a schedule is re-enabled
        time.sleep(60)


def _refresh_cfg():
    return fetchers.load_settings().get("refresh") or {}


def parse_slots(value):
    """'06:00, 11:00' or ['6:00', '18:30'] → sorted [(h, m)]; bad entries are skipped."""
    if not value:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    out = set()
    for s in items:
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s))
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            out.add((int(m.group(1)), int(m.group(2))))
    return sorted(out)


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/companies")
def companies_page():
    return send_from_directory("static", "companies.html")


@app.get("/settings")
def settings_page():
    return send_from_directory("static", "settings.html")


@app.get("/about")
def about_page():
    return send_from_directory("static", "about.html")


@app.get("/watchlist")
def watchlist_page():
    return send_from_directory("static", "watchlist.html")


@app.get("/api/watchlist")
def api_watchlist():
    import yaml
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "watchlist.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    swept = {c.lower() for c in (fetchers.load_settings().get("adzuna", {}).get("companies") or [])}
    with db.connect() as con:
        adzuna_counts = {r["company"].lower(): r["c"] for r in con.execute(
            "SELECT company, COUNT(*) c FROM jobs WHERE source='adzuna' GROUP BY company")}
    for c in data.get("companies", []):
        key = c["name"].lower().split(" (")[0].split(" /")[0]
        c["in_sweeps"] = any(key.startswith(s) or s.startswith(key) for s in swept)
        c["adzuna_jobs"] = next((n for comp, n in adzuna_counts.items()
                                 if key and key in comp), 0)
    return jsonify(data)


@app.get("/api/settings")
def api_get_settings():
    s = fetchers.load_settings()
    return jsonify({"adzuna": s.get("adzuna", {}), "filters": s.get("filters", {}),
                    "home": s.get("home", {}), "refresh": s.get("refresh", {}),
                    "presets": list_presets(), "active_preset": scoring.load_config().get("preset", {}),
                    "resume": tailor.settings(), "resume_models": tailor.MODELS,
                    "apply": applier.settings(), "notify": notify.settings(),
                    "fit": fit.settings()})


@app.post("/api/settings")
def api_save_settings():
    body = request.json or {}
    patch = {}
    for section in ("adzuna", "filters", "home", "refresh", "resume", "apply", "notify", "fit"):
        if isinstance(body.get(section), dict):
            patch[section] = body[section]
    if not patch:
        return jsonify({"ok": False, "error": "nothing to save"}), 400
    fetchers.save_local_settings(patch)
    return jsonify({"ok": True})


@app.post("/api/settings/test_notify")
def api_test_notify():
    body = request.json or {}
    try:
        n = notify.send_test((body.get("discord_webhook_url") or "").strip() or None)
        return jsonify({"ok": True, "sample": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 400


@app.post("/api/settings/test_adzuna")
def api_test_adzuna():
    body = request.json or {}
    az = fetchers.load_settings().get("adzuna", {})
    app_id = body.get("app_id") or az.get("app_id", "")
    app_key = body.get("app_key") or az.get("app_key", "")
    if not app_id or not app_key:
        return jsonify({"ok": False, "error": "app_id and app_key are required"})
    try:
        r = requests.get(
            f"https://api.adzuna.com/v1/api/jobs/{az.get('country', 'us')}/search/1",
            params={"app_id": app_id, "app_key": app_key, "what": "devops",
                    "results_per_page": 1, "content-type": "application/json"},
            timeout=20)
        if r.status_code == 200:
            return jsonify({"ok": True, "count": r.json().get("count", 0)})
        return jsonify({"ok": False, "error": f"HTTP {r.status_code} - key rejected or quota exceeded"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/settings/test_claude")
def api_test_claude():
    return jsonify(tailor.check_claude())


# ---- resume tailoring (tailor.py) -------------------------------------------------
@app.post("/api/resume/start")
def api_resume_start():
    """Queue tailoring for a list of job ids. Skips citizen-only, applied (unless
    include_applied) and already-ready jobs (unless regenerate); caps at batch_max."""
    body = request.json or {}
    ids = [int(x) for x in body.get("job_ids") or [] if str(x).isdigit()]
    include_applied, regenerate = bool(body.get("include_applied")), bool(body.get("regenerate"))
    cfg = tailor.settings()
    queued, skipped = [], {"citizen_only": 0, "applied": 0, "already_ready": 0,
                           "in_progress": 0, "batch_limit": 0}
    with db.connect() as con:
        for jid in ids:
            j = con.execute("SELECT status, work_auth, resume_state FROM jobs WHERE id=?",
                            (jid,)).fetchone()
            if not j:
                continue
            if (j["work_auth"] or "") == "citizen_only":
                skipped["citizen_only"] += 1
            elif j["status"] == "applied" and not include_applied:
                skipped["applied"] += 1
            elif j["resume_state"] in ("queued", "analyzing", "generating"):
                skipped["in_progress"] += 1
            elif j["resume_state"] == "ready" and not regenerate:
                skipped["already_ready"] += 1
            elif len(queued) >= cfg["batch_max"]:
                skipped["batch_limit"] += 1
            else:
                queued.append(jid)
    if queued:
        tailor.enqueue(queued)
    return jsonify({"ok": True, "queued": queued, "skipped": skipped})


@app.get("/api/resume/status")
def api_resume_status():
    """Everything the Resumes tab needs: queue state, per-state job lists, questions."""
    with db.connect() as con:
        rows = con.execute(
            """SELECT id, company, title, url, score, status, applied_at, resume_state, resume_questions,
                      resume_path, resume_error, resume_updated_at, resume_qa, resume_cover,
                      apply_state
               FROM jobs WHERE COALESCE(resume_state,'') != ''
                 AND status NOT IN ('not_interested','hidden')
               ORDER BY resume_updated_at DESC""").fetchall()
        answered = {}
        for a in con.execute("SELECT job_id, skill FROM resume_answers"):
            answered.setdefault(a["job_id"], set()).add(a["skill"].lower())
    runs = db.runs_for_jobs([r["id"] for r in rows])
    jobs = []
    for r in rows:
        d = dict(r)
        qs = json.loads(d.pop("resume_questions") or "[]")
        done = answered.get(d["id"], set())
        d["questions"] = [q for q in qs if q["skill"].lower() not in done] \
            if d["resume_state"] == "needs_answers" else []
        # extras: only their state travels in the status feed (cards fetch the full text)
        for k in ("resume_qa", "resume_cover"):
            d[k + "_state"] = (json.loads(d.pop(k) or "{}")).get("state", "")
        # phase timeline: last run per phase (feeds the per-card timeline)
        last = {}
        for run in runs.get(d["id"], []):
            last[run["phase"]] = run
        d["phases"] = [{"phase": p, "seconds": run["seconds"], "ok": run["ok"],
                        "started": run["started"], "worker": run["worker"]}
                       for p, run in last.items()]
        if d["resume_state"] == "ready" and not d.get("apply_state"):
            d["apply_hint"] = applier.readiness(db.get_job(d["id"]))   # can it enter the apply phase?
        jobs.append(d)
    q = tailor.queue_status()
    names = {r["id"]: f"{r['company']} - {r['title']}" for r in rows}
    for item in q["queue"]:
        item["name"] = names.get(item["job_id"], str(item["job_id"]))
    for w in q["workers"].values():
        if w.get("job_id"):
            w["name"] = names.get(w["job_id"], str(w["job_id"]))
    return jsonify({"queue": q, "jobs": jobs})


@app.post("/api/resume/parallel")
def api_resume_parallel():
    n = tailor.set_parallel((request.json or {}).get("n", 2))
    fetchers.save_local_settings({"resume": {"parallel": n}})
    return jsonify({"ok": True, "parallel": n})


@app.post("/api/resume/queue")
def api_resume_queue():
    """{job_id, task, action: front|back|remove}"""
    body = request.json or {}
    jid, task, action = int(body.get("job_id", 0)), body.get("task", "resume"), body.get("action")
    if action in ("front", "back"):
        ok = tailor.reorder(jid, task, front=(action == "front"))
    elif action == "remove":
        ok = tailor.dequeue(jid, task)
    else:
        return jsonify({"ok": False, "error": "bad action"}), 400
    return jsonify({"ok": ok})


@app.post("/api/resume/answer")
def api_resume_answer():
    """answers: [{job_id, skill, question, answer(have|familiar|dont-have), context, scope}].
    A job whose open questions are all answered is re-queued for generate."""
    body = request.json or {}
    touched, globals_ = set(), {}
    for a in body.get("answers") or []:
        if a.get("answer") not in ("have", "familiar", "dont-have"):
            continue
        jid = int(a["job_id"])
        scope = "global" if a.get("scope") == "global" else "job"
        db.set_resume_answer(jid, a["skill"], a.get("question", ""), a["answer"],
                             (a.get("context") or "").strip(), scope)
        touched.add(jid)
        if scope == "global":
            globals_[a["skill"].lower()] = a       # same skill for several jobs: write once
    # JobScout owns inventory writes (workers run in parallel; the skill never edits it)
    saved = inventory.apply_answers(globals_.values())
    requeued = []
    for jid in touched:
        j = db.get_job(jid)
        if j["resume_state"] != "needs_answers":
            continue
        qs = json.loads(j["resume_questions"] or "[]")
        done = {x["skill"].lower() for x in db.resume_answers(jid)}
        if all(q["skill"].lower() in done for q in qs):
            tailor.enqueue([jid])
            requeued.append(jid)
    return jsonify({"ok": True, "requeued": requeued, "inventory_entries": saved})


@app.post("/api/resume/pause")
def api_resume_pause():
    tailor.pause(bool((request.json or {}).get("paused")))
    return jsonify({"ok": True, "paused": tailor.queue_status()["paused"]})


@app.post("/api/jobs/<int:job_id>/resume/retry")
def api_resume_retry(job_id):
    j = db.get_job(job_id)
    if not j:
        return jsonify({"error": "not found"}), 404
    if (request.json or {}).get("skip_questions"):
        # unanswered questions count as "not claimed": clear them so generate can run
        db.set_resume(job_id, questions=[])
    tailor.enqueue([job_id])
    return jsonify({"ok": True})


@app.post("/api/jobs/<int:job_id>/resume/open")
def api_resume_open(job_id):
    """Local single-user app: open the DOCX / cover letter / folder on this machine."""
    j = db.get_job(job_id)
    path = j["resume_path"] if j else None
    if not path or not os.path.exists(path):
        return jsonify({"ok": False, "error": "no resume file yet"}), 404
    what = (request.json or {}).get("what")
    if what in ("cover", "cover_short"):
        cv = json.loads(j["resume_cover"] or "{}")
        target = cv.get("path_short" if what == "cover_short" else "path") or ""
        if not os.path.exists(target):
            return jsonify({"ok": False, "error": "no cover letter yet"}), 404
    else:
        target = path if what == "file" else os.path.dirname(path)
    os.startfile(target)
    return jsonify({"ok": True})


@app.post("/api/jobs/<int:job_id>/resume/qa")
def api_resume_qa(job_id):
    """Draft (or regenerate with feedback) answers to application-form questions.
    body: {questions:[{text, limit}], notes, feedback}"""
    body = request.json or {}
    j = db.get_job(job_id)
    if not j or not j["resume_path"]:
        return jsonify({"ok": False, "error": "generate the resume first"}), 400
    st = json.loads(j["resume_qa"] or "{}")
    qs = [{"text": (q.get("text") or "").strip(), "limit": (q.get("limit") or "").strip()}
          for q in body.get("questions") or [] if (q.get("text") or "").strip()]
    if not qs:
        return jsonify({"ok": False, "error": "no questions"}), 400
    st.update({"questions": qs, "notes": (body.get("notes") or "").strip(),
               "feedback": (body.get("feedback") or "").strip()})
    db.set_resume(job_id, qa=st)
    tailor.enqueue([job_id], "qa")
    return jsonify({"ok": True})


@app.post("/api/jobs/<int:job_id>/resume/cover")
def api_resume_cover(job_id):
    """Generate (or regenerate with feedback) the cover letter. body: {notes, feedback}"""
    body = request.json or {}
    j = db.get_job(job_id)
    if not j or not j["resume_path"]:
        return jsonify({"ok": False, "error": "generate the resume first"}), 400
    st = json.loads(j["resume_cover"] or "{}")
    st.update({"notes": (body.get("notes") or "").strip(),
               "feedback": (body.get("feedback") or "").strip()})
    db.set_resume(job_id, cover=st)
    tailor.enqueue([job_id], "cover")
    return jsonify({"ok": True})


@app.get("/api/jobs/<int:job_id>/apply/files")
def api_apply_files(job_id):
    """Current tailored files for the filler (poll after POST resume/cover)."""
    j = db.get_job(job_id)
    if not j:
        return jsonify({"error": "no such job"}), 404
    f = applier._job_files(j)
    f["cover_state"] = (json.loads(j["resume_cover"] or "{}")).get("state", "")
    f["resume_state"] = j["resume_state"]
    f["cover_text"] = {}
    md = os.path.join(f["dir"], "cover-letter.md") if f["dir"] else ""
    if md and os.path.exists(md):                  # "## Long" / "## Short" sections
        text = open(md, encoding="utf-8").read()
        for m in re.finditer(r"^##\s*(Long|Short)\s*$\n(.*?)(?=^##\s|\Z)", text, re.M | re.S):
            f["cover_text"][m.group(1).lower()] = m.group(2).strip()
    return jsonify(f)


@app.get("/api/jobs/<int:job_id>/resume/notes")
def api_resume_notes(job_id):
    j = db.get_job(job_id)
    if not j or not j["resume_path"]:
        return jsonify({"notes": ""})
    p = os.path.join(os.path.dirname(j["resume_path"]), "notes.md")
    text = open(p, encoding="utf-8").read() if os.path.exists(p) else (j["resume_notes"] or "")
    return jsonify({"notes": text, "dir": os.path.dirname(j["resume_path"])})


# ---- apply pipeline, stage 1: plans + review (apply.py) ---------------------------
def _ensure_covers():
    """Plans that want a cover letter get one generated (the server bridges apply -> tailor
    so neither module imports the other)."""
    ids = applier.pending_covers()
    if ids:
        tailor.enqueue(ids, "cover")
    return ids


@app.get("/api/apply/status")
def api_apply_status():
    st = applier.status()
    _ensure_covers()
    _ensure_answers()
    return jsonify(st)


@app.post("/api/apply/plan")
def api_apply_plan():
    """Build (or rebuild) application plans for job ids. Synchronous: one HTTP read per job."""
    ids = [int(x) for x in (request.json or {}).get("job_ids") or [] if str(x).isdigit()]
    done, failed, skipped = [], {}, {}
    for jid in ids:
        j = db.get_job(jid)
        why = applier.eligibility(j) if j else "not found"
        if why:                       # ineligible jobs are reported, never given a plan row
            skipped[jid] = why
            continue
        try:
            applier.plan(jid)
            done.append(jid)
        except Exception as e:
            failed[jid] = str(e)[:200]
            db.set_apply(jid, state="failed", error=str(e)[:300])
    covers = _ensure_covers()
    drafts = _ensure_answers()
    return jsonify({"ok": True, "planned": done, "failed": failed, "skipped": skipped,
                    "covers_queued": covers, "answers_queued": drafts})


@app.post("/api/jobs/<int:job_id>/apply/review")
def api_apply_review(job_id):
    """{action: approve|skip|reset|fix, fixes: {field_key: value}}"""
    body = request.json or {}
    try:
        p = applier.review(job_id, body.get("action") or "fix", body.get("fixes") or {},
                           mode=body.get("mode"))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "state": db.get_job(job_id)["apply_state"], "needs": len(p.get("needs", []))})


@app.post("/api/apply/approve")
def api_apply_approve():
    """Bulk approve: only plans with nothing unresolved go through."""
    ids = [int(x) for x in (request.json or {}).get("job_ids") or [] if str(x).isdigit()]
    ok, refused = [], {}
    for jid in ids:
        try:
            applier.review(jid, "approve")
            ok.append(jid)
        except Exception as e:
            refused[jid] = str(e)[:200]
    return jsonify({"ok": True, "approved": ok, "refused": refused})


# ---- apply pipeline, stage 2: the queue the /apply-queue skill drains -------------
@app.post("/api/apply/enqueue")
def api_apply_enqueue():
    ids = [int(x) for x in (request.json or {}).get("job_ids") or [] if str(x).isdigit()]
    return jsonify({"ok": True, **applier.enqueue(ids)})


@app.post("/api/jobs/<int:job_id>/apply/dequeue")
def api_apply_dequeue(job_id):
    return jsonify({"ok": applier.dequeue(job_id)})


@app.get("/api/apply/queue/next")
def api_apply_queue_next():
    """Called by the /apply-queue skill. Locks the next plan (state filling) and returns it,
    or {"done": true}. ?mode=prefill|unattended limits a run; ?runner labels it."""
    item = applier.next_in_queue(runner=request.args.get("runner") or "manual",
                                 mode_filter=request.args.get("mode") or None)
    return jsonify(item or {"done": True})


@app.post("/api/apply/result")
def api_apply_result():
    """{run_id, outcome prefilled|submitted|held|failed, reason, screenshot, form_hash_seen,
    confirmation}"""
    b = request.json or {}
    try:
        r = applier.record_result(int(b["run_id"]), b.get("outcome"), b.get("reason", ""),
                                  b.get("screenshot", ""), b.get("form_hash_seen", ""),
                                  b.get("confirmation", ""))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **r})


@app.post("/api/jobs/<int:job_id>/apply/mark_submitted")
def api_apply_mark_submitted(job_id):
    try:
        applier.mark_submitted(job_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/jobs/<int:job_id>/apply/screenshot")
def api_apply_screenshot(job_id):
    """The skill saves a screenshot into the job folder; body is the PNG. ?kind=filled|held|confirmation"""
    j = db.get_job(job_id)
    d = os.path.dirname(j["resume_path"] or "") if j else ""
    if not d or not os.path.isdir(d):
        return jsonify({"ok": False, "error": "no job folder"}), 400
    kind = request.args.get("kind") or "filled"
    if kind not in ("filled", "held", "confirmation"):
        return jsonify({"ok": False, "error": "bad kind"}), 400
    path = os.path.join(d, f"{kind}.png")
    with open(path, "wb") as f:
        f.write(request.get_data())
    return jsonify({"ok": True, "path": path})


@app.get("/api/jobs/<int:job_id>/apply/screenshot/<kind>")
def api_apply_screenshot_get(job_id, kind):
    j = db.get_job(job_id)
    d = os.path.dirname(j["resume_path"] or "") if j else ""
    path = os.path.join(d, f"{kind}.png") if d else ""
    if kind not in ("filled", "held", "confirmation") or not os.path.exists(path):
        return "", 404
    return send_from_directory(d, f"{kind}.png")


@app.post("/api/jobs/<int:job_id>/apply/draft_answers")
def _draft_answers_for(job_id, only_new=False):
    """Queue the Q&A phase for the plan's open custom questions (required or optional).
    only_new: skip when every open question was already drafted once (no retry loops)."""
    j = db.get_job(job_id)
    p = json.loads(j["apply_plan"] or "{}")
    qs = [{"text": f["label"], "limit": ""} for f in p.get("fields", [])
          if f.get("source") == "answer" and f.get("value") is None]
    if not qs:
        return 0
    st = json.loads(j["resume_qa"] or "{}")
    if st.get("state") in ("queued", "running"):
        return 0
    known = {q["text"] for q in st.get("questions") or []}
    new = [q for q in qs if q["text"] not in known]
    if only_new and not new:
        return 0
    st["questions"] = (st.get("questions") or []) + new
    db.set_resume(job_id, qa=st)
    tailor.enqueue([job_id], "qa")
    return len(qs)


def _ensure_answers():
    """Every open custom question on a planned / needs_input plan gets drafted automatically
    (the user's rule: descriptive questions are answered, optional or not)."""
    out = []
    with db.connect() as con:
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM jobs WHERE apply_state IN ('planned','needs_input')")]
    for jid in ids:
        if _draft_answers_for(jid, only_new=True):
            out.append(jid)
    return out


def api_apply_draft_answers(job_id):
    """Manual trigger: (re)draft the open custom questions on this plan."""
    n = _draft_answers_for(job_id)
    if not n:
        return jsonify({"ok": False, "error": "no open custom questions (or drafting already running)"}), 400
    return jsonify({"ok": True, "queued": n})


@app.post("/api/companies/detect")
def api_companies_detect():
    body = request.json or {}
    name = (body.get("name") or "").strip()
    url = (body.get("url") or "").strip()
    if not name and not url:
        return jsonify({"error": "name or careers URL required"}), 400
    existing = next((c for c in fetchers.load_companies()
                     if name and c["name"].lower() == name.lower()), None)
    if existing:
        return jsonify({"found": True, "already_configured": True,
                        "ats": existing["ats"], "slug": existing["slug"]})
    if url:                                       # fingerprint the careers page (category 2 platforms)
        res = detector.detect_url(url if url.startswith("http") else "https://" + url)
    else:
        res = detector.detect_company(name)      # synchronous; Workday probing is slow
    swept = [c.lower() for c in (fetchers.load_settings().get("adzuna", {}).get("companies") or [])]
    res["in_adzuna_sweeps"] = name.lower() in swept
    return jsonify(res)


COMPANIES_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "config", "companies.yaml")


@app.post("/api/companies/add")
def api_companies_add():
    body = request.json or {}
    name = re.sub(r"[{}\[\]:#,\n]", "", (body.get("name") or "")).strip()
    ats, slug = (body.get("ats") or "").strip(), (body.get("slug") or "").strip()
    if not name or ats not in fetchers.ATS_FETCHERS or not re.fullmatch(r"[A-Za-z0-9_.:/\-]+", slug):
        return jsonify({"ok": False, "error": "invalid name/ats/slug"}), 400
    for c in fetchers.load_companies():
        if c["name"].lower() == name.lower() or (c["ats"] == ats and c["slug"] == slug):
            return jsonify({"ok": False, "error": f"{c['name']} is already configured"}), 409
    with open(COMPANIES_YAML, encoding="utf-8") as f:
        original = f.read()
    quoted = f'"{slug}"' if ":" in slug else slug
    line = f"  - {{name: {name}, ats: {ats}, slug: {quoted}}}   # added via app\n"
    try:
        with open(COMPANIES_YAML, "a", encoding="utf-8") as f:
            f.write(line)
        fetchers.load_companies()                # validates the YAML still parses
    except Exception as e:
        with open(COMPANIES_YAML, "w", encoding="utf-8") as f:
            f.write(original)                    # revert on any failure
        return jsonify({"ok": False, "error": f"yaml write failed: {e}"}), 500
    return jsonify({"ok": True, "name": name})


WATCHLIST_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "watchlist.yaml")


@app.post("/api/companies/add_watch")
def api_companies_add_watch():
    """Append a company with no fetchable board to config/watchlist.yaml (same append +
    re-parse + revert pattern as /add). The detect note becomes the platform label."""
    import yaml
    body = request.json or {}
    name = re.sub(r"[{}\[\]:#,\n\"]", "", (body.get("name") or "")).strip()
    url = (body.get("url") or "").strip()
    platform = re.sub(r"[{}\[\]#\n\"]", "", (body.get("platform") or "")).strip()[:80] or "unknown"
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    if url and not re.fullmatch(r"https?://[^\s\"']+", url):
        return jsonify({"ok": False, "error": "careers URL must be http(s)"}), 400
    with open(WATCHLIST_YAML, encoding="utf-8") as f:
        original = f.read()
    existing = (yaml.safe_load(original) or {}).get("companies", [])
    if any(c["name"].lower().split(" (")[0] == name.lower() for c in existing):
        return jsonify({"ok": True, "already": True})
    line = (f'  - {{name: {name}, careers: "{url}", platform: "{platform}", austin: false}}'
            f"   # added via app {datetime.now(timezone.utc):%Y-%m-%d}\n")
    try:
        # the file has a second top-level key (`boards:`) after `companies:`, so a plain
        # append would land under the wrong list - insert at the end of the companies block
        m = re.search(r"^boards:", original, re.M)
        text = original[:m.start()].rstrip("\n") + "\n" + line + "\n" + original[m.start():] if m \
            else original.rstrip("\n") + "\n" + line
        with open(WATCHLIST_YAML, "w", encoding="utf-8") as f:
            f.write(text)
        parsed = yaml.safe_load(text)            # validates the YAML still parses, and where it landed
        assert any(c["name"] == name for c in parsed.get("companies", [])), "not under companies"
    except Exception as e:
        with open(WATCHLIST_YAML, "w", encoding="utf-8") as f:
            f.write(original)
        return jsonify({"ok": False, "error": f"yaml write failed: {e}"}), 500
    return jsonify({"ok": True, "name": name})


@app.post("/api/companies/add_sweep")
def api_companies_add_sweep():
    name = ((request.json or {}).get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    comps = list(fetchers.load_settings().get("adzuna", {}).get("companies") or [])
    if name.lower() in (c.lower() for c in comps):
        return jsonify({"ok": True, "already": True})
    comps.append(name)
    fetchers.save_local_settings({"adzuna": {"companies": comps}})
    return jsonify({"ok": True})


@app.get("/api/companies")
def api_companies():
    cfg = {c["name"]: c for c in fetchers.load_companies()}
    last = db.get_meta("last_refresh") or {}
    last_by_name = {c["name"]: c for c in last.get("companies", [])}
    with db.connect() as con:
        rows = con.execute(
            """SELECT company,
                      COUNT(*) AS total,
                      SUM(CASE WHEN status IN ('new','starred') THEN 1 ELSE 0 END) AS active,
                      SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) AS applied,
                      MAX(score) AS best_score,
                      MIN(first_seen) AS since
               FROM jobs WHERE company != '' GROUP BY company""").fetchall()
    out = []
    seen = set()
    for r in rows:
        name = r["company"]
        seen.add(name)
        c, ls = cfg.get(name), last_by_name.get(name)
        out.append({
            "name": name, "total": r["total"], "active": r["active"], "applied": r["applied"],
            "best_score": r["best_score"], "since": (r["since"] or "")[:10],
            "board": f"{c['ats']}:{c['slug']}" if c else "via aggregators",
            "last_kept": ls.get("kept") if ls else None,
            "last_error": ls.get("error") if ls else None,
        })
    for name, c in cfg.items():          # boards that have never produced a job
        if name not in seen:
            ls = last_by_name.get(name)
            out.append({"name": name, "total": 0, "active": 0, "applied": 0, "best_score": None,
                        "since": "", "board": f"{c['ats']}:{c['slug']}",
                        "last_kept": ls.get("kept") if ls else None,
                        "last_error": ls.get("error") if ls else None})
    out.sort(key=lambda x: (-x["total"], x["name"].lower()))
    return jsonify({"companies": out})


@app.post("/api/refresh")
def api_refresh():
    body = request.get_json(silent=True) or {}
    only = None
    if body.get("company"):
        only = [body["company"]]
    elif isinstance(body.get("companies"), list) and body["companies"]:
        only = body["companies"]
    if not start_refresh(only, trigger="company" if only else "manual"):
        return jsonify({"ok": False, "error": "refresh already running"}), 409
    return jsonify({"ok": True})


@app.get("/api/facets")
def api_facets():
    with db.connect() as con:
        companies = [r["company"] for r in con.execute(
            "SELECT DISTINCT company FROM jobs WHERE company != '' ORDER BY company COLLATE NOCASE")]
        roles = [r["role"] for r in con.execute(
            "SELECT DISTINCT role FROM jobs WHERE role IS NOT NULL AND role != '' ORDER BY role")]
        sources = [r["source"] for r in con.execute(
            "SELECT DISTINCT source FROM jobs ORDER BY source")]
    return jsonify({"companies": companies, "roles": roles, "sources": sources})


@app.get("/api/status")
def api_status():
    with db.connect() as con:
        counts = dict(con.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        inbox = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('new','starred','saved') "
            "AND COALESCE(work_auth,'') != 'citizen_only'").fetchone()[0]
        citizen_only = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE COALESCE(work_auth,'') = 'citizen_only' "
            "AND status IN ('new','starred','saved')").fetchone()[0]
        viewed = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE viewed_at IS NOT NULL "
            "AND status NOT IN ('hidden','not_interested')").fetchone()[0]
    return jsonify({
        "refresh": {k: refresh_state[k] for k in
                    ("running", "message", "phase", "done", "total", "failed", "started", "trigger")},
        "home": {"city": fetchers.home()["city"], "state": fetchers.home()["state"],
                 "state_code": fetchers.home()["state_code"]},
        "view": scoring.load_config().get("view") or {},
        "auto_refresh": {"next": _auto_state["next"],
                         "at": ["%02d:%02d" % s for s in parse_slots(_refresh_cfg().get("at"))],
                         "every_hours": _refresh_cfg().get("every_hours") or 0},
        "last_refresh": db.get_meta("last_refresh"),
        "counts": {
            "inbox": inbox,
            "viewed": viewed,
            "starred": counts.get("starred", 0),
            "saved": counts.get("saved", 0),
            "applied": counts.get("applied", 0),
            "not_interested": counts.get("not_interested", 0),
            "hidden": counts.get("hidden", 0),
            "citizen_only": citizen_only,
            "total": sum(counts.values()),
        },
    })


PRESETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "presets")


def list_presets():
    import yaml
    out = []
    for fn in sorted(os.listdir(PRESETS_DIR)) if os.path.isdir(PRESETS_DIR) else []:
        if fn.endswith(".yaml"):
            with open(os.path.join(PRESETS_DIR, fn), encoding="utf-8") as f:
                meta = (yaml.safe_load(f) or {}).get("preset") or {}
            out.append({"id": fn[:-5], "name": meta.get("name", fn[:-5]), "description": meta.get("description", "")})
    return out


@app.get("/api/presets")
def api_presets():
    return jsonify({"presets": list_presets(), "active": scoring.load_config().get("preset", {})})


@app.post("/api/presets/load")
def api_presets_load():
    """Copy config/presets/<id>.yaml over config/skills.yaml (backup kept), then rescore.
    Relevance/search terms apply from the next refresh; existing rows keep their fit scores."""
    pid = re.sub(r"[^a-z0-9\-]", "", ((request.json or {}).get("id") or "").lower())
    src = os.path.join(PRESETS_DIR, pid + ".yaml")
    if not pid or not os.path.exists(src):
        return jsonify({"ok": False, "error": "unknown preset"}), 400
    import shutil
    from datetime import datetime as _dt
    backup = scoring.SKILLS_PATH + f".bak-{_dt.now():%Y%m%d-%H%M%S}"
    shutil.copy(scoring.SKILLS_PATH, backup)
    shutil.copy(src, scoring.SKILLS_PATH)
    n = scoring.rescore_all(db)
    return jsonify({"ok": True, "preset": pid, "backup": os.path.basename(backup), "rescored": n})


@app.post("/api/rescore")
def api_rescore():
    try:
        n = scoring.rescore_all(db)
        return jsonify({"ok": True, "rescored": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ---- model fit scoring (fit.py) ------------------------------------------------------
@app.get("/api/fit/status")
def api_fit_status():
    return jsonify(fit.status())


@app.post("/api/fit/start")
def api_fit_start():
    """Queue fit scoring. {job_ids:[...]} scores those (gate still applies to each),
    otherwise every gated job up to max_per_run; {min_score} overrides the gate; {limit}."""
    body = request.json or {}
    ids = [int(x) for x in body.get("job_ids") or [] if str(x).isdigit()] or None
    ms = body.get("min_score")
    try:
        queued = fit.start(only_ids=ids, min_score=int(ms) if ms is not None else None,
                           limit=int(body["limit"]) if body.get("limit") else None)
        return jsonify({"ok": True, "queued": queued})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/jobs/<int:job_id>/fit/feedback")
def api_fit_feedback(job_id):
    body = request.json or {}
    try:
        return jsonify(fit.feedback(job_id, body.get("verdict", ""), (body.get("note") or "")[:500]))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.get("/api/fit/profile")
def api_fit_profile():
    return jsonify({"text": fit.profile_text(), "version": fit.profile_version(),
                    "feedback": [dict(r) for r in db.fit_feedback()][-50:]})


@app.post("/api/fit/profile")
def api_fit_profile_update():
    """Fold pending feedback into resume/fit-profile.md now (synchronous, one model call),
    then queue the stale jobs for re-scoring."""
    try:
        r = fit.update_profile()
        if r.get("changed"):
            r["requeued"] = len(fit.start())
        return jsonify({"ok": True, **r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


def _inbox_age_clause():
    """Inbox hides postings older than filters.max_age_days_boards (evergreen reqs on direct
    boards); other tabs and explicit date filters are untouched."""
    days = (fetchers.load_settings().get("filters") or {}).get("max_age_days_boards")
    if not days:
        return ""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime("%Y-%m-%d")
    return f" AND (posted_at IS NULL OR posted_at >= '{cutoff}')"


@app.get("/api/jobs")
def api_jobs():
    def multi(name):
        return [x for x in request.args.get(name, "").split(",") if x]

    tab = request.args.get("tab", "inbox")
    q = request.args.get("q", "").strip().lower()
    min_score = float(request.args.get("min_score", 0) or 0)
    sources = multi("source")
    locs = multi("loc")
    modes = multi("mode")
    roles = multi("role")
    days = request.args.get("days", "")
    new_only = request.args.get("new", "") in ("1", "true")
    company = request.args.get("company", "")
    sort = request.args.get("sort", "score")

    # `where`/`params` hold only the user's FILTERS (not the tab clause), so the same
    # clauses can compute per-tab counts under the active filters.
    where, params = [], []
    tab_filters = {
        "inbox": "status IN ('new','starred','saved')" + _inbox_age_clause(),
        "viewed": "viewed_at IS NOT NULL AND status NOT IN ('hidden','not_interested')",
        "starred": "status = 'starred'",
        "saved": "status = 'saved'",
        "applied": "status = 'applied'",
        "not_interested": "status = 'not_interested'",
        "hidden": "status = 'hidden'",
        "all": "1=1",
    }
    if q:
        where.append("(LOWER(title) LIKE ? OR LOWER(company) LIKE ? OR LOWER(description) LIKE ?)")
        params += [f"%{q}%"] * 3
    if min_score:                               # the slider reads the fit score where one exists
        where.append("COALESCE(fit, score) >= ?")
        params.append(min_score)
    if sources:
        clauses = []
        specifics = [s for s in sources if s not in ("direct", "aggregator")]
        if "direct" in sources:
            clauses.append(f"source IN ({','.join('?' * len(DIRECT_SOURCES))})")
            params += DIRECT_SOURCES
        if "aggregator" in sources:
            clauses.append(f"source NOT IN ({','.join('?' * len(DIRECT_SOURCES))})")
            params += DIRECT_SOURCES
        if specifics:
            clauses.append(f"source IN ({','.join('?' * len(specifics))})")
            params += specifics
        where.append("(" + " OR ".join(clauses) + ")")
    auth = request.args.get("auth", "")
    if auth in ("citizen_only", "no_sponsorship"):
        where.append("COALESCE(work_auth,'') = ?")
        params.append(auth)
    elif auth != "all":
        # default: citizenship/clearance-required jobs are excluded (profile work-auth policy)
        where.append("COALESCE(work_auth,'') != 'citizen_only'")
    if locs:
        where.append(f"loc_class IN ({','.join('?' * len(locs))})")
        params += locs
    if modes:
        where.append(f"work_mode IN ({','.join('?' * len(modes))})")
        params += modes
    if roles:
        where.append(f"role IN ({','.join('?' * len(roles))})")
        params += roles
    if company:
        where.append("LOWER(company) LIKE ?")
        params.append(f"%{company.lower()}%")
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime("%Y-%m-%d")
        # days_by=updated reads the employer's last-edit date (falls back to posted_at where unknown)
        col = "COALESCE(updated_at, posted_at)" if request.args.get("days_by") == "updated" else "posted_at"
        where.append(f"{col} >= ?")
        params.append(cutoff)
    if new_only:                               # only what the last refresh inserted
        ids = ((db.get_meta("last_refresh") or {}).get("_summary") or {}).get("new_ids") or [-1]
        where.append(f"id IN ({','.join('?' * len(ids))})")
        params += ids

    orders = {
        # fit-scored jobs first by fit, then the rest by keyword score
        "fit": "CASE WHEN fit IS NULL THEN 1 ELSE 0 END, COALESCE(fit, score) DESC, score DESC, posted_at DESC",
        "score": "score DESC, posted_at DESC",
        "date": "posted_at DESC, score DESC",
        "updated": "COALESCE(updated_at, posted_at) DESC, score DESC",
        "source": "source ASC, score DESC",
        "auth": "CASE COALESCE(work_auth,'') WHEN '' THEN 0 WHEN 'no_sponsorship' THEN 1 "
                "ELSE 2 END, score DESC",
    }
    order = orders.get(sort, orders["score"])
    if tab == "viewed" and sort in ("score", "fit"):
        order = "viewed_at DESC"        # recency is the point of the Viewed tab
    tab_clause = tab_filters.get(tab, tab_filters["inbox"])
    full_where = " AND ".join([tab_clause] + where)
    filter_where = " AND ".join(where) if where else "1=1"
    with db.connect() as con:
        rows = con.execute(
            f"""SELECT id, company, title, location, loc_class, work_mode, role, work_auth,
                department, url, source, posted_at, updated_at, salary_text, employment_type, score,
                breakdown, flags, status, notes, first_seen, viewed_at, applied_at, fingerprint,
                resume_state, resume_path, resume_questions, resume_error, resume_updated_at,
                resume_qa, resume_cover, apply_state,
                fit, fit_reason, fit_gaps, fit_state, fit_version
                FROM jobs WHERE {full_where} ORDER BY {order}""",
            params).fetchall()          # no LIMIT here: duplicates are collapsed below, then capped
        tc = con.execute(
            f"""SELECT {', '.join(f"SUM(CASE WHEN {clause} THEN 1 ELSE 0 END)"
                                  for clause in tab_filters.values())}
                FROM jobs WHERE {filter_where}""", params).fetchone()
        tab_counts = {name: (tc[i] or 0) for i, name in enumerate(tab_filters)}

    # collapse cross-source duplicates, keeping the best source / highest score,
    # while preserving the SQL sort order (first-encounter position wins)
    order_fp, best, dup_counts = [], {}, {}
    for r in rows:
        fp = r["fingerprint"]
        if fp not in best:
            order_fp.append(fp)
            best[fp] = r
            dup_counts[fp] = 1
        else:
            dup_counts[fp] += 1
            cur = best[fp]
            if (SOURCE_PRIORITY.get(r["source"], 3), -r["score"]) < \
               (SOURCE_PRIORITY.get(cur["source"], 3), -cur["score"]):
                best[fp] = r

    out = []
    total = len(order_fp)
    fit_ver = fit.profile_version()
    for fp in order_fp[:PAGE_CAP]:
        d = dict(best[fp])
        d["breakdown"] = json.loads(d["breakdown"]) if d["breakdown"] else {}
        d["flags"] = json.loads(d["flags"]) if d["flags"] else []
        d["dup_count"] = dup_counts[fp] - 1
        d["resume_question_count"] = len(json.loads(d.pop("resume_questions") or "[]")) \
            if d["resume_state"] == "needs_answers" else 0
        d["resume_qa"] = json.loads(d["resume_qa"] or "{}")
        d["resume_cover"] = json.loads(d["resume_cover"] or "{}")
        d["fit_gaps"] = json.loads(d["fit_gaps"] or "[]")
        d["fit_stale"] = d["fit"] is not None and (d["fit_version"] or 0) < fit_ver
        del d["fingerprint"]
        out.append(d)
    return jsonify({"jobs": out, "tab_counts": tab_counts, "total": total,
                    "truncated": total > PAGE_CAP})


@app.get("/api/jobs/<int:job_id>")
def api_job_detail(job_id):
    row = db.get_job(job_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    d = dict(row)
    d["breakdown"] = json.loads(d["breakdown"]) if d["breakdown"] else {}
    d["flags"] = json.loads(d["flags"]) if d["flags"] else []
    return jsonify(d)


@app.post("/api/jobs/<int:job_id>/status")
def api_job_status(job_id):
    status = (request.json or {}).get("status")
    if status not in ("new", "starred", "saved", "applied", "not_interested", "hidden"):
        return jsonify({"error": "bad status"}), 400
    db.set_status(job_id, status)
    if status in ("not_interested", "hidden"):
        _drop_from_pipelines(job_id)
    elif status == "applied":
        _drop_queued_plan(job_id)            # applied by hand: the filler must not apply again
    return jsonify({"ok": True})


def _drop_queued_plan(job_id):
    """A job marked applied outside the filler leaves the apply queue (plan -> skipped,
    note kept). Tailoring state and files are untouched."""
    j = db.get_job(job_id)
    if j and j["apply_state"] in ("planned", "needs_input", "approved", "queued"):
        db.set_apply(job_id, state="skipped", error="marked applied by hand - plan not run")


def _drop_from_pipelines(job_id):
    """A job the user rejected leaves the tailoring queue / open questions and any
    un-run application plan. Files and finished resumes are kept on disk."""
    j = db.get_job(job_id)
    if not j:
        return
    if j["resume_state"] in ("queued", "needs_answers"):
        tailor.dequeue(job_id, "resume")
        db.set_resume(job_id, state="ready" if j["resume_path"] else "", error="", questions=[])
    if j["apply_state"] in ("planned", "needs_input", "approved", "queued"):
        try:
            applier.review(job_id, "skip", {}, None)
        except Exception:
            pass


@app.post("/api/jobs/<int:job_id>/viewed")
def api_job_viewed(job_id):
    db.set_viewed(job_id)
    return jsonify({"ok": True})


@app.post("/api/jobs/<int:job_id>/notes")
def api_job_notes(job_id):
    db.set_notes(job_id, (request.json or {}).get("notes", ""))
    return jsonify({"ok": True})


def serve(port):
    """Listen on BOTH loopbacks. On Windows `localhost` resolves to ::1 first; with only
    127.0.0.1 bound, every browser request burned ~2 s failing over IPv6 before IPv4 answered
    (measured 2,040 ms vs 14 ms per call, 2026-10-03). Werkzeug cannot dual-stack one socket,
    so run one server per address in the same process."""
    from werkzeug.serving import make_server
    servers = []
    for host in ("127.0.0.1", "::1"):
        try:
            servers.append(make_server(host, port, app, threaded=True))
        except OSError as e:
            print(f" * could not bind {host}:{port}: {e}")
    if not servers:
        raise SystemExit(f"port {port} is busy - is another JobScout running?")
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f" * JobScout on http://localhost:{port}  ({', '.join(s.host for s in servers)})")
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    db.init()
    tailor.recover()          # re-queue 'queued' jobs, fail runs cut off by the last shutdown
    fit.recover()             # fit jobs left queued/scoring go back to unscored
    applier.start_auto_planner()   # no-op unless apply.auto_plan is on
    # JOBSCOUT_PORT lets a second instance run beside the user's (e.g. Claude Code preview)
    port = int(os.environ.get("JOBSCOUT_PORT")
               or fetchers.load_settings().get("server", {}).get("port", 8765))
    if not os.environ.get("JOBSCOUT_PORT"):        # only the user's main instance auto-refreshes
        threading.Thread(target=_auto_refresh_loop, daemon=True).start()
    serve(port)
