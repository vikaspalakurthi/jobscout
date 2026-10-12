"""Bulk board discovery: probe many company names with detect.detect_company in parallel.

    python discover.py --from-adzuna            # every employer Adzuna has surfaced (jobs.db)
    python discover.py --names names.txt        # one company name per line
    python discover.py --from-adzuna --names austin.txt --out data/discovered.json
    python discover.py --names misses.txt --fingerprint   # + domain lookup and careers-page fingerprinting (category 2)

Skips names already in config/companies.yaml (case-insensitive). Writes a JSON report
{found: [{name, ats, slug, postings}], missed: [...]} and prints YAML lines for the found
boards so they can be reviewed and pasted into companies.yaml (never appended blindly:
a slug guess can hit a different company that happens to share the slug).
"""
import argparse
import json
import re
import sqlite3
import sys
import time

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

import detect
import fetchers

AGENCY = re.compile(r"\b(staffing|recruit|talent|consult|solutions|infosolutions|technologies inc|"
                    r"tech\w* (llc|inc)|systems (llc|inc)|group,? inc|llc|jobs?|hiring|placement|"
                    r"resourc|workforce|manpower|robert half|apex systems|insight global|teksystems|"
                    r"judge group|kforce|randstad|cybercoders|dice|indeed)\b", re.I)


def adzuna_names(db_path="data/jobs.db", min_score=40):
    con = sqlite3.connect(db_path)
    rows = con.execute("""SELECT company, COUNT(*) n, MAX(score) s FROM jobs
                          WHERE source='adzuna' AND company != '' GROUP BY company
                          ORDER BY n DESC""").fetchall()
    return [r[0] for r in rows if (r[2] or 0) >= min_score]


def clean(name):
    n = re.sub(r"[,.]?\s*\b(inc|llc|ltd|corp|corporation|co|plc|limited|holdings|group)\b\.?$", "",
               name.strip(), flags=re.I)
    return n.strip(" ,.-")


CAREER_PATHS = ["https://careers.{d}/", "https://jobs.{d}/", "https://www.{d}/careers", "https://www.{d}/careers/",
                "https://{d}/careers", "https://www.{d}/jobs", "https://www.{d}/about/careers"]


def company_domain(name):
    """Free public autocomplete (no key): company name -> primary web domain, or None."""
    try:
        r = requests.get("https://autocomplete.clearbit.com/v1/companies/suggest",
                         params={"query": name}, timeout=10)
        hits = r.json() if r.status_code == 200 else []
    except Exception:
        return None
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for h in hits:                                   # prefer the hit whose name matches best
        if re.sub(r"[^a-z0-9]", "", (h.get("name") or "").lower()).startswith(key[:6]):
            return h.get("domain")
    return hits[0].get("domain") if hits else None


def fingerprint_company(name):
    """Category-2 discovery: find the careers page via the company's domain and fingerprint it
    (detect.detect_url). Returns detect_url's dict plus the url that resolved."""
    dom = company_domain(name)
    if not dom:
        return {"found": False, "note": "no domain found"}
    last = {"found": False, "note": f"no careers page fingerprinted on {dom}"}
    for tpl in CAREER_PATHS:
        url = tpl.format(d=dom)
        try:
            res = detect.detect_url(url)
        except Exception:
            continue
        if res.get("found"):
            res["url"] = url
            return res
        if res.get("ats"):                           # recognised platform without a fetcher: report once
            last = {**res, "url": url}
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-adzuna", action="store_true")
    ap.add_argument("--names", help="text file, one company per line")
    ap.add_argument("--out", default="data/discovered.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--include-agencies", action="store_true")
    ap.add_argument("--fingerprint", action="store_true",
                    help="when slug guessing fails, resolve the company's domain and fingerprint its careers page")
    a = ap.parse_args()

    names = []
    if a.from_adzuna:
        names += adzuna_names()
    if a.names:
        with open(a.names, encoding="utf-8") as f:
            names += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    have = {c["name"].lower() for c in fetchers.load_companies()}
    seen, todo = set(), []
    for n in names:
        c = clean(n)
        k = c.lower()
        if not c or k in seen or k in have:
            continue
        if not a.include_agencies and AGENCY.search(n):
            continue
        seen.add(k)
        todo.append(c)
    print(f"{len(todo)} names to probe ({len(names)} given, {len(have)} already configured)")

    found, missed, t0 = [], [], time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(detect.detect_company, n): n for n in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            n = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"found": False, "error": str(e)}
            if not r.get("found") and a.fingerprint:
                r = fingerprint_company(n)
            if r.get("found"):
                found.append({"name": n, "ats": r["ats"], "slug": r["slug"], "postings": r.get("postings"), "url": r.get("url")})
                print(f"[{i}/{len(todo)}] FOUND {n}: {r['ats']} {r['slug']} ({r.get('postings')}) {r.get('url') or ''}", flush=True)
            else:
                missed.append(n)
                if r.get("ats"):
                    print(f"[{i}/{len(todo)}]   -   {n}: {r['ats']} recognised, no fetcher ({r.get('url')})", flush=True)
                if i % 25 == 0:
                    print(f"[{i}/{len(todo)}] ... {len(found)} found so far, {int(time.time()-t0)}s", flush=True)
    found.sort(key=lambda x: x["name"].lower())
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"found": found, "missed": missed, "seconds": int(time.time() - t0)}, f, indent=1)
    print(f"\n{len(found)} boards found, {len(missed)} without a public board, {int(time.time()-t0)}s")
    print("\n# review, then paste into config/companies.yaml:")
    for x in found:
        slug = f'"{x["slug"]}"' if ":" in x["slug"] else x["slug"]
        print(f"  - {{name: {x['name']}, ats: {x['ats']}, slug: {slug}}}   # {x['postings']} postings")


if __name__ == "__main__":
    sys.exit(main())
