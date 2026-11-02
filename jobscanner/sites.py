"""Read internships straight from company career sites (Workday and Greenhouse job boards).

Jobs show up here before aggregators copy them. Neither board gives an exact posting
time for every job, so newness comes from what we've seen before (see ``__main__``).
"""

from __future__ import annotations

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
