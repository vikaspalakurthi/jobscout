"""Job fetchers. Every fetcher yields normalized dicts:
  source, external_id, company, title, location, loc_class, url, description,
  posted_at (YYYY-MM-DD or None), salary_text, employment_type
Only official/public, no-scrape endpoints are used.
"""
import copy
import html
import inspect
import os
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import requests
import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "JobSearchLocal/1.0 (personal job search tool)"}
TIMEOUT = 30
BOARD_WORKERS = 8        # parallel company-board fetches in run_all (settings: refresh.board_workers)


def _report(progress, msg, done, total, failed=0, phase="boards"):
    """Call the progress callback; pass counts only when it accepts keyword args
    (server.py does, refresh.py's print-lambda does not)."""
    try:
        params = inspect.signature(progress).parameters
        wants = any(p.kind == p.VAR_KEYWORD for p in params.values()) or "done" in params
    except (TypeError, ValueError):
        wants = False
    if wants:
        progress(msg, done=done, total=total, failed=failed, phase=phase)
    else:
        progress(msg)

# Sources that fetch straight from the employer (vs aggregator echoes).
DIRECT_SOURCES = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday",
                  "amazon", "oracle", "recruitee", "workable", "bamboohr", "breezy",
                  "oraclehcm", "phenom", "icims")

# Fetch-time prefilter. The patterns live in config/skills.yaml -> relevance (loaded from a
# role preset, see config/presets/); the built-ins below are the fallback when that section is
# missing. Compiled once per skills.yaml modification.
_FALLBACK_TITLE = (
    r"site reliab|\bsre\b|dev.?ops|observab|platform|infrastructur|\binfra\b|kafka|elastic|"
    r"opensearch|\belk\b|splunk|\bsiem\b|reliab|production engineer|cloud engineer|"
    r"cloud operations|cloud infrastructure|cloud ?ops|tech ?ops|\bops engineer|"
    r"operations engineer|it operations|systems? engineer|linux|\bunix\b|monitoring|telemetry|"
    r"logging|data platform|streaming|database reliability|incident|\bnoc\b|"
    r"kubernetes|\bk8s\b|admin(istrator)?\b|sysadmin|middleware|\bdba\b|"
    r"release engineer|build engineer|automation engineer|ci/?cd|prometheus|grafana|"
    r"(aws|azure|gcp) (engineer|architect)|resilience|capacity (engineer|planning)|"
    r"chaos engineer|performance engineer")
_FALLBACK_RESCUE = [
    r"kafka|confluent|redpanda", r"elasticsearch|opensearch|\belk\b|logstash|kibana|splunk",
    r"prometheus|grafana|opentelemetry|datadog|new relic|observability|\bslo\b|error budget",
    r"kubernetes|\bk8s\b|helm\b", r"terraform|ansible|puppet|gitops|argocd",
    r"site reliability|\bsre\b|devops|on.?call|incident response|production support",
    r"ci/cd|jenkins|github actions", r"linux|unix"]
_FALLBACK_SEARCH = ["site reliability engineer", "devops", "observability", "platform engineer",
                    "kafka", "elasticsearch", "linux administrator", "systems administrator", "kubernetes"]
_rel_cache = {"key": None}


def _relevance():
    """(TITLE_RELEVANT regex, [rescue regexes], min_groups, [search terms]) for the active profile."""
    import scoring
    rel = scoring.relevance()
    key = id(scoring.load_config())
    if _rel_cache["key"] != key:
        terms = rel.get("title_terms") or [_FALLBACK_TITLE]
        rescue = rel.get("desc_rescue_groups") or _FALLBACK_RESCUE
        _rel_cache.update(
            key=key,
            title=re.compile("|".join(f"(?:{t})" for t in terms), re.I),
            rescue=[re.compile(p, re.I) for p in rescue],
            min_groups=int(rel.get("min_groups") or 3),
            search=list(rel.get("search_terms") or _FALLBACK_SEARCH))
    return _rel_cache


class _TitleRelevant:
    """Module-level handle that always reflects the current profile (fetchers use .search())."""
    def search(self, text):
        return _relevance()["title"].search(text or "")


TITLE_RELEVANT = _TitleRelevant()


def desc_relevant(description):
    d = (description or "")[:12000].lower()
    r = _relevance()
    return sum(1 for p in r["rescue"] if p.search(d)) >= r["min_groups"]


def search_terms():
    """Keyword queries for boards that only answer searches (Workday, iCIMS, Phenom, Oracle HCM, amazon.jobs)."""
    return _relevance()["search"]


def is_relevant(title, description):
    return bool(TITLE_RELEVANT.search(title or "")) or desc_relevant(description)

NON_US = re.compile(
    r"london|united kingdom|\buk\b|canada|toronto|vancouver|montreal|germany|berlin|munich|"
    r"netherlands|amsterdam|france|paris|\bindia\b|bangalore|bengaluru|hyderabad|\bpune\b|chennai|"
    r"gurgaon|noida|mumbai|singapore|australia|sydney|melbourne|ireland|dublin|poland|warsaw|"
    r"krakow|spain|madrid|barcelona|portugal|lisbon|brazil|sao paulo|mexico city|japan|tokyo|galway|"
    r"china|taipei|hsinchu|\bporto\b|kaunas|pilsen|israel|tel aviv|\bemea\b|\bapac\b|\blatam\b|philippines|colombia|argentina|romania|"
    r"czech|estonia|lithuania|latvia|ukraine|serbia|hungary|austria|switzerland|zurich|sweden|"
    r"stockholm|norway|oslo|denmark|copenhagen|finland|helsinki|italy|milan|belgium|brussels|"
    r"new zealand|south africa|nigeria|kenya|egypt|dubai|\buae\b|saudi|korea|seoul|taiwan|"
    r"vietnam|thailand|indonesia|malaysia|turkey|istanbul|costa rica|guatemala|\bperu\b|chile|"
    r"uruguay|bolivia|ecuador|panama|honduras|el salvador|dominican|venezuela|paraguay|"
    r"nicaragua|bulgaria|croatia|slovakia|slovenia|greece|cyprus|malta|iceland|luxembourg|"
    r"morocco|tunisia|ghana|pakistan|bangladesh|sri lanka|nepal|qatar|bahrain|kuwait|armenia|"
    r"azerbaijan|kazakhstan|belarus|moldova|bogota|buenos aires|santiago|lima\b|montevideo|"
    r"guadalajara|monterrey|gurugram|karachi|lahore|islamabad|new delhi|\bdelhi\b|ahmedabad|"
    r"kolkata|jakarta|manila|ho chi minh|hanoi|cape town|johannesburg|nairobi|lagos|accra|"
    r"bucharest|sofia|zagreb|bratislava|ljubljana|athens|prague|brno|budapest|vilnius|riga\b|"
    r"tallinn|kyiv|kiev|belgrade|wroclaw|gdansk|poznan|cluj|brasilia|rio de janeiro|medellin|"
    r"quito|hong kong|macau|luxembourg city|reykjavik|casablanca|abu dhabi|doha|amman|beirut",
    re.I,
)
# Workday boards like CVS Health write "IRL - Galway", "RI - Woonsocket", "Work At Home-Texas"
NON_US_PREFIX = re.compile(r"^\s*(irl|gbr|ind|deu|fra|esp|mex|bra|aus|phl|pol|nld|can|che|ita|swe)\s*-"
                           r"|,\s*(kor|ind|chn|jpn|deu|gbr|irl|isr|sgp|twn|mys)\s*$"
                           # ISO-2 country code first ("BR, Sao Paulo", "CN-BEIJING-...", "IT-FI-FLORENCE") or last
                           # ("Kaunas, LT", "Graz, AT", "Telford, GB"). Codes that double as US state codes are
                           # deliberately absent (CA, IN, DE, PA, CO, IL, AR, ID, MA, TN, AZ, MD, MT, ME...).
                           r"|^\s*(BR|CN|IT|FR|GB|MX|MY|PH|PL|PT|RO|LT|LV|EE|NL|SE|AT|IE|ES|AU|CH|SG|TW|KR|JP|CZ|HU|VN|CL|PE|BE|DK|NO|FI|ZA|AE|TR|EG|NG|KE|TH|HK|NZ|GR|SK|SI|HR|BG|RS|UA|PK|BD|LK|CR|GT|DO|UY|EC|BO|PY|VE|HN|SV|GH|QA|BH|KW|AM|KZ|BY|LU|IS|CY|PR)\s*[,\-]"
                           r"|,\s*(BR|CN|IT|FR|GB|MX|MY|PH|PL|PT|RO|LT|LV|EE|NL|SE|AT|IE|ES|AU|CH|SG|TW|KR|JP|CZ|HU|VN|CL|PE|BE|DK|NO|FI|ZA|AE|TR|EG|NG|KE|TH|HK|NZ|GR|SK|SI|HR|BG|RS|UA|PK|BD|LK|CR|GT|DO|UY|EC|BO|PY|VE|HN|SV|GH|QA|BH|KW|AM|KZ|BY|LU|IS|CY|PR)\s*$"
                           r"|^\s*(international|global\s*-|tlv|eu)\s*$|puerto rico|(?<!new )\bmexico\b|kuala lumpur"
                           r"|\bthane\b|\bbraga\b|\bmassy\b|montpellier|linkoping|tijuana", re.I)
US_STATE_PREFIX = re.compile(r"^\s*(A[LKZR]|C[AOT]|DE|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])\s*[-,]", re.I)   # "RI - Woonsocket", "CT, Bloomfield"
WFH = re.compile(r"work\s*(from|at)\s*home|\bwfh\b", re.I)
US_STATE_NAME = re.compile(
    r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|"
    r"georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|"
    r"massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|"
    r"new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|"
    r"oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|"
    r"vermont|virginia|washington|west virginia|wisconsin|wyoming)\b", re.I)
US_HINT = re.compile(r"united states|\busa?\b|u\.s\.|america", re.I)
US_STATE = re.compile(r",\s?(A[LKZR]|C[AOT]|DE|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])\b")
US_CITY = re.compile(
    r"new york|\bnyc\b|san francisco|\bsf\b|bay area|seattle|boston|chicago|denver|atlanta|"
    r"los angeles|palo alto|mountain view|sunnyvale|san jose|menlo park|washington,? d\.?c|"
    r"portland|miami|phoenix|philadelphia|minneapolis|salt lake|raleigh|charlotte|nashville|"
    r"pittsburgh|san diego|boulder|oakland|brooklyn|bellevue|redmond|arlington|reston|"
    r"mclean|santa clara|irvine|columbus|detroit|tampa|orlando|kansas city|st\.? louis",
    re.I,
)
_home_cache = {"key": None}


def home():
    """Settings -> home: the user's metro (class "home") and the rest of their state (class
    "home_state"). Compiled once per settings change."""
    h = load_settings().get("home") or {}
    key = repr(sorted(h.items()))
    if _home_cache["key"] != key:
        city_terms = [str(t).lower() for t in (h.get("city_terms") or [h.get("city") or "austin"])]
        state_terms = [str(t).lower() for t in (h.get("state_terms") or [])]
        state = str(h.get("state") or "Texas").lower()
        code = str(h.get("state_code") or "TX").lower()
        _home_cache.update(
            key=key, city=h.get("city") or "Austin", state=h.get("state") or "Texas",
            state_code=(h.get("state_code") or "TX").upper(),
            city_re=re.compile("|".join(re.escape(t) for t in city_terms), re.I),
            state_re=re.compile(r"\b" + re.escape(state) + r"\b|,\s?" + re.escape(code) + r"\b|^\s*" + re.escape(code) + r"\s*-"
                                + ("|" + "|".join(r"\b" + re.escape(t) + r"\b" for t in state_terms) if state_terms else ""), re.I))
    return _home_cache


# A location that is just the country ("United States") means a distributed/US-wide
# role, not a specific office — treat as remote US.
BARE_US = re.compile(
    r"^\s*(?:remote\s*[-–,]?\s*)?(?:united states(?: of america)?|usa|u\.s\.a?\.?|us)"
    r"\s*(?:\(remote\))?\s*$", re.I)


def detect_work_mode(location, description, explicit=None):
    """remote | hybrid | office | unknown. `explicit` is an ATS-provided value."""
    if explicit:
        e = str(explicit).lower()
        if e in ("remote", "hybrid"):
            return e
        if e in ("onsite", "on-site", "office"):
            return "office"
    t = (location or "").lower()
    if "hybrid" in t:
        return "hybrid"
    if "remote" in t or "anywhere" in t or "distributed" in t or BARE_US.match(location or "") \
            or WFH.search(t):
        return "remote"
    d = (description or "")[:6000].lower()
    if "hybrid" in d:
        return "hybrid"
    if re.search(r"fully remote|100% remote|remote.first|work from home", d):
        return "remote"
    if re.search(r"on.?site|in.?office|in the office|days? per week in", d):
        return "office"
    if t.strip():
        return "office"     # concrete location listed, no remote/hybrid signal
    return "unknown"


def infer_employment(description, existing=None):
    """Fill in FT/Contract/C2C when the board didn't state it, from the description."""
    if existing:
        return existing
    d = (description or "")[:8000].lower()
    if re.search(r"\bc2c\b|corp.to.corp", d):
        return "C2C"
    if re.search(r"\b1099\b", d):
        return "1099"
    if re.search(r"w2 contract|contract (position|role|opportunity)|contract.to.hire|"
                 r"\d+.month contract", d):
        return "Contract"
    if re.search(r"full.?time", d):
        return "Full-time"
    if re.search(r"part.?time", d):
        return "Part-time"
    return None


def classify_location(loc_text, remote_hint=False, country=None):
    t = (loc_text or "").lower()
    is_remote = (remote_hint or "remote" in t or "anywhere" in t or "distributed" in t
                 or "virtual" in t or bool(WFH.search(t)))
    non_us = ((bool(NON_US.search(t)) and "remote - us" not in t)
              or bool(NON_US_PREFIX.search(loc_text or "")))
    us = (bool(US_HINT.search(loc_text or "")) or bool(US_STATE.search(loc_text or ""))
          or bool(US_STATE_PREFIX.match(loc_text or "")) or bool(US_CITY.search(loc_text or ""))
          or bool(US_STATE_NAME.search(loc_text or "")) or (country or "").upper() in ("US", "USA"))
    h = home()
    if h["city_re"].search(t):
        return "home"
    if BARE_US.match(loc_text or ""):
        return "remote_us"
    if is_remote:
        if us:
            return "remote_us"
        if non_us:
            return "non_us"
        return "remote_global"
    if non_us:
        return "non_us"
    if h["state_re"].search(t):
        return "home_state"
    if us:
        return "us_other"
    return "unknown"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")


def strip_html(text):
    if not text:
        return ""
    p = _TextExtractor()
    try:
        p.feed(text)
        out = "".join(p.parts)
    except Exception:
        out = re.sub(r"<[^>]+>", " ", text)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n\s*\n+", "\n\n", out)
    return out.strip()[:20000]


def fingerprint(company, title):
    return re.sub(r"[^a-z0-9]+", "", (company or "").lower()) + "|" + re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _norm(source, external_id, company, title, location, url, description,
          posted_at=None, salary_text=None, employment_type=None, remote_hint=False,
          country=None, explicit_mode=None, department=None, updated_at=None):
    loc_class = classify_location(location, remote_hint, country)
    if explicit_mode:
        work_mode = detect_work_mode(location, description, explicit=explicit_mode)
    elif remote_hint:
        work_mode = "hybrid" if "hybrid" in (location or "").lower() else "remote"
    else:
        work_mode = detect_work_mode(location, description)
    employment_type = infer_employment(description, employment_type)
    return {
        "department": (department or "").strip() or None,
        "work_mode": work_mode,
        "source": source,
        "dedupe_key": f"{source}:{external_id}",
        "fingerprint": fingerprint(company, title),
        "company": (company or "").strip(),
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "loc_class": loc_class,
        "url": url,
        "description": description or "",
        "posted_at": posted_at,                       # first published (YYYY-MM-DD)
        "updated_at": (updated_at or "")[:10] or None,   # last edited by the employer, where the board says
        "salary_text": salary_text,
        "employment_type": employment_type,
    }


def _keep_location(loc_class, filters):
    if loc_class == "non_us":
        return False
    if loc_class == "remote_global" and not filters.get("keep_remote_global", False):
        return False
    keep_state = filters.get("keep_home_state", filters.get("keep_texas_non_austin", True))
    if loc_class == "home_state" and not keep_state:
        return False
    if loc_class == "us_other" and not filters.get("keep_us_other_cities", False):
        return False
    return True


def _get_json(url, params=None, headers=None):
    r = requests.get(url, params=params, headers={**UA, **(headers or {})}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------- Company ATS boards ----------------

def fetch_greenhouse(slug, name, filters):
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        desc = strip_html(html.unescape(j.get("content") or ""))
        if not is_relevant(title, desc):
            continue
        loc = (j.get("location") or {}).get("name", "")
        # first_published is the real posting date; updated_at moves on every edit (Pinterest
        # bulk-edits its board, making 4-month-old reqs look posted yesterday - 2026-10-06)
        posted = (j.get("first_published") or j.get("updated_at") or "")[:10] or None
        dept = "; ".join(d.get("name", "") for d in (j.get("departments") or []) if d.get("name"))
        job = _norm("greenhouse", j["id"], name, title, loc,
                    j.get("absolute_url"), desc, posted_at=posted, department=dept,
                    updated_at=j.get("updated_at"))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_lever(slug, name, filters):
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    out = []
    for j in data:
        title = j.get("text", "")
        desc = (j.get("descriptionPlain") or "") + "\n" + "\n".join(
            (l.get("text", "") + "\n" + strip_html(l.get("content", ""))) for l in (j.get("lists") or [])
        )
        if not is_relevant(title, desc):
            continue
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        wp = (j.get("workplaceType") or "").lower()
        posted = None
        if j.get("createdAt"):
            posted = datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        job = _norm("lever", j["id"], name, title, loc, j.get("hostedUrl"), desc.strip()[:20000],
                    posted_at=posted, employment_type=cats.get("commitment"),
                    remote_hint=(wp == "remote"), country=j.get("country"),
                    explicit_mode=wp if wp in ("remote", "hybrid", "onsite", "on-site") else None,
                    department=" / ".join(filter(None, [cats.get("department"), cats.get("team")])))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_ashby(slug, name, filters):
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                     params={"includeCompensation": "true"})
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        desc = strip_html(j.get("descriptionHtml") or "") or (j.get("descriptionPlain") or "")
        if not is_relevant(title, desc):
            continue
        loc = j.get("location") or ""
        secondary = ", ".join((s.get("location") or "") for s in (j.get("secondaryLocations") or []))
        if secondary:
            loc = f"{loc}; {secondary}"
        comp = j.get("compensation") or {}
        salary = comp.get("compensationTierSummary") or None
        posted = (j.get("publishedAt") or j.get("publishedDate") or "")[:10] or None
        url = j.get("jobUrl") or j.get("applyUrl")
        job = _norm("ashby", j.get("id"), name, title, loc, url, desc, posted_at=posted,
                    updated_at=j.get("updatedAt"),
                    salary_text=salary, employment_type=j.get("employmentType"),
                    remote_hint=bool(j.get("isRemote")),
                    department=" / ".join(filter(None, [j.get("department"), j.get("team")])))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_smartrecruiters(slug, name, filters):
    # Title-only prefilter here (no desc rescue): descriptions cost one extra call per job.
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    out = []
    offset = 0
    while offset <= 300:
        data = _get_json(base, params={"limit": 100, "offset": offset})
        content = data.get("content", [])
        for j in content:
            title = j.get("name", "")
            if not TITLE_RELEVANT.search(title):
                continue
            locd = j.get("location") or {}
            loc = ", ".join(x for x in [locd.get("city"), locd.get("region"), locd.get("country", "").upper()] if x)
            posted = (j.get("releasedDate") or "")[:10] or None
            desc = ""
            try:
                detail = _get_json(f"{base}/{j['id']}")
                sections = ((detail.get("jobAd") or {}).get("sections") or {})
                desc = strip_html(" ".join(s.get("text", "") for s in sections.values() if isinstance(s, dict)))
            except Exception:
                pass
            url = f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"
            job = _norm("smartrecruiters", j["id"], name, title, loc, url, desc, posted_at=posted,
                        remote_hint=bool(locd.get("remote")), country=(locd.get("country") or "").upper())
            if _keep_location(job["loc_class"], filters):
                out.append(job)
        if len(content) < 100:
            break
        offset += 100
    return out


# Search terms for query-driven boards (Workday, Amazon, Oracle) that are too large
# to pull completely.
# Workday's reference ID for country = United States (stable across tenants).
WD_US_FACET = "bc33aa3152ec42d4995f4791a106ed09"


def fetch_workday(slug, name, filters):
    """Workday CXS API (the JSON backend of every myworkdayjobs.com careers site).
    slug format: tenant:wdhost:site  e.g.  crowdstrike:wd5:crowdstrikecareers"""
    tenant, wd, site = slug.split(":")
    cxs = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    headers = {**UA, "Content-Type": "application/json"}
    found = {}
    use_facet = True
    for term in search_terms():
        body = {"appliedFacets": {"locationCountry": [WD_US_FACET]} if use_facet else {},
                "limit": 20, "offset": 0, "searchText": term}
        r = requests.post(f"{cxs}/jobs", json=body, headers=headers, timeout=TIMEOUT)
        if r.status_code == 400 and use_facet:
            # this tenant doesn't accept the country facet — retry unfaceted
            use_facet = False
            body["appliedFacets"] = {}
            r = requests.post(f"{cxs}/jobs", json=body, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        for p in r.json().get("jobPostings", []):
            path = p.get("externalPath")
            if not path or path in found:
                continue
            if TITLE_RELEVANT.search(p.get("title", "")):
                found[path] = p
    out = []
    for path, p in list(found.items())[:50]:      # cap detail fetches per company
        try:
            info = _get_json(f"{cxs}{path}").get("jobPostingInfo") or {}
        except Exception:
            info = {}
        loc = info.get("location") or p.get("locationsText") or ""
        extra = info.get("additionalLocations") or []
        if extra:
            loc += "; " + "; ".join(extra)
        url = info.get("externalUrl") or f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}"
        job = _norm("workday", f"{tenant}:{info.get('jobReqId') or path}", name,
                    p.get("title", ""), loc, url, strip_html(info.get("jobDescription") or ""),
                    posted_at=(info.get("startDate") or "")[:10] or None,
                    employment_type=info.get("timeType"))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_recruitee(slug, name, filters):
    data = _get_json(f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        title = j.get("title", "")
        desc = strip_html((j.get("description") or "") + "\n" + (j.get("requirements") or ""))
        if not is_relevant(title, desc):
            continue
        loc = j.get("location") or ", ".join(filter(None, [j.get("city"), j.get("country")]))
        job = _norm("recruitee", j.get("id"), name, title, loc, j.get("careers_url"), desc,
                    posted_at=(j.get("created_at") or "")[:10],
                    employment_type=j.get("employment_type_kind") or j.get("employment_type"),
                    remote_hint=bool(j.get("remote")), department=j.get("department"))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_workable(slug, name, filters):
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
                     params={"details": "true"})
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        desc = strip_html(j.get("description") or "")
        if not is_relevant(title, desc):
            continue
        loc = ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")]))
        job = _norm("workable", j.get("shortcode") or j.get("code"), name, title, loc,
                    j.get("url") or j.get("shortlink"), desc,
                    posted_at=(j.get("published_on") or "")[:10] or None,
                    employment_type=j.get("employment_type"),
                    remote_hint=bool(j.get("telecommuting")), department=j.get("department"))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_bamboohr(slug, name, filters):
    # Title-only prefilter (descriptions cost one extra call per job, like SmartRecruiters)
    data = _get_json(f"https://{slug}.bamboohr.com/careers/list")
    out = []
    for j in (data.get("result") or []):
        title = j.get("jobOpeningName", "")
        if not TITLE_RELEVANT.search(title):
            continue
        jid = j.get("id")
        locd = j.get("location") or {}
        loc = ", ".join(filter(None, [locd.get("city"), locd.get("state")]))
        desc = ""
        try:
            det = _get_json(f"https://{slug}.bamboohr.com/careers/{jid}/detail")
            desc = strip_html(((det.get("result") or {}).get("jobOpening") or {}).get("description") or "")
        except Exception:
            pass
        job = _norm("bamboohr", jid, name, title, loc,
                    f"https://{slug}.bamboohr.com/careers/{jid}", desc,
                    employment_type=j.get("employmentStatusLabel"),
                    remote_hint=str(j.get("isRemote")).lower() in ("true", "1"),
                    department=j.get("departmentLabel"))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_breezy(slug, name, filters):
    data = _get_json(f"https://{slug}.breezy.hr/json")
    out = []
    for j in data if isinstance(data, list) else []:
        title = j.get("name", "")
        desc = strip_html(j.get("description") or "")
        if not is_relevant(title, desc):
            continue
        locd = j.get("location") or {}
        loc = locd.get("name") or ""
        job = _norm("breezy", j.get("id") or j.get("friendly_id"), name, title, loc,
                    j.get("url"), desc, posted_at=(j.get("published_date") or "")[:10] or None,
                    employment_type=(j.get("type") or {}).get("name"),
                    remote_hint=bool(locd.get("is_remote")),
                    department=(j.get("department") or "") if isinstance(j.get("department"), str)
                               else (j.get("department") or {}).get("name"))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


# ---- Category-2 platforms: rented ATS with no documented feed, read the same way the
# ---- careers page reads them (one JSON request, no auth). See docs/ARCHITECTURE.md.

def _orc_jobs(base, site, name, public_base, source, filters, detail_cap=40):
    """Oracle Recruiting Cloud (HCM) candidate-experience API shared by fetch_oracle and
    fetch_oraclehcm. base = https://host/hcmRestApi/resources/latest, site = CX_1001 etc."""
    out, seen, details_fetched = [], set(), 0
    for term in search_terms():
        try:
            data = _get_json(f"{base}/recruitingCEJobRequisitions", params={
                "onlyData": "true", "expand": "requisitionList.secondaryLocations",
                "finder": f'findReqs;siteNumber={site},keyword="{term}",limit=50,offset=0,'
                          f'sortBy=POSTING_DATES_DESC'})
        except Exception:
            continue
        for item in data.get("items") or []:
            for r in item.get("requisitionList") or []:
                rid = str(r.get("Id"))
                if rid in seen:
                    continue
                seen.add(rid)
                title = r.get("Title", "")
                loc = r.get("PrimaryLocation", "")
                secs = "; ".join(s.get("Name", "") for s in (r.get("secondaryLocations") or []))
                if secs:
                    loc += "; " + secs
                desc = strip_html(r.get("ShortDescriptionStr") or "")
                if not (TITLE_RELEVANT.search(title) or desc_relevant(desc)):
                    continue
                if details_fetched < detail_cap:
                    try:
                        det = _get_json(f"{base}/recruitingCEJobRequisitionDetails", params={
                            "onlyData": "true", "expand": "all",
                            "finder": f'ById;siteNumber={site},Id="{rid}"'})
                        di = (det.get("items") or [{}])[0]
                        full = strip_html(" ".join(filter(None, [
                            di.get("ExternalDescriptionStr"), di.get("CorporateDescriptionStr"),
                            di.get("ExternalQualificationsStr"),
                            di.get("ExternalResponsibilitiesStr")])))
                        if full:
                            desc = full
                        details_fetched += 1
                    except Exception:
                        pass
                job = _norm(source, f"{site}:{rid}" if source != "oracle" else rid, name, title, loc,
                            f"{public_base}/job/{rid}", desc,
                            posted_at=(r.get("PostedDate") or "")[:10] or None)
                if _keep_location(job["loc_class"], filters):
                    out.append(job)
    return out


def fetch_oraclehcm(slug, name, filters):
    """Oracle HCM (Recruiting Cloud) careers sites. slug: apihost:siteNumber[:publichost]
    e.g. jpmc.fa.oraclecloud.com:CX_1001, edbz.fa.us2.oraclecloud.com:CX:careers.ti.com.
    Site numbers come from /hcmRestApi/resources/latest/recruitingCESites (detect.py --url)."""
    parts = slug.split(":")
    host, site = parts[0], parts[1]
    public = parts[2] if len(parts) > 2 else host
    return _orc_jobs(f"https://{host}/hcmRestApi/resources/latest", site,
                     name, f"https://{public}/hcmUI/CandidateExperience/en/sites/{site}"
                     if public == host else f"https://{public}/en/sites/{site}",
                     "oraclehcm", filters)


def _phenom_ddo(html):
    """Phenom job pages embed their data as `phApp.ddo = {...}`; return that object."""
    i = html.find("phApp.ddo")
    if i < 0:
        return {}
    i = html.find("{", i)
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[i:])
        return obj
    except Exception:
        return {}


def fetch_phenom(slug, name, filters):
    """Phenom People careers sites (careers.<company>.com). slug: host[:pathprefix]
    e.g. careers.cisco.com:/global/en, jobs.bswhealth.com:/us/en. Search = POST /widgets
    (refineSearch); full description = the job page's embedded phApp.ddo."""
    parts = slug.split(":", 1)
    host, prefix = parts[0], (parts[1] if len(parts) > 1 else "/us/en")
    headers = {**UA, "Content-Type": "application/json", "Accept": "application/json"}
    found = {}
    for term in search_terms():
        body = {"lang": "en_us", "deviceType": "desktop", "country": "us",
                "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "",
                "subsearch": "", "from": 0, "jobs": True, "counts": True,
                "all_fields": ["category", "country", "state", "city"], "size": 50,
                "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
                "pageId": "page12", "siteType": "external", "keywords": term,
                "global": True, "selected_fields": {}, "locationData": {}}
        try:
            r = requests.post(f"https://{host}/widgets", json=body, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            jobs = ((r.json().get("refineSearch") or {}).get("data") or {}).get("jobs") or []
        except Exception:
            continue
        for j in jobs:
            key = j.get("jobSeqNo") or j.get("reqId")
            if not key or key in found:
                continue
            if TITLE_RELEVANT.search(j.get("title", "")):
                found[key] = j
    out = []
    for key, j in list(found.items())[:50]:
        url = f"https://{host}{prefix}/job/{key}"
        desc = j.get("descriptionTeaser") or ""
        try:
            page = requests.get(url, headers=UA, timeout=TIMEOUT)
            jd = ((_phenom_ddo(page.text).get("jobDetail") or {}).get("data") or {}).get("job") or {}
            if jd.get("description"):
                desc = strip_html(jd["description"])
        except Exception:
            pass
        loc = j.get("cityStateCountry") or j.get("location") or ", ".join(
            filter(None, [j.get("city"), j.get("state"), j.get("country")]))
        remote = (j.get("RemoteType") or "").lower()
        job = _norm("phenom", f"{host}:{key}", name, j.get("title", ""), loc, url, desc,
                    posted_at=(j.get("postedDate") or j.get("dateCreated") or "")[:10] or None,
                    employment_type=j.get("type"),
                    explicit_mode=("remote" if remote == "remote" else "hybrid" if remote == "hybrid" else None))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def _icims_attract(host, name, filters):
    """iCIMS 'Attract' career sites (careers.<company>.com/careers-home): /api/jobs JSON."""
    found = {}
    for term in search_terms():
        try:
            data = _get_json(f"https://{host}/api/jobs", params={
                "keywords": term, "page": 1, "sortBy": "relevance", "descending": "false",
                "internal": "false"})
        except Exception:
            continue
        for row in data.get("jobs") or []:
            d = row.get("data") or row
            rid = str(d.get("req_id") or d.get("slug") or "")
            if not rid or rid in found:
                continue
            if (d.get("country_code") or "US").upper() != "US":   # non-US is never kept anyway
                continue
            if TITLE_RELEVANT.search(d.get("title", "")) or desc_relevant(strip_html(d.get("description") or "")):
                found[rid] = d
    out = []
    for rid, d in found.items():
        loc = ", ".join(filter(None, [d.get("city"), d.get("state")])) or d.get("location_name") or ""
        url = f"https://{host}/careers-home/jobs/{d.get('slug') or rid}"
        job = _norm("icims", f"{host}:{rid}", name, d.get("title", ""), loc, url,
                    strip_html(d.get("description") or ""),
                    posted_at=(d.get("posted_date") or d.get("open_date") or "")[:10] or None,
                    employment_type=d.get("employment_type"),
                    country=("United States" if (d.get("country_code") or "").upper() == "US"
                             else (d.get("country") or d.get("country_code"))))
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_icims(slug, name, filters):
    """iCIMS. slug = careers host. Two flavours, detected by the host name:
    'attract' sites (careers.<company>.com) expose /api/jobs; classic portals
    (<x>-<company>.icims.com) are HTML: /jobs/search?ss=1&searchKeyword=… lists
    /jobs/<id>/<slug>/job links, each job page carries the description."""
    if not slug.endswith(".icims.com"):
        return _icims_attract(slug, name, filters)
    host = slug
    sess = requests.Session()
    sess.headers.update(UA)
    found = {}
    for term in search_terms():
        try:
            r = sess.get(f"https://{host}/jobs/search", params={
                "ss": "1", "searchKeyword": term, "in_iframe": "1"}, timeout=TIMEOUT)
        except Exception:
            continue
        for m in re.finditer(r'href="(https://' + re.escape(host) +
                             r'/jobs/(\d+)/[^"?]+/job)[^"]*"[^>]*>.*?<h3[^>]*>\s*([^<]{3,160})<', r.text, re.S):
            url, jid, title = m.group(1), m.group(2), m.group(3).strip()
            if jid in found or not TITLE_RELEVANT.search(title):
                continue
            found[jid] = (title, url)
    out = []
    for jid, (title, url) in list(found.items())[:50]:
        desc, loc, posted = "", "", None
        try:
            page = sess.get(url, params={"in_iframe": "1"}, timeout=TIMEOUT).text
            body = re.search(r'<div class="iCIMS_JobContent"[^>]*>(.*?)<div class="iCIMS_JobOptions', page, re.S)
            desc = strip_html(body.group(1) if body else page)
            lm = re.search(r'Job Locations</span>\s*<span[^>]*>\s*([^<]+)<', page)
            raw = lm.group(1).strip() if lm else ""
            parts = []                                  # "US-TX-Austin | US-IL-Chicago" -> "Austin, TX; Chicago, IL"
            for piece in raw.split("|"):
                m3 = re.match(r"\s*([A-Z]{2})-([A-Z]{2})-(.+?)\s*$", piece)
                parts.append(f"{m3.group(3)}, {m3.group(2)}" if m3 and m3.group(1) == "US" else piece.strip())
            loc = "; ".join(p for p in parts if p)
            pm = re.search(r'Posted Date</dt>\s*<dd[^>]*>\s*<span title="(\d{1,2}/\d{1,2}/\d{4})', page)
            if pm:
                try:
                    posted = datetime.strptime(pm.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                except ValueError:
                    posted = None
        except Exception:
            pass
        job = _norm("icims", f"{host}:{jid}", name, title, loc, url, desc, posted_at=posted)
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


ATS_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
    "bamboohr": fetch_bamboohr,
    "breezy": fetch_breezy,
    "oraclehcm": fetch_oraclehcm,
    "phenom": fetch_phenom,
    "icims": fetch_icims,
}


# ---------------- Aggregators ----------------

def fetch_adzuna(settings, filters):
    az = settings.get("adzuna", {})
    app_id = az.get("app_id") or os.environ.get("ADZUNA_APP_ID", "")
    app_key = az.get("app_key") or os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        raise RuntimeError("no API key configured (config/settings.yaml -> adzuna)")
    country = az.get("country", "us")
    locations = az.get("locations") or ([az["location"]] if az.get("location") else [f"{home()['city']}, {home()['state_code']}"])
    out, seen = [], set()

    def normalize(j):
        salary = None
        if j.get("salary_min"):
            salary = f"${int(j['salary_min']):,} - ${int(j.get('salary_max') or j['salary_min']):,}"
            if j.get("salary_is_predicted") == "1":
                salary += " (est.)"
        loc = ", ".join((j.get("location") or {}).get("area", [])[::-1][:3])
        return _norm("adzuna", str(j.get("id")), (j.get("company") or {}).get("display_name", ""),
                     j.get("title", ""), loc, j.get("redirect_url"),
                     j.get("description", ""), posted_at=(j.get("created") or "")[:10],
                     salary_text=salary, employment_type=j.get("contract_type"))

    for query in az.get("queries", []):
        for where in list(locations) + [None]:
            for page in range(1, az.get("pages_per_query", 1) + 1):
                params = {
                    "app_id": app_id, "app_key": app_key,
                    "what": query if where else f"{query} remote",
                    "results_per_page": az.get("results_per_page", 50),
                    "max_days_old": az.get("max_days_old", 30),
                    "sort_by": "date",   # freshest first: matters when a query has >50 hits
                    "content-type": "application/json",
                }
                if where:
                    params["where"] = where
                    params["distance"] = az.get("distance_km", 60)
                data = _get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}", params=params)
                for j in data.get("results", []):
                    jid = str(j.get("id"))
                    if jid in seen:
                        continue
                    seen.add(jid)
                    job = normalize(j)
                    if _keep_location(job["loc_class"], filters):
                        out.append(job)

    # Targeted sweeps for companies with no public board (own careers platforms), so
    # their postings can't get squeezed out of the generic queries' 50-newest window.
    sweep_titles = az.get("company_sweep_titles") or ["reliability", "devops", "platform",
                                                      "administrator"]
    for comp in az.get("companies", []) or []:
        name_pat = re.compile(r"\b" + re.escape(comp.lower()) + r"\b")
        for term in sweep_titles:
            try:
                data = _get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params={
                    "app_id": app_id, "app_key": app_key, "company": comp,
                    "title_only": term,     # title-scoped: keeps big employers' ad floods out
                    "results_per_page": 50, "max_days_old": az.get("company_sweep_days", 60),
                    "sort_by": "date", "content-type": "application/json"})
            except Exception:
                continue    # one bad term must not kill the whole Adzuna fetch
            for j in data.get("results", []):
                jid = str(j.get("id"))
                if jid in seen:
                    continue
                seen.add(jid)
                # `company` is a text match ("apple" would match Applebee's) — require
                # the target as a whole word in the display name
                if not name_pat.search(((j.get("company") or {}).get("display_name") or "").lower()):
                    continue
                if not is_relevant(j.get("title", ""), j.get("description", "")):
                    continue
                job = normalize(j)
                if _keep_location(job["loc_class"], filters):
                    out.append(job)
    return out


def fetch_remotive(filters):
    out, seen = [], set()
    for q in ["devops", "sre", "kafka", "elasticsearch", "platform engineer", "observability"]:
        data = _get_json("https://remotive.com/api/remote-jobs", params={"search": q, "limit": 100})
        for j in data.get("jobs", []):
            jid = str(j.get("id"))
            if jid in seen:
                continue
            seen.add(jid)
            req_loc = j.get("candidate_required_location") or "Remote"
            if NON_US.search(req_loc) and not US_HINT.search(req_loc):
                continue
            job = _norm("remotive", jid, j.get("company_name", ""), j.get("title", ""),
                        f"Remote ({req_loc})", j.get("url"), strip_html(j.get("description", "")),
                        posted_at=(j.get("publication_date") or "")[:10],
                        salary_text=j.get("salary") or None,
                        employment_type=j.get("job_type"), remote_hint=True,
                        country="US" if US_HINT.search(req_loc) else None)
            if _keep_location(job["loc_class"], filters):
                out.append(job)
    return out


def fetch_remoteok(filters):
    data = _get_json("https://remoteok.com/api")
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("id"):
            continue
        title = j.get("position") or j.get("title") or ""
        tags = " ".join(j.get("tags") or [])
        desc_ok = desc_relevant(strip_html(j.get("description", "")))
        if not (TITLE_RELEVANT.search(title) or TITLE_RELEVANT.search(tags) or desc_ok):
            continue
        loc = j.get("location") or "Remote"
        if NON_US.search(loc) and not US_HINT.search(loc):
            continue
        salary = None
        if j.get("salary_min"):
            salary = f"${int(j['salary_min']):,} - ${int(j.get('salary_max') or j['salary_min']):,}"
        job = _norm("remoteok", j["id"], j.get("company", ""), title, f"Remote ({loc})",
                    j.get("url"), strip_html(j.get("description", "")),
                    posted_at=(j.get("date") or "")[:10], salary_text=salary, remote_hint=True,
                    country="US" if US_HINT.search(loc) else None)
        if _keep_location(job["loc_class"], filters):
            out.append(job)
    return out


def fetch_amazon(filters):
    """amazon.jobs public search JSON (Amazon/AWS have no ATS board)."""
    out, seen = [], set()
    for term in search_terms():
        for loc_extra in ([("loc_query", f"{home()['city']}, {home()['state']}, United States")], []):
            params = [("base_query", term), ("normalized_country_code[]", "USA"),
                      ("result_limit", 100), ("offset", 0), ("sort", "recent")] + loc_extra
            data = _get_json("https://www.amazon.jobs/en/search.json", params=params)
            for j in data.get("jobs", []):
                jid = str(j.get("id_icims") or j.get("id"))
                if jid in seen:
                    continue
                seen.add(jid)
                title = j.get("title", "")
                desc = "\n".join(filter(None, [
                    j.get("description"), j.get("basic_qualifications"),
                    j.get("preferred_qualifications")]))
                if not is_relevant(title, desc):
                    continue
                posted = None
                try:
                    posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass
                job = _norm("amazon", jid, "Amazon", title,
                            j.get("normalized_location") or j.get("location") or "",
                            "https://www.amazon.jobs" + (j.get("job_path") or ""), desc,
                            posted_at=posted, employment_type=j.get("job_schedule_type"),
                            country="US")
                if _keep_location(job["loc_class"], filters):
                    out.append(job)
    return out


ORACLE_ORC = "https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest"


def fetch_oracle(filters):
    """Oracle's own careers site API (Oracle Recruiting Cloud, public)."""
    out, seen, details_fetched = [], set(), 0
    for term in search_terms():
        data = _get_json(f"{ORACLE_ORC}/recruitingCEJobRequisitions", params={
            "onlyData": "true", "expand": "requisitionList.secondaryLocations",
            "finder": f'findReqs;siteNumber=CX_45001,keyword="{term}",limit=50,offset=0,'
                      f'sortBy=POSTING_DATES_DESC'})
        for item in data.get("items") or []:
            for r in item.get("requisitionList") or []:
                rid = str(r.get("Id"))
                if rid in seen:
                    continue
                seen.add(rid)
                title = r.get("Title", "")
                loc = r.get("PrimaryLocation", "")
                secs = "; ".join(s.get("Name", "") for s in (r.get("secondaryLocations") or []))
                if secs:
                    loc += "; " + secs
                desc = strip_html(r.get("ShortDescriptionStr") or "")
                if not (TITLE_RELEVANT.search(title) or desc_relevant(desc)):
                    continue
                if details_fetched < 40:
                    try:
                        det = _get_json(f"{ORACLE_ORC}/recruitingCEJobRequisitionDetails", params={
                            "onlyData": "true", "expand": "all",
                            "finder": f'ById;siteNumber=CX_45001,Id="{rid}"'})
                        di = (det.get("items") or [{}])[0]
                        full = strip_html(" ".join(filter(None, [
                            di.get("ExternalDescriptionStr"), di.get("CorporateDescriptionStr"),
                            di.get("ExternalQualificationsStr"),
                            di.get("ExternalResponsibilitiesStr")])))
                        if full:
                            desc = full
                        details_fetched += 1
                    except Exception:
                        pass
                job = _norm("oracle", rid, "Oracle", title, loc,
                            f"https://careers.oracle.com/jobs/#en/sites/jobsearch/job/{rid}",
                            desc, posted_at=(r.get("PostedDate") or "")[:10] or None)
                if _keep_location(job["loc_class"], filters):
                    out.append(job)
    return out


def fetch_hn(filters):
    """Monthly 'Ask HN: Who is hiring?' thread via the official Algolia API.
    Top-level comments are job posts, conventionally 'Company | Role | Location | ...'."""
    data = _get_json("https://hn.algolia.com/api/v1/search_by_date",
                     params={"query": "Ask HN: Who is hiring?",
                             "tags": "story,author_whoishiring", "hitsPerPage": 5})
    story = next((h for h in data.get("hits", [])
                  if "who is hiring" in (h.get("title") or "").lower()), None)
    if not story:
        return []
    sid = story["objectID"]
    out, page = [], 0
    while page < 3:
        c = _get_json("https://hn.algolia.com/api/v1/search_by_date",
                      params={"tags": f"comment,story_{sid}", "hitsPerPage": 1000, "page": page})
        hits = c.get("hits", [])
        for h in hits:
            if str(h.get("parent_id")) != str(sid):
                continue                    # only top-level comments are job posts
            text = strip_html(h.get("comment_text") or "")
            if not text:
                continue
            first = text.split("\n", 1)[0][:220]
            parts = [p.strip() for p in first.split("|")]
            company = (parts[0][:60] if parts and parts[0] else "HN post")
            title = parts[1][:120] if len(parts) > 1 else first[:120]
            loc_bits = " | ".join(parts[2:5]) or first
            # posts don't follow the segment order reliably — if the location segments
            # don't classify, fall back to the whole first line
            if classify_location(loc_bits) == "unknown" and classify_location(first) != "unknown":
                loc_bits = first[:140]
            if not is_relevant(first, text):
                continue
            job = _norm("hn", h["objectID"], company, title, loc_bits,
                        f"https://news.ycombinator.com/item?id={h['objectID']}",
                        text, posted_at=(h.get("created_at") or "")[:10])
            if _keep_location(job["loc_class"], filters):
                out.append(job)
        if len(hits) < 1000:
            break
        page += 1
    return out


def fetch_themuse(filters):
    out = []
    for location in [f"{home()['city']}, {home()['state_code']}", "Flexible / Remote"]:
        for page in range(0, 4):
            try:
                data = _get_json("https://www.themuse.com/api/public/jobs",
                                 params={"page": page, "location": location,
                                         "category": ["Software Engineering", "IT"]})
            except Exception:
                break
            results = data.get("results", [])
            for j in results:
                title = j.get("name", "")
                contents = strip_html(j.get("contents", ""))
                if not is_relevant(title, contents):
                    continue
                locs = "; ".join(l.get("name", "") for l in (j.get("locations") or []))
                job = _norm("themuse", j.get("id"), (j.get("company") or {}).get("name", ""),
                            title, locs, (j.get("refs") or {}).get("landing_page"),
                            contents,
                            posted_at=(j.get("publication_date") or "")[:10],
                            remote_hint="remote" in locs.lower() or "flexible" in locs.lower())
                if _keep_location(job["loc_class"], filters):
                    out.append(job)
            if page + 1 >= data.get("page_count", 0):
                break
    return out


# ---------------- Orchestration ----------------

LOCAL_SETTINGS_PATH = os.path.join(BASE_DIR, "config", "local.yaml")


def _deep_merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


_YAML_CACHE = {}          # path -> (mtime, parsed); yaml.safe_load of companies.yaml costs ~250 ms
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _yaml_cached(path):
    """Parse a YAML file once per modification; callers get a deep copy they may mutate.
    (Before this, /api/resume/status parsed companies.yaml 12x per poll = 3 s, 2026-10-03.)"""
    try:
        mtime = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return None
    hit = _YAML_CACHE.get(path)
    if not hit or hit[0] != mtime:
        with open(path, encoding="utf-8") as f:
            hit = (mtime, yaml.load(f, Loader=_YAML_LOADER))
        _YAML_CACHE[path] = hit
    return copy.deepcopy(hit[1])


def load_settings():
    """settings.yaml with config/local.yaml (written by the Settings page) merged on top."""
    settings = _yaml_cached(os.path.join(BASE_DIR, "config", "settings.yaml"))
    _deep_merge(settings, _yaml_cached(LOCAL_SETTINGS_PATH) or {})
    return settings


def save_local_settings(patch):
    """Merge `patch` into config/local.yaml. Returns the new merged settings."""
    local = {}
    if os.path.exists(LOCAL_SETTINGS_PATH):
        with open(LOCAL_SETTINGS_PATH, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
    _deep_merge(local, patch)
    with open(LOCAL_SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(local, f, sort_keys=False, allow_unicode=True)
    return load_settings()


def load_companies():
    return (_yaml_cached(os.path.join(BASE_DIR, "config", "companies.yaml")) or {}).get("companies", [])


def _too_old(job, max_age_days):
    if not job.get("posted_at") or not max_age_days:
        return False
    try:
        posted = datetime.strptime(job["posted_at"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return posted < datetime.now(timezone.utc) - timedelta(days=max_age_days)
    except ValueError:
        return False


def run_all(progress=lambda msg: None, only_companies=None):
    """Run every fetcher. Returns (jobs, stats). With only_companies (list of names),
    fetches just those boards and skips the aggregators."""
    settings = load_settings()
    filters = settings.get("filters", {})
    max_age = filters.get("max_age_days")
    max_age_boards = filters.get("max_age_days_boards")     # direct boards keep evergreen reqs open for a year+
    stats = {}
    batches = []

    companies = load_companies()
    if only_companies:
        wanted = {n.lower() for n in only_companies}
        companies = [c for c in companies if c["name"].lower() in wanted]
    total = len(companies)
    _report(progress, f"Fetching {total} company boards...", 0, total)
    board_fail = []
    board_kept = {ats: 0 for ats in ATS_FETCHERS}
    company_stats = []

    def one_board(c):
        ats, slug, name = c["ats"], c["slug"], c["name"]
        fetcher = ATS_FETCHERS.get(ats)
        if not fetcher:
            return c, None, f"unknown ats '{ats}'"
        try:
            return c, [j for j in fetcher(slug, name, filters) if not _too_old(j, max_age_boards)], None
        except Exception as e:
            return c, None, str(e)

    # Boards are independent HTTP calls (each fetcher builds its own session), so they run in a
    # pool; results are folded back on this thread so the stats/batches stay single-writer.
    # as_completed (not pool.map): a slow Workday/iCIMS board must not freeze the counter.
    # Order of company_stats follows completion order — nothing downstream depends on it.
    done = 0
    workers = int((settings.get("refresh") or {}).get("board_workers") or BOARD_WORKERS)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one_board, c) for c in companies]
        for fut in as_completed(futures):
            c, jobs, err = fut.result()
            ats, slug, name = c["ats"], c["slug"], c["name"]
            done += 1
            if err is not None:
                board_fail.append(f"{name} ({ats}:{slug}): {err}")
                company_stats.append({"name": name, "ats": ats, "slug": slug, "error": err})
                _report(progress, f"{name} ({ats}): FAILED - {err}", done, total, failed=len(board_fail))
                continue
            board_kept[ats] += len(jobs)
            batches.extend(jobs)
            company_stats.append({"name": name, "ats": ats, "slug": slug, "kept": len(jobs)})
            _report(progress, f"{name} ({ats}): {len(jobs)} relevant", done, total, failed=len(board_fail))
    for ats, kept in board_kept.items():
        stats[ats] = {"kept": kept}
    stats["companies"] = company_stats
    if board_fail:
        stats["board_failures"] = board_fail

    if only_companies:
        return batches, stats

    aggregators = [
        ("adzuna", lambda: fetch_adzuna(settings, filters)),
        ("amazon", lambda: fetch_amazon(filters)),
        ("oracle", lambda: fetch_oracle(filters)),
        ("remotive", lambda: fetch_remotive(filters)),
        ("remoteok", lambda: fetch_remoteok(filters)),
        ("themuse", lambda: fetch_themuse(filters)),
        ("hn", lambda: fetch_hn(filters)),
    ]
    n_agg = len(aggregators)
    for i, (name, fn) in enumerate(aggregators):
        if name == "adzuna" and settings.get("adzuna", {}).get("enabled", True) is False:
            stats[name] = {"kept": 0, "skipped": "disabled in Settings"}
            _report(progress, "Adzuna: disabled in Settings, skipped", i + 1, n_agg, phase="aggregators")
            continue
        _report(progress, f"Fetching {name}...", i, n_agg, phase="aggregators")
        try:
            jobs = [j for j in fn() if not _too_old(j, max_age)]
            stats[name] = {"kept": len(jobs)}
            batches.extend(jobs)
            _report(progress, f"{name}: {len(jobs)} relevant", i + 1, n_agg, phase="aggregators")
        except Exception as e:
            stats[name] = {"error": str(e)}
            _report(progress, f"{name}: FAILED - {e}", i + 1, n_agg, phase="aggregators")

    return batches, stats
