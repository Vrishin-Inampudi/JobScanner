"""Remember which postings have already been seen or emailed."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .source import Posting

# Ids that vanish from a list are forgotten this long after they were first seen,
# so a row that briefly drops out of a view isn't re-sent when it comes back.
FORGET_AFTER_DAYS = 90


def _today(today: str | None) -> str:
    return today or date.today().isoformat()


def _cutoff(today: str | None) -> str:
    return (date.fromisoformat(_today(today)) - timedelta(days=FORGET_AFTER_DAYS)).isoformat()


class State:
    """``lists`` maps list key -> {row id: first-seen date}; ``emailed_urls`` maps apply URL -> date sent."""

    def __init__(self, lists: dict[str, dict[str, str]] | None = None, emailed_urls: dict[str, str] | None = None):
        self.lists = lists or {}
        self.emailed_urls = emailed_urls or {}

    @classmethod
    def load(cls, path: str | Path) -> "State":
        p = Path(path)
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text())
        return cls(raw.get("lists"), raw.get("emailed_urls"))

    def diff(self, list_key: str, postings: list[Posting]) -> tuple[list[Posting], bool]:
        """Postings not seen before. A list with no history is baselined (nothing new)."""
        if list_key not in self.lists:
            return [], True
        seen = self.lists[list_key]
        return [p for p in postings if p.row_id not in seen], False

    def mark_seen(self, list_key: str, postings: list[Posting], today: str | None = None) -> None:
        """Record the list's current rows; forget old rows that are no longer listed."""
        today, cutoff = _today(today), _cutoff(today)
        old = self.lists.get(list_key, {})
        current = {p.row_id for p in postings}
        self.lists[list_key] = {
            **{rid: d for rid, d in old.items() if rid not in current and d >= cutoff},
            **{rid: old.get(rid, today) for rid in current},
        }

    def was_emailed(self, url: str) -> bool:
        return bool(url) and url in self.emailed_urls

    def mark_emailed(self, urls: list[str], today: str | None = None) -> None:
        for u in urls:
            if u:
                self.emailed_urls[u] = _today(today)

    def save(self, path: str | Path, today: str | None = None) -> None:
        cutoff = _cutoff(today)
        emailed = {u: d for u, d in self.emailed_urls.items() if d >= cutoff}
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"lists": self.lists, "emailed_urls": emailed}, indent=1, sort_keys=True) + "\n")
