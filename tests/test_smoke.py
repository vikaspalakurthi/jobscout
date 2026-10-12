"""Offline smoke tests: classifiers, normalize -> upsert -> rescore, and the API on a temp DB.
No network, no Claude. Run: python -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import db                      # noqa: E402
db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="jobscout-test-"), "jobs.db")
db.init()

import fetchers                # noqa: E402
import scoring                 # noqa: E402
import server                  # noqa: E402

SRE_DESC = ("We run Kubernetes on AWS with Prometheus, Grafana and OpenTelemetry. You will own "
            "SLOs, on-call and Terraform. Elasticsearch and Kafka experience is a plus. 8+ years.")


class Classifiers(unittest.TestCase):
    def test_location_classes(self):
        cases = {
            "Austin, TX": "home",
            "Austin, Texas": "home",
            "Dallas, TX": "home_state",
            "Remote - United States": "remote_us",
            "Foster City, CA": "us_other",          # the ISO-code trap: CA is California, not Canada
            "Bangalore, India": "non_us",
            "Toronto, ON, Canada": "non_us",
            "Indianapolis, IN": "us_other",         # "india" must not match inside Indianapolis
        }
        for loc, want in cases.items():
            self.assertEqual(fetchers.classify_location(loc), want, loc)

    def test_relevance_prefilter(self):
        self.assertTrue(fetchers.is_relevant("Site Reliability Engineer", ""))
        self.assertTrue(fetchers.is_relevant("Senior DevOps Engineer", ""))
        self.assertFalse(fetchers.is_relevant("Account Executive", "sell our product"))
        self.assertFalse(fetchers.is_relevant("SDET II", ""), "QA titles are not in the SRE preset")

    def test_qa_preset_relevance(self):
        """Loading a preset changes what is fetched (relevance) and how titles are labelled."""
        import yaml
        qa = yaml.safe_load(open(os.path.join(ROOT, "config", "presets", "qa-sdet.yaml"), encoding="utf-8"))
        saved = dict(scoring._cache)
        try:
            # inject the preset as the active profile (mtime must match or load_config re-reads the file)
            scoring._cache.update(mtime=os.stat(scoring.SKILLS_PATH).st_mtime_ns, cfg=qa)
            fetchers._rel_cache["key"] = None
            self.assertTrue(fetchers.is_relevant("SDET II", ""))
            self.assertFalse(fetchers.is_relevant("Site Reliability Engineer", ""))
            self.assertIn("sdet", fetchers.search_terms())
            s, b, f = scoring.Scorer(qa).score("Senior SDET", "selenium pytest ci/cd playwright", "home")
            self.assertEqual(b["title"]["label"], "SDET")
        finally:
            scoring._cache.update(saved); fetchers._rel_cache["key"] = None

    def test_norm_shape(self):
        j = fetchers._norm("greenhouse", 1, "Acme", "SRE", "Austin, TX", "https://x", SRE_DESC,
                           posted_at="2026-10-01", updated_at="2026-10-05T10:00:00Z")
        for key in ("dedupe_key", "fingerprint", "loc_class", "work_mode", "posted_at", "updated_at"):
            self.assertIn(key, j)
        self.assertEqual(j["updated_at"], "2026-10-05")
        self.assertEqual(j["dedupe_key"], "greenhouse:1")


class Pipeline(unittest.TestCase):
    def test_upsert_rescore_and_status_survive_refresh(self):
        job = fetchers._norm("greenhouse", 42, "Acme", "Senior Site Reliability Engineer",
                             "Austin, TX", "https://acme.example/jobs/42", SRE_DESC, posted_at="2026-10-01")
        n, ids = db.upsert_jobs([job])
        self.assertEqual((n, len(ids)), (1, 1))
        scoring.rescore_all(db)
        row = db.get_job(ids[0])
        self.assertGreater(row["score"], 50, "an SRE posting in Austin must score well by keyword")
        self.assertEqual(row["role"], "SRE")
        db.set_status(ids[0], "starred")
        n2, _ = db.upsert_jobs([dict(job, title="Senior Site Reliability Engineer (updated)")])
        self.assertEqual(n2, 0)
        again = db.get_job(ids[0])
        self.assertEqual(again["status"], "starred", "refresh must never touch user status")
        self.assertIn("(updated)", again["title"])

    def test_citizenship_flag(self):
        job = fetchers._norm("lever", "c1", "Gov Co", "SRE", "Austin, TX", "https://x",
                             SRE_DESC + " Must be a US citizen with an active TS/SCI clearance.")
        _, ids = db.upsert_jobs([job])
        scoring.rescore_all(db)
        self.assertEqual(db.get_job(ids[0])["work_auth"], "citizen_only")


class Api(unittest.TestCase):
    def setUp(self):
        self.c = server.app.test_client()

    def test_endpoints_answer(self):
        for path in ("/api/status", "/api/jobs?tab=inbox&sort=fit", "/api/jobs?tab=all&days=30&days_by=updated",
                     "/api/facets", "/api/companies", "/api/fit/status", "/api/resume/status", "/api/settings",
                     "/", "/companies", "/settings", "/about"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)

    def test_jobs_filtering_and_toggle(self):
        r = self.c.get("/api/jobs?tab=inbox").get_json()
        self.assertIn("jobs", r)
        if r["jobs"]:
            jid = r["jobs"][0]["id"]
            self.assertEqual(self.c.post(f"/api/jobs/{jid}/status", json={"status": "hidden"}).status_code, 200)
            self.assertEqual(db.get_job(jid)["status"], "hidden")
            self.c.post(f"/api/jobs/{jid}/status", json={"status": "new"})

    def test_schedule_slots(self):
        self.assertEqual(server.parse_slots("06:00, 11:00, 18:00, 23:00"), [(6, 0), (11, 0), (18, 0), (23, 0)])
        self.assertEqual(server.parse_slots("25:00, nope"), [])


if __name__ == "__main__":
    unittest.main()
