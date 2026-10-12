"""Scoring engine: matches jobs against config/skills.yaml."""
import os
import re
from datetime import datetime, timezone

import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_PATH = os.path.join(BASE_DIR, "config", "skills.yaml")


def load_config():
    with open(SKILLS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _term_pattern(term):
    term = str(term).lower()
    if " " in term or "/" in term:
        return re.compile(re.escape(term))
    return re.compile(r"\b" + re.escape(term) + r"\b")


class Scorer:
    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        c = self.cfg
        self.role_titles = [
            (re.compile(r["match"], re.I), r.get("label", r["match"]), r["points"])
            for r in c.get("role_titles", [])
        ]
        self.blockers = [re.compile(p, re.I) for p in c.get("title_blockers", [])]
        self.title_penalties = [
            (re.compile(r["match"], re.I),
             re.compile(r["unless"], re.I) if r.get("unless") else None,
             r["points"])
            for r in c.get("title_penalties", [])
        ]
        self.seniority = [
            (re.compile(r["match"], re.I), r["points"]) for r in c.get("seniority_bonus", [])
        ]
        skills = c.get("skills", {})
        self.skills_cap = skills.get("cap_points", 40)
        self.skill_groups = [
            {
                "name": g["name"],
                "points": g["points"],
                "terms": [(str(t), _term_pattern(t)) for t in g["terms"]],
            }
            for g in skills.get("groups", [])
        ]
        self.location_points = c.get("location_points", {})
        self.freshness = c.get("freshness_bonus", []) or []
        sp = c.get("source_points", {}) or {}
        self.source_direct = sp.get("direct", 0)
        self.source_aggregator = sp.get("aggregator", 0)
        self.experience = [
            (re.compile(r["match"], re.I), r["points"]) for r in c.get("experience_bonus", [])
        ]
        self.flags = {
            key: {
                "points": fd.get("points", 0),
                "label": fd.get("label", key),
                # literal terms report themselves as the hit; regex `patterns` report the matched text
                "terms": [(str(t), _term_pattern(t)) for t in fd.get("terms", [])]
                         + [(None, re.compile(p, re.I)) for p in fd.get("patterns", [])],
                "ignore": [re.compile(p, re.I) for p in fd.get("ignore", [])],
            }
            for key, fd in (c.get("flags") or {}).items()
        }

    def score(self, title, description, loc_class, employment_type=None,
              source=None, posted_at=None):
        """Returns (score 0-100, breakdown dict, flags list of {key,label,points})."""
        title_l = (title or "").lower()
        desc_l = (description or "").lower()
        text_l = title_l + "\n" + desc_l
        breakdown = {}
        flags = []

        for pat in self.blockers:
            if pat.search(title_l):
                return 0, {"blocked_by": pat.pattern}, [{"key": "blocked", "label": "Blocked title", "points": 0}]

        best_pts, best_label = 0, None
        for pat, label, pts in self.role_titles:
            if pts > best_pts and pat.search(title_l):
                best_pts, best_label = pts, label
        if best_label:
            breakdown["title"] = {"label": best_label, "points": best_pts}
        total = best_pts

        for pat, unless, pts in self.title_penalties:
            if pat.search(title_l) and not (unless and unless.search(title_l)):
                total += pts
                breakdown.setdefault("title_penalties", []).append({"match": pat.pattern, "points": pts})

        for pat, pts in self.seniority:
            if pat.search(title_l):
                total += pts
                breakdown["seniority"] = pts
                break

        skill_pts = 0
        matched_groups = []
        for g in self.skill_groups:
            hits = [t for t, pat in g["terms"] if pat.search(text_l)]
            if hits:
                pts = g["points"] + min(len(hits) - 1, 3)
                skill_pts += pts
                matched_groups.append({"group": g["name"], "points": pts, "terms": hits[:8]})
        skill_pts = min(skill_pts, self.skills_cap)
        if matched_groups:
            breakdown["skills"] = {"points": skill_pts, "groups": matched_groups}
        total += skill_pts

        loc_pts = self.location_points.get(loc_class or "unknown", 0)
        breakdown["location"] = {"class": loc_class, "points": loc_pts}
        total += loc_pts

        for pat, pts in self.experience:
            if pat.search(desc_l):
                total += pts
                breakdown["experience"] = pts
                break

        if posted_at:
            try:
                posted = datetime.strptime(posted_at[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days = max(0, (datetime.now(timezone.utc) - posted).days)
                for fb in self.freshness:
                    if days <= fb["within_days"]:
                        total += fb["points"]
                        breakdown["freshness"] = {"days_old": days, "points": fb["points"]}
                        break
            except ValueError:
                pass

        if source:
            from fetchers import DIRECT_SOURCES
            src_pts = self.source_direct if source in DIRECT_SOURCES else self.source_aggregator
            if src_pts:
                total += src_pts
                breakdown["source"] = {"source": source, "points": src_pts}

        for key, fd in self.flags.items():
            hits = []
            for t, pat in fd["terms"]:
                # a hit inside an ignore context (e.g. the EPPA rights poster) doesn't count
                for m in pat.finditer(text_l):
                    window = text_l[max(0, m.start() - 120):m.end() + 120]
                    if not any(ig.search(window) for ig in fd["ignore"]):
                        hits.append(t if t is not None else m.group(0))
                        break
            if hits:
                total += fd["points"]
                flags.append({"key": key, "label": fd["label"], "points": fd["points"], "terms": hits[:4]})

        if employment_type and re.search(r"contract|c2c|1099", str(employment_type), re.I):
            if not any(f["key"] == "contract" for f in flags):
                flags.append({"key": "contract", "label": "Contract role", "points": 0, "terms": [employment_type]})

        return max(0, min(100, round(total, 1))), breakdown, flags


def rescore_all(database):
    from fetchers import classify_location, detect_work_mode, infer_employment

    scorer = Scorer()
    rows = database.all_jobs_for_scoring()
    scored = []
    for r in rows:
        # Reclassify conservatively from the location text: always adopt a non_us
        # verdict (purges past classifier leaks); otherwise only upgrade unknowns.
        loc_class = r["loc_class"]
        fresh = classify_location(r["location"])
        # backfill: take the fresh class when the stored one is vague and the classifier
        # now sees something more specific (new location formats get fixed by a rescore)
        if fresh == "non_us" or loc_class in (None, "", "unknown") or \
                (loc_class in ("remote_global", "us_other") and fresh in ("remote_us", "texas", "austin")) or \
                (loc_class == "non_us" and fresh != "non_us"):    # a fixed classifier rule can un-flag a row
            loc_class = fresh
        work_mode = r["work_mode"]
        loc_mode = detect_work_mode(r["location"], "")      # signal from the location text alone
        if not work_mode or work_mode == "unknown":
            work_mode = detect_work_mode(r["location"], r["description"])
        elif loc_mode in ("remote", "hybrid") and work_mode == "office":
            work_mode = loc_mode        # "TX - Work from home" is not an office job
        employment = infer_employment(r["description"], r["employment_type"])
        s, b, f = scorer.score(r["title"], r["description"], loc_class, employment,
                               source=r["source"], posted_at=r["posted_at"])
        role = (b.get("title") or {}).get("label", "") if isinstance(b.get("title"), dict) else ""
        flag_keys = {fl["key"] for fl in f}
        if "citizenship" in flag_keys or "itar" in flag_keys:
            work_auth = "citizen_only"
        elif "no_sponsorship" in flag_keys:
            work_auth = "no_sponsorship"
        else:
            work_auth = ""
        scored.append((r["id"], s, b, f, loc_class, work_mode, role, employment, work_auth))
    database.update_scores(scored)
    return len(scored)
