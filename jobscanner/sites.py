"""Read internships straight from company career sites.

Supported: Workday, Greenhouse and Oracle Recruiting job boards (used by many companies),
plus Amazon, Google and Apple's own career sites. Jobs show up here before aggregators
copy them. Not every site gives an exact posting time, so newness comes from what we've
seen before (see ``__main__``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests

from .source import TIMEOUT, Posting, SourceError

WORKDAY_PAGE = 20          # the most Workday returns per request
WORKDAY_MAX_PAGES = 25
RECENT = timedelta(days=7)  # ignore jobs older than this; they aren't news
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
_BOARD = re.compile(r"^[a-z0-9_-]+$", re.I)
_SITE = re.compile(r"^/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)/?$")
# Facets that mark a job as an internship, by the names companies commonly use.
INTERN_FACETS = ("workerSubType", "Job_Family", "jobFamilyGroup", "timeType")


def workday_parts(url: str) -> tuple[str, str, str]:
    """'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite' -> (host, tenant, site)."""
    u = urlsplit(url)
    m = _SITE.match(u.path)
    if u.scheme != "https" or not u.hostname or not u.hostname.endswith(".myworkdayjobs.com") or not m:
        raise SourceError(f"not a Workday careers URL: {url!r}")
    return u.hostname, u.hostname.split(".")[0], m.group(1)


def workday_posted(text: str, now: datetime) -> datetime | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' -> that day; '30+ Days Ago' -> None."""
    t = (text or "").lower()
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    else:
        m = re.search(r"(\d+)\+?\s+days?", t)
        if not m or "+" in t:
            return None
        days = int(m.group(1))
    return (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def _intern_facet(facets: list) -> tuple[str, str] | None:
    """Find the (facet, value id) that selects internships, e.g. workerSubType = 'Intern (Fixed Term)'."""
    for name in INTERN_FACETS:
        for f in facets or []:
            if f.get("facetParameter") != name:
                continue
            values = [x for v in f.get("values") or [] for x in (v.get("values") or [v])]
            for v in values:
                if re.search(r"\bintern", str(v.get("descriptor") or ""), re.I) and v.get("id"):
                    return name, v["id"]
    return None


def fetch_workday(session: requests.Session, url: str, company: str, list_key: str,
                  wants=lambda title: True, now: datetime | None = None) -> list[Posting]:
    """Recent internships on a Workday careers site. Locations come from each wanted job's page."""
    now = now or datetime.now(timezone.utc)
    host, tenant, site = workday_parts(url)
    api = f"https://{host}/wday/cxs/{tenant}/{site}"

    def page(offset: int, facets: dict) -> dict:
        resp = session.post(f"{api}/jobs", json={"limit": WORKDAY_PAGE, "offset": offset, "searchText": "",
                                                 "appliedFacets": facets}, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    first = page(0, {})
    facet = _intern_facet(first.get("facets"))
    if not facet:
        raise SourceError(f"{company}: no internship filter found on {url}")
    facets = {facet[0]: [facet[1]]}

    postings = []
    for n in range(WORKDAY_MAX_PAGES):
        data = page(n * WORKDAY_PAGE, facets)
        jobs = data.get("jobPostings") or []
        for job in jobs:
            title, path = str(job.get("title") or "").strip(), str(job.get("externalPath") or "")
            when = workday_posted(job.get("postedOn"), now)
            if not title or not path.startswith("/") or when is None or now - when > RECENT or not wants(title):
                continue
            location = str(job.get("locationsText") or "")
            detail = session.get(f"{api}{path}", timeout=TIMEOUT)
            if detail.ok:
                info = detail.json().get("jobPostingInfo") or {}
                location = "; ".join(x for x in [info.get("location"), *(info.get("additionalLocations") or [])] if x) or location
            postings.append(Posting(
                list_key=list_key, row_id=path.rsplit("_", 1)[-1], title=title, company=company,
                location=_workday_location(location), salary="", size=None, date=f"{when:%b} {when.day}",
                work_model=None, apply_url=f"https://{host}/{site}{path}", posted_at=None, source=f"{company} careers site",
            ))
        if len(jobs) < WORKDAY_PAGE:
            break
    return postings


def _workday_location(text: str) -> str:
    """'US, CA, Santa Clara' -> 'Santa Clara, CA'; 'USA - Sunnyvale, CA' -> 'Sunnyvale, CA'."""
    out = []
    for part in text.split(";"):
        part = part.strip()
        m = re.fullmatch(r"US,\s*([A-Z]{2}),\s*(.+)", part)
        if m:
            part = f"{m.group(2)}, {m.group(1)}"
        part = re.sub(r"^USA?\s*-\s*", "", part)
        if part:
            out.append(part)
    return "; ".join(out)


def fetch_greenhouse(session: requests.Session, board: str, company: str, list_key: str,
                     wants=lambda title: True, now: datetime | None = None) -> list[Posting]:
    """Recent jobs on a Greenhouse board, using each job's first-published time."""
    if not _BOARD.match(board):
        raise SourceError(f"{company}: bad Greenhouse board name {board!r}")
    now = now or datetime.now(timezone.utc)
    resp = session.get(GREENHOUSE_API.format(board=board), timeout=TIMEOUT)
    resp.raise_for_status()
    postings = []
    for job in resp.json().get("jobs") or []:
        title, url = str(job.get("title") or "").strip(), str(job.get("absolute_url") or "")
        stamp = job.get("first_published") or job.get("updated_at")
        if not title or not url.startswith("https://") or not stamp or not wants(title):
            continue
        when = datetime.fromisoformat(stamp).astimezone(timezone.utc)
        if now - when > RECENT:
            continue
        postings.append(Posting(
            list_key=list_key, row_id=str(job.get("id") or url), title=title, company=company,
            location=str((job.get("location") or {}).get("name") or ""), salary="", size=None,
            date=f"{when:%b} {when.day}", work_model=None, apply_url=url, posted_at=when,
            source=f"{company} careers site",
        ))
    return postings


ORACLE_PAGE = 50
ORACLE_MAX_PAGES = 10
_ORACLE_SITE = re.compile(r"^/hcmUI/CandidateExperience/[a-z-]+/sites/([A-Za-z0-9_]+)/?$")


def oracle_parts(url: str) -> tuple[str, str]:
    """'https://edel.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001' -> (host, site)."""
    u = urlsplit(url)
    m = _ORACLE_SITE.match(u.path)
    if u.scheme != "https" or not u.hostname or not u.hostname.endswith(".oraclecloud.com") or not m:
        raise SourceError(f"not an Oracle Recruiting careers URL: {url!r}")
    return u.hostname, m.group(1)


def _day(text: str, fmt: str) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def fetch_oracle(session: requests.Session, url: str, company: str, list_key: str,
                 wants=lambda title: True, now: datetime | None = None) -> list[Posting]:
    """Recent jobs on an Oracle Recruiting site, newest first, until they're older than RECENT."""
    now = now or datetime.now(timezone.utc)
    host, site = oracle_parts(url)
    postings = []
    for n in range(ORACLE_MAX_PAGES):
        finder = f"findReqs;siteNumber={site},sortBy=POSTING_DATES_DESC,limit={ORACLE_PAGE},offset={n * ORACLE_PAGE}"
        resp = session.get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
                           params={"onlyData": "true", "finder": finder}, timeout=TIMEOUT)
        resp.raise_for_status()
        items = resp.json().get("items") or [{}]
        jobs = items[0].get("requisitionList") or []
        too_old = False
        for job in jobs:
            when = _day(job.get("PostedDate"), "%Y-%m-%d")
            if when is None or now - when > RECENT:
                too_old = True
                continue
            title = str(job.get("Title") or "").strip()
            if not title or not wants(title) or not job.get("Id"):
                continue
            postings.append(Posting(
                list_key=list_key, row_id=str(job["Id"]), title=title, company=company,
                location=str(job.get("PrimaryLocation") or ""), salary="", size=None, date=f"{when:%b} {when.day}",
                work_model=None, apply_url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job['Id']}",
                posted_at=None, source=f"{company} careers site",
            ))
        if too_old or len(jobs) < ORACLE_PAGE:
            break
    return postings


AMAZON_API = "https://www.amazon.jobs/en/search.json"


def fetch_amazon(session: requests.Session, company: str, list_key: str,
                 wants=lambda title: True, now: datetime | None = None) -> list[Posting]:
    """Recent US internships on amazon.jobs."""
    now = now or datetime.now(timezone.utc)
    resp = session.get(AMAZON_API, params={"base_query": "intern", "country": "USA", "sort": "recent",
                                           "result_limit": 100}, timeout=TIMEOUT)
    resp.raise_for_status()
    postings = []
    for job in resp.json().get("jobs") or []:
        title, path = str(job.get("title") or "").strip(), str(job.get("job_path") or "")
        when = _day(job.get("posted_date"), "%B %d, %Y")
        if not title or not path.startswith("/") or when is None or now - when > RECENT or not wants(title):
            continue
        city, state = job.get("city"), job.get("state")
        postings.append(Posting(
            list_key=list_key, row_id=str(job.get("id_icims") or path), title=title, company=company,
            location=f"{city}, {state}" if city and state else str(job.get("normalized_location") or ""),
            salary="", size=None, date=f"{when:%b} {when.day}", work_model=None,
            apply_url=f"https://www.amazon.jobs{path}", posted_at=None, source=f"{company} careers site",
        ))
    return postings


GOOGLE_SEARCH = "https://www.google.com/about/careers/applications/jobs/results/"
_GOOGLE_DATA = re.compile(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel", re.S)


def fetch_google(session: requests.Session, company: str, list_key: str,
                 wants=lambda title: True, now: datetime | None = None) -> list[Posting]:
    """Recent US internships on Google Careers, read from the data embedded in its results page."""
    now = now or datetime.now(timezone.utc)
    postings = []
    for page in range(1, 6):
        resp = session.get(GOOGLE_SEARCH, params={"target_level": "INTERN_AND_APPRENTICE", "location": "United States",
                                                  "sort_by": "date", "page": page}, timeout=TIMEOUT)
        resp.raise_for_status()
        m = _GOOGLE_DATA.search(resp.text)
        if not m:
            raise SourceError(f"{company}: job data not found on Google Careers; page layout may have changed")
        try:
            jobs = json.loads(m.group(1))[0] or []
        except (ValueError, IndexError, TypeError) as e:
            raise SourceError(f"{company}: unreadable Google Careers data: {e}") from e
        for job in jobs:
            try:
                job_id, title = str(job[0]), str(job[1]).strip()
                when = datetime.fromtimestamp(job[12][0], timezone.utc)
                places = [str(loc[0]) for loc in job[9] or []]
            except (IndexError, TypeError, ValueError):
                continue
            if not re.fullmatch(r"\d+", job_id) or now - when > RECENT or not wants(title):
                continue
            postings.append(Posting(
                list_key=list_key, row_id=job_id, title=title, company=company, location="; ".join(places),
                salary="", size=None, date=f"{when:%b} {when.day}", work_model=None,
                apply_url=f"{GOOGLE_SEARCH}{job_id}", posted_at=when, source=f"{company} careers site",
            ))
        if len(jobs) < 20:
            break
    return postings


APPLE_SEARCH = "https://jobs.apple.com/en-us/search"
_APPLE_DATA = re.compile(r'window\.__staticRouterHydrationData = JSON\.parse\("(.*?)"\);', re.S)


def fetch_apple(session: requests.Session, company: str, list_key: str,
                wants=lambda title: True, now: datetime | None = None) -> list[Posting]:
    """Recent US internships on jobs.apple.com, read from the data embedded in its search page."""
    now = now or datetime.now(timezone.utc)
    postings = []
    for page in range(1, 6):
        resp = session.get(APPLE_SEARCH, params={"team": "internships-STDNT-INTRN", "location": "united-states-USA",
                                                 "sort": "newest", "page": page}, timeout=TIMEOUT)
        resp.raise_for_status()
        m = _APPLE_DATA.search(resp.text)
        try:
            results = json.loads(json.loads(f'"{m.group(1)}"'))["loaderData"]["search"]["searchResults"] or []
        except (AttributeError, ValueError, KeyError, TypeError) as e:
            raise SourceError(f"{company}: job data not found on jobs.apple.com; page layout may have changed") from e
        for job in results:
            title, pid = str(job.get("postingTitle") or "").strip(), str(job.get("positionId") or "")
            try:
                when = datetime.fromisoformat(str(job.get("postDateInGMT"))[:19]).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if not title or not re.fullmatch(r"[\w-]+", pid) or now - when > RECENT or not wants(title):
                continue
            places = [", ".join(x for x in (loc.get("city"), loc.get("stateProvince")) if x) or str(loc.get("name") or "")
                      for loc in job.get("locations") or []]
            slug = re.sub(r"[^a-z0-9-]", "", str(job.get("transformedPostingTitle") or ""))
            postings.append(Posting(
                list_key=list_key, row_id=pid, title=title, company=company, location="; ".join(places),
                salary="", size=None, date=f"{when:%b} {when.day}", work_model=None,
                apply_url=f"https://jobs.apple.com/en-us/details/{pid}/{slug}", posted_at=when,
                source=f"{company} careers site",
            ))
        if len(results) < 20:
            break
    return postings


BUILTIN = {"amazon": fetch_amazon, "google": fetch_google, "apple": fetch_apple}


def fetch_site(session: requests.Session, spec, now: datetime | None = None) -> list[Posting]:
    """Fetch one company site described by a config ListSpec."""
    if spec.workday:
        return fetch_workday(session, spec.workday, spec.company, spec.key, spec.wants, now=now)
    if spec.greenhouse:
        return fetch_greenhouse(session, spec.greenhouse, spec.company, spec.key, spec.wants, now=now)
    if spec.oracle:
        return fetch_oracle(session, spec.oracle, spec.company, spec.key, spec.wants, now=now)
    return BUILTIN[spec.careers](session, spec.company, spec.key, spec.wants, now=now)
