"""Remember which postings have already been seen or emailed."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .source import Posting

# Bump when the meaning of stored keys changes; older files are re-baselined silently.
VERSION = 5

# Jobs that vanish from a list are forgotten this long after they were first seen,
# so a job that briefly drops out of a view isn't re-sent when it comes back.
FORGET_AFTER_DAYS = 90


def _today(today: str | None) -> str:
    return today or date.today().isoformat()


def _cutoff(today: str | None) -> str:
    return (date.fromisoformat(_today(today)) - timedelta(days=FORGET_AFTER_DAYS)).isoformat()


class State:
    """``lists`` maps list key -> {job key: first-seen date}; ``emailed`` maps job key -> date sent.

    Jobs are identified by ``Posting.job_keys``; a job is seen if any of its keys is.
    """

    def __init__(self, lists: dict[str, dict[str, str]] | None = None, emailed: dict[str, str] | None = None,
                 last_run_at: datetime | None = None, last_email_at: datetime | None = None,
                 pending: list[Posting] | None = None, last_digest_on: str | None = None,
                 trouble: dict[str, str] | None = None, fits: dict[str, dict] | None = None,
                 sent: list[dict] | None = None, drafts: dict | None = None, last_weekly_on: str | None = None):
        self.lists = lists or {}
        self.emailed = emailed or {}
        self.last_run_at = last_run_at      # last run where every list was checked
        self.last_email_at = last_email_at  # last email of any kind (jobs or "nothing posted")
        self.pending = pending or []        # matches waiting for the daily digest
        self.last_digest_on = last_digest_on  # ET date of the last digest check
        self.trouble = trouble or {}        # company site key -> when it started failing (ISO time)
        self.fits = fits or {}              # job key -> {score, why, draft} for matches waiting in `pending`
        self.sent = sent or []              # recent matches {at, id, company, title, url, fit}, for the weekly summary
        self.drafts = drafts or {}          # {"on": ET date, "count": drafts written that day}
        self.last_weekly_on = last_weekly_on  # ET date of the last weekly summary

    @classmethod
    def load(cls, path: str | Path) -> "State":
        p = Path(path)
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text())
        if raw.get("version") == 4:
            # v5 only remembers jobs that pass a list's title filter. Drop v4's much larger
            # seen lists; what was emailed (the real guard against repeats) carries over.
            raw = {**raw, "lists": {}}
        elif raw.get("version") != VERSION:
            return cls()
        when = lambda k: datetime.fromisoformat(raw[k]) if raw.get(k) else None
        pending = [Posting.from_dict(d) for d in raw.get("pending") or []]
        return cls(raw.get("lists"), raw.get("emailed"), when("last_run_at"), when("last_email_at"),
                   pending, raw.get("last_digest_on"), raw.get("trouble"), raw.get("fits"), raw.get("sent"),
                   raw.get("drafts"), raw.get("last_weekly_on"))

    def diff(self, list_key: str, postings: list[Posting]) -> list[Posting]:
        """Postings not seen before in this list."""
        seen = self.lists.get(list_key, {})
        return [p for p in postings if not any(k in seen for k in p.job_keys)]

    def mark_seen(self, list_key: str, postings: list[Posting], today: str | None = None) -> None:
        """Record the list's current jobs; forget old jobs that are no longer listed."""
        today, cutoff = _today(today), _cutoff(today)
        old = self.lists.get(list_key, {})
        current = {k for p in postings for k in p.job_keys}
        self.lists[list_key] = {
            **{k: d for k, d in old.items() if k not in current and d >= cutoff},
            **{k: old.get(k, today) for k in current},
        }

    def was_emailed(self, posting: Posting) -> bool:
        return any(k in self.emailed for k in posting.job_keys)

    def mark_emailed(self, postings: list[Posting], today: str | None = None) -> None:
        for p in postings:
            for k in p.job_keys:
                self.emailed[k] = _today(today)

    def save(self, path: str | Path, today: str | None = None) -> None:
        cutoff = _cutoff(today)
        emailed = {k: d for k, d in self.emailed.items() if d >= cutoff}
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": VERSION, "lists": self.lists, "emailed": emailed,
                "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
                "last_email_at": self.last_email_at.isoformat() if self.last_email_at else None,
                "pending": [p.to_dict() for p in self.pending], "last_digest_on": self.last_digest_on,
                "trouble": self.trouble, "fits": self.fits, "sent": [s for s in self.sent if s["at"][:10] >= cutoff],
                "drafts": self.drafts, "last_weekly_on": self.last_weekly_on}
        p.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
