"""Application tracker: "Applied" / "Not interested" links in each email, kept in a Google Sheet.

A small Apps Script web app (tracker/Code.gs) records each click as a row in your Sheet.
Each run reads the rows back, so jobs you've marked never come back, and "Not interested"
also mutes the same role at the same company in other cities. The same web app keeps every
match in a second tab and serves a private dashboard of them, where each application can be
moved on to Interview, Offer or Rejected. Optional: without the TRACKER_URL and TRACKER_TOKEN
secrets, emails just don't show the links.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
import json
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from .source import TIMEOUT, Posting

log = logging.getLogger("jobscanner")

APPLIED, SKIP = "applied", "not interested"
INTERVIEW, OFFER, REJECTED = "interview", "offer", "rejected"
POSTING_CHARS = 6000   # of each match's text kept in the Sheet, so project ideas can see what a job asked for


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
    rows: list[dict] = field(default_factory=list)       # each marked job: status, company, title, link, marked, posting
    loaded: bool = False                                 # the Sheet was read (so "nothing marked" is real)

    def with_status(self, status: str) -> list[dict]:
        return [r for r in self.rows if r["status"] == status]

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


def load(session, env=os.environ, full: bool = False) -> Marks:
    """Read your marks from the Sheet. Problems are logged, never fatal: alerts matter more.

    ``full`` also asks for each job's saved posting text (only project ideas need it).
    """
    cfg = _config(env)
    if not cfg:
        return Marks()
    url, token = cfg
    try:
        resp = session.get(url, params={"t": token, "a": "list", **({"full": "1"} if full else {})}, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json().get("rows") or []
    except Exception as e:  # noqa: BLE001 - tracker trouble mustn't stop the alerts
        log.warning("couldn't read the application tracker: %s", e)
        return Marks()
    marks = Marks(loaded=True)
    for row in rows:
        status, job_id, role = str(row.get("status", "")).strip().lower(), str(row.get("id", "")), str(row.get("role", ""))
        if job_id:
            marks.jobs[job_id] = status
        if status == SKIP and role:
            marks.muted.add(role)
        marks.rows.append({"status": status, "id": job_id, "company": str(row.get("company", "")),
                           "title": str(row.get("title", "")), "link": str(row.get("link", "")),
                           "marked": _when(row.get("marked")), "posting": str(row.get("posting") or "")})
    return marks


def _when(value) -> datetime | None:
    """The Sheet's 'Marked' time (ISO text); None if an older script didn't send one."""
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def dashboard_url(env=os.environ) -> str | None:
    """Your private dashboard of every match, or None if the tracker isn't set up."""
    cfg = _config(env)
    return f"{cfg[0]}?{urlencode({'t': cfg[1], 'a': 'dashboard'})}" if cfg else None


def push(session, matches, now: datetime, env=os.environ) -> None:
    """Add these matches (and any drafts) to the dashboard. Problems are logged, never fatal."""
    cfg = _config(env)
    if not cfg or not matches:
        return
    url, token = cfg
    rows = [{"id": m.posting.job_keys[0], "role": role_key(m.posting.company, m.posting.title),
             "found": now.isoformat(), "company": m.posting.company, "title": m.posting.title,
             "location": m.posting.location.replace("\n", "; "), "pay": m.hourly, "fit": m.fit, "why": m.fit_why,
             "tags": ", ".join(m.flags), "top": bool(m.priority), "source": m.posting.source,
             "link": m.posting.apply_url, "draft": m.draft,
             "posting": f"{m.posting.qualifications}\n{m.posting.description}".strip()[:POSTING_CHARS]} for m in matches]
    try:
        resp = session.post(url, json={"t": token, "a": "matches", "rows": rows}, timeout=TIMEOUT)
        resp.raise_for_status()
        if not resp.json().get("ok"):
            raise ValueError(str(resp.json().get("error") or "unexpected reply; redeploy tracker/Code.gs"))
    except Exception as e:  # noqa: BLE001 - dashboard trouble mustn't stop the alerts
        log.warning("couldn't update the dashboard: %s", e if isinstance(e, ValueError) else type(e).__name__)


def _post(session, cfg, body: dict) -> dict:
    resp = session.post(cfg[0], json={"t": cfg[1], **body}, timeout=TIMEOUT)
    resp.raise_for_status()
    reply = resp.json()
    if not reply.get("ok"):
        raise ValueError(str(reply.get("error") or "unexpected reply; redeploy tracker/Code.gs"))
    return reply


def guides(session, env=os.environ) -> list[dict] | None:
    """Project guides already sent, as {sent, title}; None if the Sheet can't say (so none is written)."""
    cfg = _config(env)
    if not cfg:
        return None
    try:
        resp = session.get(cfg[0], params={"t": cfg[1], "a": "guides"}, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()["guides"]   # an older script answers without this: redeploy tracker/Code.gs
        return [{"sent": str(r.get("sent", "")), "title": str(r.get("title", ""))} for r in rows]
    except Exception as e:  # noqa: BLE001
        log.warning("couldn't read past project guides (is tracker/Code.gs up to date?): %s", type(e).__name__)
        return None


def push_guide(session, guide_id: str, guide: dict, env=os.environ) -> bool:
    """Save a guide to the Sheet so its private page works; False if it couldn't be saved."""
    cfg = _config(env)
    if not cfg:
        return False
    try:
        _post(session, cfg, {"a": "guide", "id": guide_id, "title": str(guide.get("title", "")),
                             "json": json.dumps(guide, separators=(",", ":"))})
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("couldn't save the project guide to the Sheet: %s", type(e).__name__)
        return False


def guide_url(guide_id: str, env=os.environ) -> str | None:
    cfg = _config(env)
    return f"{cfg[0]}?{urlencode({'t': cfg[1], 'a': 'guide', 'id': guide_id})}" if cfg else None
