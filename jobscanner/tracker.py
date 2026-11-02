"""Application tracker: "Applied" / "Not interested" links in each email, kept in a Google Sheet.

A small Apps Script web app (tracker/Code.gs) records each click as a row in your Sheet.
Each run reads the rows back, so jobs you've marked never come back, and "Not interested"
also mutes the same role at the same company in other cities. Optional: without the
TRACKER_URL and TRACKER_TOKEN secrets, emails just don't show the links.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

from .source import TIMEOUT, Posting

log = logging.getLogger("jobscanner")

APPLIED, SKIP = "applied", "not interested"


def role_key(company: str, title: str) -> str:
    """company|title without a trailing location, so one role posted in many cities is one key.

    'University - Cyber Security Intern - Honolulu, HI' -> '... cyber security intern'.
    """
    title = re.sub(r"\s*[-–,(]\s*[A-Za-z .'-]+,\s*[A-Z]{2}\)?\s*$", "", title)
    return "|".join(" ".join(re.sub(r"[^a-z0-9]+", " ", x.lower()).split()) for x in (company, title))


@dataclass
class Marks:
    jobs: dict[str, str] = field(default_factory=dict)   # job key -> status
    muted: set[str] = field(default_factory=set)         # role keys marked not interested

    def hides(self, p: Posting) -> bool:
        return any(k in self.jobs for k in p.job_keys) or role_key(p.company, p.title) in self.muted


def _config(env) -> tuple[str, str] | None:
    url, token = env.get("TRACKER_URL", "").strip(), env.get("TRACKER_TOKEN", "").strip()
    if not url or not token:
        return None
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname != "script.google.com":
        log.warning("TRACKER_URL must be a https://script.google.com web app URL; tracker disabled")
        return None
    return url, token


def links(p: Posting, env=os.environ) -> dict[str, str] | None:
    """{'applied': url, 'skip': url} for one posting's email card, or None if not set up."""
    cfg = _config(env)
    if not cfg:
        return None
    url, token = cfg
    base = {"t": token, "id": p.job_keys[0], "role": role_key(p.company, p.title), "company": p.company,
            "title": p.title, "location": p.location.replace("\n", "; "), "link": p.apply_url}
    return {"applied": f"{url}?{urlencode({**base, 'a': 'applied'})}",
            "skip": f"{url}?{urlencode({**base, 'a': 'skip'})}"}


def load(session, env=os.environ) -> Marks:
    """Read your marks from the Sheet. Problems are logged, never fatal: alerts matter more."""
    cfg = _config(env)
    if not cfg:
        return Marks()
    url, token = cfg
    try:
        resp = session.get(url, params={"t": token, "a": "list"}, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json().get("rows") or []
    except Exception as e:  # noqa: BLE001 - tracker trouble mustn't stop the alerts
        log.warning("couldn't read the application tracker: %s", e)
        return Marks()
    marks = Marks()
    for row in rows:
        status, job_id, role = str(row.get("status", "")).lower(), str(row.get("id", "")), str(row.get("role", ""))
        if job_id:
            marks.jobs[job_id] = status
        if status == SKIP and role:
            marks.muted.add(role)
    return marks
