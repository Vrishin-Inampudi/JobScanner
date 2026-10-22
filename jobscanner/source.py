"""Fetch internship lists shown on intern-list.com.

intern-list.com names its lists (``?k=cs`` = Cybersecurity) and embeds each one from
jobright.ai, whose JSON API returns jobs newest first with a posting timestamp.
"""

from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

SITE_URL = "https://www.intern-list.com/"
JOBS_API = "https://jobright.ai/swan/mini-sites/list"
JOB_URL = "https://jobright.ai/jobs/info/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
PAGE_SIZE = 50
MAX_PAGES = 40
TIMEOUT = 60

# jobright's postedAt is US Pacific wall-clock time encoded as if it were UTC
# (verified 2026-10-22: the newest jobs on every list looked ~8h old). If that is ever
# fixed, Pacific-converted times land in the future and we fall back to plain UTC.
SOURCE_TZ = ZoneInfo("America/Los_Angeles")
FUTURE_SLACK = timedelta(minutes=10)


class SourceError(RuntimeError):
    """The site or job API returned something we don't understand."""


@dataclass(frozen=True)
class Posting:
    list_key: str
    row_id: str
    title: str
    company: str
    location: str
    salary: str
    size: str | None
    date: str
    work_model: str | None
    apply_url: str
    posted_at: datetime | None = None  # UTC

    @property
    def uid(self) -> str:
        return f"{self.list_key}:{self.row_id}"

    @property
    def job_keys(self) -> tuple[str, ...]:
        """Identities used to avoid repeats: the job link, and company|title.

        The same job can appear in several lists, and the site has re-posted existing
        jobs under a new link, so a job counts as seen if either key has been seen.
        """
        fingerprint = "|".join(" ".join(x.lower().split()) for x in (self.company, self.title))
        if not self.apply_url:
            return (fingerprint,)
        u = urlsplit(self.apply_url)
        return (f"{u.netloc.lower()}{u.path.rstrip('/')}", fingerprint)


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


_H2 = re.compile(r"<h2\b([^>]*\bdata-job-path=[^>]*)>", re.I)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_JOB_PATH = re.compile(r"^/us/([a-z0-9_]+)$")


def parse_list_map(page_html: str) -> dict[str, str]:
    """Map each US list's short key (the ``?k=`` value) to its jobright category."""
    lists: dict[str, str] = {}
    for m in _H2.finditer(page_html):
        attrs = {k.lower(): htmllib.unescape(v) for k, v in _ATTR.findall(m.group(1))}
        path, key = _JOB_PATH.match(attrs.get("data-job-path", "")), attrs.get("short-link")
        if path and key:
            lists.setdefault(key, f"intern:us:{path.group(1)}")
    if not lists:
        raise SourceError("no job lists found on intern-list.com; page layout may have changed")
    return lists


def fetch_list_map(session: requests.Session) -> dict[str, str]:
    resp = session.get(SITE_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_list_map(resp.text)


def _as_pacific(ms: int) -> datetime:
    wall = datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None)
    return wall.replace(tzinfo=SOURCE_TZ).astimezone(timezone.utc)


def posted_at(ms: int, pacific: bool) -> datetime:
    return _as_pacific(ms) if pacific else datetime.fromtimestamp(ms / 1000, timezone.utc)


def stamps_are_pacific(newest_ms: int, now: datetime) -> bool:
    return _as_pacific(newest_ms) <= now + FUTURE_SLACK


def parse_jobs(payload: dict, list_key: str, pacific: bool = True) -> list[Posting]:
    if not payload.get("success"):
        raise SourceError(f"job API error for list '{list_key}': {payload.get('errorMsg')}")
    try:
        jobs = payload["result"]["jobList"] or []
    except (KeyError, TypeError) as e:
        raise SourceError(f"unexpected job API response for list '{list_key}': missing {e}") from e

    postings = []
    for job in jobs:
        props = job.get("properties") or {}
        job_id = str(job.get("jobId") or "").replace('"', "")
        if not re.fullmatch(r"[0-9a-f]{24}", job_id) or not job.get("postedAt"):
            raise SourceError(f"list '{list_key}': job without a valid id/postedAt: {str(job)[:200]}")
        when = posted_at(job["postedAt"], pacific)
        postings.append(
            Posting(
                list_key=list_key,
                row_id=job_id,
                title=(props.get("title") or "").strip(),
                company=(props.get("company") or "").strip(),
                location=(props.get("location") or "").strip(),
                salary=(props.get("salary") or "").strip(),
                size=props.get("companySize"),
                date=when.astimezone(SOURCE_TZ).date().isoformat(),
                work_model=props.get("workModel"),
                apply_url=JOB_URL + job_id,
                posted_at=when,
            )
        )
    return postings


def fetch_jobs(session: requests.Session, category: str, list_key: str, since: datetime,
               now: datetime | None = None) -> list[Posting]:
    """Jobs in a list posted at or after ``since``, newest first."""
    now = now or datetime.now(timezone.utc)
    pacific = True
    postings: list[Posting] = []
    for page in range(MAX_PAGES):
        resp = session.post(
            JOBS_API,
            params={"position": page * PAGE_SIZE, "count": PAGE_SIZE},
            json={"category": category},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw = (payload.get("result") or {}).get("jobList") or []
        if page == 0:
            if not raw:
                raise SourceError(f"list '{list_key}' ({category}) returned no jobs")
            pacific = stamps_are_pacific(max(j.get("postedAt") or 0 for j in raw), now)
        batch = parse_jobs(payload, list_key, pacific)
        postings += [p for p in batch if p.posted_at >= since]
        if len(raw) < PAGE_SIZE or not batch or min(p.posted_at for p in batch) < since:
            break
    return postings
