"""New-job notifications after a refresh (Discord webhook).

A refresh returns the ids it INSERTED (db.upsert_jobs); after scoring we pick the ones worth
a look (score >= notify.min_score, allowed loc_class, not citizen-only, not blocked) and post
one Discord embed. The webhook URL lives in config/local.yaml (gitignored) via the Settings
page. Nothing here blocks or fails a refresh: every error is caught and reported in the
refresh summary.
"""
import json

import requests

import db
import fetchers

DEFAULTS = {
    "enabled": False,
    "discord_webhook_url": "",
    "min_score": 50,
    "max_items": 15,
    "loc_classes": ["austin", "texas", "remote_us"],
}
JOBSCOUT_URL = "http://localhost:8765"


def settings():
    s = dict(DEFAULTS)
    s.update((fetchers.load_settings().get("notify") or {}))
    return s


def pick(new_ids, s=None):
    """The subset of freshly inserted jobs that deserve a ping, best score first, deduped."""
    s = s or settings()
    if not new_ids:
        return []
    out, seen = [], set()
    with db.connect() as con:
        for chunk in range(0, len(new_ids), 500):
            ids = new_ids[chunk:chunk + 500]
            rows = con.execute(
                f"""SELECT id, company, title, location, loc_class, work_mode, role, score, url,
                           source, salary_text, fingerprint, work_auth, posted_at
                    FROM jobs WHERE id IN ({','.join('?' * len(ids))})
                      AND status = 'new' AND COALESCE(work_auth,'') != 'citizen_only'
                      AND score >= ?""", (*ids, float(s["min_score"]))).fetchall()
            for r in rows:
                if s.get("loc_classes") and r["loc_class"] not in s["loc_classes"]:
                    continue
                if r["fingerprint"] in seen:
                    continue
                seen.add(r["fingerprint"])
                out.append(dict(r))
    out.sort(key=lambda r: (-(r["score"] or 0), r["company"]))
    return out


def _line(r):
    bits = [r["company"], r["location"] or r["loc_class"]]
    if r["work_mode"]:
        bits.append(r["work_mode"])
    if r["role"]:
        bits.append(r["role"])
    if r["salary_text"]:
        bits.append(r["salary_text"][:40])
    title = (r["title"] or "")[:90].replace("]", ")").replace("[", "(")
    return f"**{int(r['score'] or 0)}** [{title}]({r['url']}) - " + " · ".join(str(b) for b in bits if b)


def build_embed(jobs, total_new, s=None):
    s = s or settings()
    shown = jobs[: int(s["max_items"])]
    lines = [_line(r) for r in shown]
    desc = "\n".join(lines)
    if len(desc) > 3900:                       # Discord embed description limit is 4096
        desc = desc[:3880].rsplit("\n", 1)[0] + "\n…"
    more = len(jobs) - len(shown)
    footer = f"{total_new} new posting(s) this refresh · {len(jobs)} worth a look"
    if more > 0:
        footer += f" · {more} more in JobScout"
    return {
        "title": f"JobScout: {len(jobs)} new job{'s' if len(jobs) != 1 else ''} worth a look",
        "url": JOBSCOUT_URL + "/?new=1",
        "description": desc or "Nothing above the score bar this time.",
        "color": 0x2ecc71 if jobs else 0x95a5a6,
        "footer": {"text": footer[:2048]},
    }


def send_discord(embed, webhook_url, content=""):
    r = requests.post(webhook_url, json={"content": content[:1900], "embeds": [embed]},
                      timeout=15)
    if r.status_code >= 300:
        raise RuntimeError(f"Discord webhook returned {r.status_code}: {r.text[:200]}")
    return True


def after_refresh(new_ids):
    """Called once per refresh after scoring. Returns a small dict for the refresh summary."""
    s = settings()
    result = {"enabled": bool(s["enabled"]), "new": len(new_ids or []), "notified": 0}
    if not s["enabled"]:
        return result
    if not s["discord_webhook_url"]:
        result["error"] = "no Discord webhook URL configured"
        return result
    try:
        picked = pick(new_ids, s)
        if not picked:
            result["skipped"] = "no new jobs above the score bar"
            return result
        send_discord(build_embed(picked, len(new_ids), s), s["discord_webhook_url"])
        result["notified"] = len(picked)
        db.set_meta("last_notify", {"at": db.now_iso(), "count": len(picked),
                                    "ids": [r["id"] for r in picked]})
    except Exception as e:                     # never let a webhook break a refresh
        result["error"] = str(e)[:300]
    return result


def send_test(webhook_url=None):
    """Post a sample digest (the 3 best jobs added by the last refresh, or a placeholder)."""
    s = settings()
    url = webhook_url or s["discord_webhook_url"]
    if not url:
        raise RuntimeError("no Discord webhook URL")
    last = (db.get_meta("last_refresh") or {}).get("_summary") or {}
    picked = pick(last.get("new_ids") or [], dict(s, max_items=3))[:3]
    embed = build_embed(picked, last.get("new", 0), dict(s, max_items=3))
    embed["title"] = "JobScout test: " + embed["title"]
    send_discord(embed, url, content="JobScout notifications are working.")
    return len(picked)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("sent, sample size", send_test())
    else:
        print(json.dumps(settings(), indent=1))
