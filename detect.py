"""Which ATS does a company use? Probes all platforms JobScout can fetch and prints
the ready-to-paste companies.yaml line.

Usage:  python detect.py "Company Name" [more names...]
        python detect.py --slug someslug          (probe an exact slug only)
"""
import re
import sys

import requests

UA = {"User-Agent": "JobSearchLocal/1.0 (personal job search tool)"}
WD_HOSTS = ["wd1", "wd5", "wd3", "wd12", "wd2", "wd10", "wd101", "wd103", "wd108"]


def slug_candidates(name):
    base = re.sub(r"[^a-z0-9]+", "", name.lower())
    dashed = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    out = [base, dashed]
    for suffix in ("inc", "io", "hq", "labs", "technologies", "software"):
        out.append(base + suffix)
    return list(dict.fromkeys(s for s in out if s))


def probe_simple(ats, slug):
    """Returns posting count if the board exists and has jobs, else None."""
    try:
        if ats == "greenhouse":
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                             headers=UA, timeout=10)
            n = len(r.json().get("jobs", [])) if r.status_code == 200 else 0
            return n or None
        if ats == "lever":
            r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json",
                             headers=UA, timeout=10)
            n = len(r.json()) if r.status_code == 200 and isinstance(r.json(), list) else 0
            return n or None
        if ats == "ashby":
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                             headers=UA, timeout=10)
            n = len(r.json().get("jobs", [])) if r.status_code == 200 else 0
            return n or None
        if ats == "smartrecruiters":
            r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
                             headers=UA, timeout=10)
            n = r.json().get("totalFound", 0) if r.status_code == 200 else 0
            return n or None
        if ats == "recruitee":
            r = requests.get(f"https://{slug}.recruitee.com/api/offers/", headers=UA, timeout=10)
            n = len(r.json().get("offers", [])) if r.status_code == 200 else 0
            return n or None
        if ats == "workable":
            r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
                             headers=UA, timeout=10)
            n = len(r.json().get("jobs", [])) if r.status_code == 200 else 0
            return n or None
        if ats == "bamboohr":
            r = requests.get(f"https://{slug}.bamboohr.com/careers/list", headers=UA, timeout=10)
            n = len(r.json().get("result") or []) if r.status_code == 200 else 0
            return n or None
        if ats == "breezy":
            r = requests.get(f"https://{slug}.breezy.hr/json", headers=UA, timeout=10)
            d = r.json() if r.status_code == 200 else None
            return (len(d) or None) if isinstance(d, list) else None
    except Exception:
        pass
    return None


def probe_workday(tenant):
    for wd in WD_HOSTS:
        base = f"https://{tenant}.{wd}.myworkdayjobs.com"
        try:
            r = requests.get(base, headers=UA, timeout=7, allow_redirects=True)
        except Exception:
            continue
        m = re.search(r"myworkdayjobs\.com/(?:[a-zA-Z_\-]+/)?([A-Za-z0-9_\-]+)/?$", r.url)
        site = m.group(1) if m and m.group(1) not in ("en-US", "login") else None
        for s in dict.fromkeys(filter(None, [site, "External", "Careers", f"{tenant}careers",
                                             "External_Career_Site", tenant.capitalize(), "Search"])):
            try:
                rr = requests.post(f"{base}/wday/cxs/{tenant}/{s}/jobs",
                                   json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                                   headers={**UA, "Content-Type": "application/json"}, timeout=10)
                if rr.status_code == 200:
                    return wd, s, rr.json().get("total", 0)
            except Exception:
                pass
    return None


def detect_company(name, exact_slug=None):
    """Probe every fetchable platform. Returns
    {found: bool, ats, slug, postings} or {found: False, checked: [...]}."""
    slugs = [exact_slug] if exact_slug else slug_candidates(name)
    simple_platforms = ("greenhouse", "lever", "ashby", "smartrecruiters",
                        "recruitee", "workable", "bamboohr", "breezy")
    for ats in simple_platforms:
        for slug in slugs:
            probe = slug if ats != "smartrecruiters" else (exact_slug or name.replace(" ", ""))
            n = probe_simple(ats, probe)
            if n:
                return {"found": True, "ats": ats, "slug": probe, "postings": n}
    for tenant in slugs[:2]:
        hit = probe_workday(tenant)
        if hit:
            wd, site, total = hit
            return {"found": True, "ats": "workday", "slug": f"{tenant}:{wd}:{site}",
                    "postings": total}
    return {"found": False, "checked": list(simple_platforms) + ["workday"]}


def detect(name, exact_slug=None):
    res = detect_company(name, exact_slug)
    if res["found"]:
        slug = res["slug"]
        quoted = f'"{slug}"' if ":" in slug else slug
        print(f"FOUND  {name}: {res['ats']} ({res['postings']} postings)")
        print(f"  - {{name: {name}, ats: {res['ats']}, slug: {quoted}}}")
    else:
        print(f"  -    {name}: no public board found (own platform / iCIMS / Phenom / Taleo "
              f"-> Adzuna is the channel; consider adding it to adzuna.companies in settings)")
    return res["found"]


FINGERPRINTS = [   # (ats, pattern on page HTML + final URL); order matters: Phenom pages link to Workday apply
    ("phenom", r"phenompeople\.com"),
    ("icims", r"\.icims\.com"),
    ("oraclehcm", r"oraclecloud\.com|/hcmUI/CandidateExperience"),
    ("eightfold", r"eightfold\.ai"),
    ("workday", r"myworkdayjobs\.com"),
    ("successfactors", r"successfactors\.com|jobs2web"),
    ("jobvite", r"jobs\.jobvite\.com"),
    ("taleo", r"taleo\.net"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([a-z0-9_-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_%-]+)"),
]
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
              "Accept": "text/html,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}


def detect_url(url):
    """Fingerprint a careers page and extract the fetcher identifier (companies.yaml slug).
    Returns {found, ats, slug, postings} or {found: False, ats?, note}."""
    from urllib.parse import urlparse
    try:
        r = requests.get(url, headers=BROWSER_UA, timeout=20, allow_redirects=True)
    except Exception as e:
        return {"found": False, "note": f"fetch failed: {e}"}
    html, final = r.text, r.url
    if r.status_code in (401, 403) or re.search(r"Access Denied|edgesuite\.net", html[:2000]):
        return {"found": False, "note": f"bot-protected (HTTP {r.status_code}) - category 3, Adzuna only"}
    host = urlparse(final).netloc
    hay = html + " " + final
    for ats, pat in FINGERPRINTS:
        if not re.search(pat, hay, re.I):
            continue
        if ats == "workday":
            m = re.search(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)", hay)
            if m and m.group(3).lower() not in ("login", "job", "jobs"):
                hit = probe_workday(m.group(1))
                return {"found": True, "ats": "workday", "slug": f"{m.group(1)}:{m.group(2)}:{m.group(3)}",
                        "postings": hit[2] if hit else None}
        if ats == "icims":
            try:                                        # Attract site (careers-home) on the page's own host?
                a = requests.get(f"https://{host}/api/jobs", params={"keywords": "engineer", "page": 1},
                                 headers=BROWSER_UA, timeout=15)
                if a.status_code == 200 and "json" in a.headers.get("content-type", ""):
                    return {"found": True, "ats": "icims", "slug": host, "postings": a.json().get("totalCount")}
            except Exception:
                pass
            classic = sorted(set(re.findall(r"https?://([a-z0-9-]+\.icims\.com)/jobs", hay)))
            classic = [c for c in classic if not c.startswith(("internal-", "partners-", "earlycareers-"))]
            if classic:
                return {"found": True, "ats": "icims", "slug": classic[0], "postings": None}
        if ats == "oraclehcm":
            m = re.search(r"https?://([a-z0-9]+\.fa\.[a-z0-9]+\.oraclecloud\.com)", hay)
            apihost = m.group(1) if m else host
            m2 = re.search(r"/sites/([A-Za-z0-9_]+)", final) or re.search(r"/sites/([A-Za-z0-9_]+)", hay)
            site = m2.group(1) if m2 else None
            try:
                sites = requests.get(f"https://{apihost}/hcmRestApi/resources/latest/recruitingCESites",
                                     params={"onlyData": "true", "limit": 20}, headers=BROWSER_UA,
                                     timeout=15).json().get("items", [])
                active = [x for x in sites if x.get("StatusCode") == "ORA_ACTIVE"
                          and str(x.get("SiteCode", "")).upper().startswith("CX")]
                if not site or not any(x.get("SiteNumber") == site for x in sites):
                    site = (active or sites or [{}])[0].get("SiteNumber", site)
            except Exception:
                pass
            if site:
                return {"found": True, "ats": "oraclehcm", "postings": None,
                        # a third part = custom domain that proxies the site (careers.ti.com/en/sites/CX);
                        # a marketing page that merely links to oraclecloud keeps the API host
                        "slug": f"{apihost}:{site}" + (f":{host}" if apihost != host and "/sites/" in final else "")}
        if ats == "phenom":
            m = re.match(r"^/([a-z]{2,6}/[a-z]{2})(?:/|$)", urlparse(final).path)
            return {"found": True, "ats": "phenom", "slug": f"{host}:/{m.group(1) if m else 'us/en'}", "postings": None}
        if ats in ("greenhouse", "lever", "ashby"):
            from urllib.parse import unquote
            slugs = [unquote(x) for x in re.findall(pat, hay, re.I)]
            slugs = [x for x in slugs if x.lower() not in ("embed", "job", "jobs", "the", "for")]
            if slugs:
                slug = max(set(slugs), key=slugs.count)          # the board linked most often
                n = probe_simple(ats, slug)
                if n:
                    return {"found": True, "ats": ats, "slug": slug, "postings": n}
        if ats in ("greenhouse", "lever", "ashby"):
            return {"found": False, "ats": ats, "note": f"{ats} link on the page but no board slug answered - check the careers page's job links by hand"}
        note = f"{ats} recognised, no fetcher yet"
        if ats == "eightfold":
            note += " (its API answers 'Not authorized for PCSX' without the page token)"
        return {"found": False, "ats": ats, "note": note}
    return {"found": False, "note": "no known platform fingerprint on the page"}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == "--url" and len(args) > 1:
        name = args[2] if len(args) > 2 else "<Company>"
        res = detect_url(args[1])
        if res.get("found"):
            slug = res["slug"]
            quoted = f'"{slug}"' if ":" in slug else slug
            print(f"FOUND  {res['ats']}" + (f" ({res['postings']} postings)" if res.get("postings") else ""))
            print(f"  - {{name: {name}, ats: {res['ats']}, slug: {quoted}}}")
        else:
            print(f"  -    {res.get('ats', '')} {res.get('note', '')}".strip())
    elif args[0] == "--slug" and len(args) > 1:
        detect(args[1], exact_slug=args[1])
    else:
        for name in args:
            detect(name)
