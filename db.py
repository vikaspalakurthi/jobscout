"""SQLite storage for jobs."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "jobs.db")

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT UNIQUE NOT NULL,      -- source:external_id
    fingerprint TEXT,                     -- company|title, for cross-source dupes
    source TEXT NOT NULL,
    company TEXT,
    title TEXT,
    location TEXT,
    loc_class TEXT,
    url TEXT,
    description TEXT,
    posted_at TEXT,
    salary_text TEXT,
    employment_type TEXT,
    score REAL DEFAULT 0,
    breakdown TEXT,                       -- json
    flags TEXT,                           -- json list
    status TEXT DEFAULT 'new',            -- new | starred | applied | hidden
    notes TEXT DEFAULT '',
    first_seen TEXT,
    last_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS resume_runs (      -- one row per claude -p phase run (tailor.py)
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    task TEXT,                                -- resume | qa | cover
    phase TEXT,                               -- analyze | generate | answer | cover
    worker INTEGER,
    model TEXT,
    started TEXT,
    finished TEXT,
    seconds REAL,
    ok INTEGER,                               -- 1 ok, 0 failed, NULL running
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_job ON resume_runs(job_id);
CREATE TABLE IF NOT EXISTS apply_runs (       -- one row per filler attempt on a plan (apply.py stage 2)
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    mode TEXT,                                -- prefill | unattended
    started TEXT,
    finished TEXT,
    outcome TEXT,                             -- prefilled | submitted | held | failed
    reason TEXT,
    screenshot TEXT,                          -- path in the job folder
    form_hash_seen TEXT,
    runner TEXT                               -- session label (manual / scheduled)
);
CREATE INDEX IF NOT EXISTS idx_apply_runs_job ON apply_runs(job_id);
CREATE TABLE IF NOT EXISTS resume_answers (   -- answers to tailor-resume analyze questions
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    skill TEXT NOT NULL,
    question TEXT,
    answer TEXT,                              -- have | familiar | dont-have
    context TEXT DEFAULT '',
    scope TEXT DEFAULT 'job',                 -- job (this application) | global (inventory)
    created_at TEXT,
    UNIQUE(job_id, skill)
);
CREATE TABLE IF NOT EXISTS fit_feedback (     -- user verdicts on model fit scores (fit.py)
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    verdict TEXT NOT NULL,                    -- too_high | too_low | right
    note TEXT DEFAULT '',
    fit INTEGER,                              -- the score being judged
    company TEXT, title TEXT, reason TEXT,    -- snapshot, so the profile update needs no joins
    created_at TEXT,
    applied INTEGER DEFAULT 0                 -- 1 once folded into resume/fit-profile.md
);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        for col in ("work_mode TEXT", "role TEXT", "work_auth TEXT", "department TEXT",
                    "updated_at TEXT",      # employer's last-edit date (Greenhouse/Ashby); posted_at = first published
                    "viewed_at TEXT", "applied_at TEXT",
                    # resume tailoring pipeline (tailor.py)
                    "resume_state TEXT DEFAULT ''", "resume_questions TEXT",
                    "resume_session_id TEXT", "resume_path TEXT", "resume_notes TEXT",
                    "resume_error TEXT", "resume_updated_at TEXT",
                    "resume_qa TEXT", "resume_cover TEXT",    # json blobs (tailor.py extras)
                    # apply pipeline (apply.py) - independent of resume_* columns
                    "apply_state TEXT DEFAULT ''", "apply_plan TEXT", "apply_error TEXT",
                    "apply_updated_at TEXT", "apply_queued_at TEXT", "apply_run_id INTEGER",
                    # model fit scoring (fit.py) - beside, never instead of, the keyword score
                    "fit INTEGER", "fit_reason TEXT", "fit_gaps TEXT", "fit_at TEXT",
                    "fit_version INTEGER", "fit_state TEXT DEFAULT ''", "fit_error TEXT"):
            try:
                con.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # column already exists


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_jobs(jobs):
    """Insert or refresh a batch of normalized job dicts. Returns (new_count, new_ids)."""
    new_count, new_ids = 0, []
    ts = now_iso()
    with _lock, connect() as con:
        for j in jobs:
            cur = con.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (j["dedupe_key"],))
            row = cur.fetchone()
            if row:
                con.execute(
                    """UPDATE jobs SET title=?, location=?, loc_class=?, work_mode=?, url=?,
                       description=?, posted_at=?, updated_at=?, salary_text=?, employment_type=?,
                       department=?, last_seen=? WHERE id=?""",
                    (j["title"], j["location"], j["loc_class"], j.get("work_mode"), j["url"],
                     j["description"], j.get("posted_at"), j.get("updated_at"), j.get("salary_text"),
                     j.get("employment_type"), j.get("department"), ts, row["id"]),
                )
            else:
                con.execute(
                    """INSERT INTO jobs (dedupe_key, fingerprint, source, company, title, location,
                       loc_class, work_mode, url, description, posted_at, updated_at, salary_text,
                       employment_type, department, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (j["dedupe_key"], j["fingerprint"], j["source"], j["company"], j["title"],
                     j["location"], j["loc_class"], j.get("work_mode"), j["url"], j["description"],
                     j.get("posted_at"), j.get("updated_at"), j.get("salary_text"), j.get("employment_type"),
                     j.get("department"), ts, ts),
                )
                new_count += 1
                new_ids.append(con.execute("SELECT last_insert_rowid()").fetchone()[0])
    return new_count, new_ids


def update_scores(scored):
    """scored: (job_id, score, breakdown, flags, loc_class, work_mode, role,
                employment_type, work_auth)"""
    with _lock, connect() as con:
        con.executemany(
            "UPDATE jobs SET score=?, breakdown=?, flags=?, loc_class=?, work_mode=?, role=?, "
            "employment_type=?, work_auth=? WHERE id=?",
            [(s, json.dumps(b), json.dumps(f), lc, wm, role, emp, auth, jid)
             for jid, s, b, f, lc, wm, role, emp, auth in scored],
        )


def all_jobs_for_scoring():
    with connect() as con:
        return con.execute(
            "SELECT id, title, description, location, loc_class, work_mode, employment_type, "
            "source, posted_at FROM jobs"
        ).fetchall()


def set_status(job_id, status):
    with _lock, connect() as con:
        if status == "applied":
            # applying implies having viewed it — stamp both
            ts = now_iso()
            con.execute("UPDATE jobs SET status=?, applied_at=?, "
                        "viewed_at=COALESCE(viewed_at, ?) WHERE id=?",
                        (status, ts, ts, job_id))
        else:
            con.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))


def set_viewed(job_id):
    with _lock, connect() as con:
        con.execute("UPDATE jobs SET viewed_at=? WHERE id=?", (now_iso(), job_id))


def set_notes(job_id, notes):
    with _lock, connect() as con:
        con.execute("UPDATE jobs SET notes=? WHERE id=?", (notes, job_id))


def get_job(job_id):
    with connect() as con:
        return con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def set_resume(job_id, **fields):
    """Update resume_* columns (state, questions, session_id, path, notes, error).
    Never touches user status fields."""
    allowed = {"state", "questions", "session_id", "path", "notes", "error", "qa", "cover"}
    sets, vals = ["resume_updated_at=?"], [now_iso()]
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(k)
        if k in ("questions", "qa", "cover"):
            v = json.dumps(v)
        sets.append(f"resume_{k}=?")
        vals.append(v)
    with _lock, connect() as con:
        con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals + [job_id])


def resume_answers(job_id):
    with connect() as con:
        return con.execute("SELECT * FROM resume_answers WHERE job_id=? ORDER BY id",
                           (job_id,)).fetchall()


# ---- model fit scoring (fit.py) ------------------------------------------------------
def set_fit(job_id, **fields):
    """Update fit_* columns (fit, reason, gaps, version, state, error). gaps is a list."""
    allowed = {"fit", "reason", "gaps", "version", "state", "error"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(k)
        if k == "gaps":
            v = json.dumps(v or [])
        sets.append("fit=?" if k == "fit" else f"fit_{k}=?")
        vals.append(v)
    if "fit" in fields:
        sets.append("fit_at=?")
        vals.append(now_iso())
    with _lock, connect() as con:
        con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals + [job_id])


def add_fit_feedback(job_id, verdict, note=""):
    job = get_job(job_id)
    with _lock, connect() as con:
        con.execute("""INSERT INTO fit_feedback (job_id, verdict, note, fit, company, title, reason, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (job_id, verdict, note or "", job["fit"], job["company"], job["title"],
                     job["fit_reason"], now_iso()))


def fit_feedback(unapplied_only=False):
    with connect() as con:
        return con.execute("SELECT * FROM fit_feedback" + (" WHERE applied=0" if unapplied_only else "")
                           + " ORDER BY id").fetchall()


def mark_fit_feedback_applied(ids):
    if not ids:
        return
    with _lock, connect() as con:
        con.executemany("UPDATE fit_feedback SET applied=1 WHERE id=?", [(i,) for i in ids])


def set_resume_answer(job_id, skill, question, answer, context="", scope="job"):
    with _lock, connect() as con:
        con.execute(
            """INSERT INTO resume_answers (job_id, skill, question, answer, context, scope, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(job_id, skill) DO UPDATE SET answer=excluded.answer,
                 context=excluded.context, scope=excluded.scope, created_at=excluded.created_at""",
            (job_id, skill, question, answer, context, scope, now_iso()))


def set_apply(job_id, **fields):
    """Update apply_* columns (state, plan, error, queued_at, run_id). Independent of
    resume_* and status."""
    allowed = {"state", "plan", "error", "queued_at", "run_id"}
    sets, vals = ["apply_updated_at=?"], [now_iso()]
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(k)
        if k == "plan":
            v = json.dumps(v)
        sets.append(f"apply_{k}=?")
        vals.append(v)
    with _lock, connect() as con:
        con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals + [job_id])


def start_apply_run(job_id, mode, runner):
    with _lock, connect() as con:
        cur = con.execute("INSERT INTO apply_runs (job_id, mode, started, runner) VALUES (?,?,?,?)",
                          (job_id, mode, now_iso(), runner))
        return cur.lastrowid


def end_apply_run(run_id, outcome, reason="", screenshot="", form_hash_seen=""):
    with _lock, connect() as con:
        con.execute("UPDATE apply_runs SET finished=?, outcome=?, reason=?, screenshot=?, "
                    "form_hash_seen=? WHERE id=?",
                    (now_iso(), outcome, (reason or "")[:500], screenshot or "", form_hash_seen or "", run_id))


def apply_runs_for(job_ids):
    if not job_ids:
        return {}
    out = {}
    with connect() as con:
        for r in con.execute(
                f"SELECT * FROM apply_runs WHERE job_id IN ({','.join('?' * len(job_ids))}) ORDER BY id",
                list(job_ids)):
            out.setdefault(r["job_id"], []).append(dict(r))
    return out


def last_apply_run():
    with connect() as con:
        r = con.execute("SELECT * FROM apply_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None


def start_run(job_id, task, phase, worker, model):
    with _lock, connect() as con:
        cur = con.execute(
            "INSERT INTO resume_runs (job_id, task, phase, worker, model, started) VALUES (?,?,?,?,?,?)",
            (job_id, task, phase, worker, model, now_iso()))
        return cur.lastrowid


def end_run(run_id, ok, error=""):
    with _lock, connect() as con:
        con.execute(
            """UPDATE resume_runs SET finished=?, ok=?, error=?,
               seconds=(julianday(?) - julianday(started)) * 86400 WHERE id=?""",
            (now_iso(), 1 if ok else 0, (error or "")[:500], now_iso(), run_id))


def fail_open_runs(error="server restarted"):
    """Runs left without a finish (process died mid-run) are closed as failed."""
    with _lock, connect() as con:
        con.execute("UPDATE resume_runs SET finished=?, ok=0, error=? WHERE finished IS NULL",
                    (now_iso(), error))


def runs_for_jobs(job_ids):
    if not job_ids:
        return {}
    out = {}
    with connect() as con:
        rows = con.execute(
            f"""SELECT job_id, task, phase, worker, model, started, finished, seconds, ok, error
                FROM resume_runs WHERE job_id IN ({','.join('?' * len(job_ids))}) ORDER BY id""",
            list(job_ids)).fetchall()
    for r in rows:
        out.setdefault(r["job_id"], []).append(dict(r))
    return out


def run_stats():
    """Today's counts and per-phase average durations (successful runs, last 30)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with connect() as con:
        ok_today, failed_today = con.execute(
            "SELECT SUM(ok=1), SUM(ok=0) FROM resume_runs WHERE started >= ?", (today,)).fetchone()
        avg = {}
        for phase in ("analyze", "generate", "answer", "cover"):
            row = con.execute(
                """SELECT AVG(seconds) FROM (SELECT seconds FROM resume_runs
                   WHERE phase=? AND ok=1 AND seconds IS NOT NULL ORDER BY id DESC LIMIT 30)""",
                (phase,)).fetchone()
            avg[phase] = round(row[0]) if row and row[0] else None
        ready_today = con.execute(
            "SELECT COUNT(*) FROM resume_runs WHERE phase='generate' AND ok=1 AND started >= ?",
            (today,)).fetchone()[0]
    return {"ok_today": ok_today or 0, "failed_today": failed_today or 0,
            "ready_today": ready_today, "avg": avg}


def set_meta(key, value):
    with _lock, connect() as con:
        con.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )


def get_meta(key, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default
