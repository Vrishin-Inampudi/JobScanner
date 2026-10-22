"""Remember which postings have already been seen or emailed."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .source import Posting

# Bump when the meaning of stored keys changes; older files are re-baselined silently.
VERSION = 3

# Jobs that vanish from a list are forgotten this long after they were first seen,
# so a job that briefly drops out of a view isn't re-sent when it comes back.
FORGET_AFTER_DAYS = 90


def _today(today: str | None) -> str:
    return today or date.today().isoformat()


def _cutoff(today: str | None) -> str:
    return (date.fromisoformat(_today(today)) - timedelta(days=FORGET_AFTER_DAYS)).isoformat()


class State:
    """``lists`` maps list key -> {job key: first-seen date}; ``emailed`` maps job key -> date sent.

    Jobs are identified by ``Posting.job_keys``, not Airtable row ids, because intern-list.com
    rebuilds its tables several times a day; a job is seen if any of its keys is.
    """

    def __init__(self, lists: dict[str, dict[str, str]] | None = None, emailed: dict[str, str] | None = None):
        self.lists = lists or {}
        self.emailed = emailed or {}

    @classmethod
    def load(cls, path: str | Path) -> "State":
        p = Path(path)
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text())
        if raw.get("version") != VERSION:
            return cls()
        return cls(raw.get("lists"), raw.get("emailed"))

    def diff(self, list_key: str, postings: list[Posting]) -> tuple[list[Posting], bool]:
        """Postings not seen before. A list with no history is baselined (nothing new)."""
        if list_key not in self.lists:
            return [], True
        seen = self.lists[list_key]
        return [p for p in postings if not any(k in seen for k in p.job_keys)], False

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
        data = {"version": VERSION, "lists": self.lists, "emailed": emailed}
        p.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
