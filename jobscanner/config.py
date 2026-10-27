"""Load config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .filters import Criteria


@dataclass
class ListSpec:
    key: str
    title_include: list[str] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)
    repo: str | None = None  # a GitHub internship repo (owner/name) instead of an intern-list.com list

    def wants(self, title: str) -> bool:
        t = title.lower()
        if self.title_include and not any(w.lower() in t for w in self.title_include):
            return False
        return not any(w.lower() in t for w in self.title_exclude)


@dataclass
class Config:
    lists: list[ListSpec]
    criteria: Criteria
    max_age_hours: float = 24
    empty_email_every_hours: float | None = None
    quiet_hours: tuple[int, int] | None = None  # [start, end) hours, Eastern time
    check_every_minutes: int = 60                # how often the scheduler runs it; used in the email copy


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    lists = [ListSpec(**item) if isinstance(item, dict) else ListSpec(key=str(item)) for item in raw.get("lists") or []]
    if not lists:
        raise ValueError(f"{path}: 'lists' must name at least one intern-list.com list")
    match = raw.get("match", "any")
    if match not in ("any", "all"):
        raise ValueError(f"{path}: 'match' must be 'any' or 'all', got {match!r}")
    crit = raw.get("criteria") or {}
    criteria = Criteria(
        min_hourly_pay=crit.get("min_hourly_pay"),
        locations=[str(s) for s in crit.get("locations") or []],
        large_company_min_size=crit.get("large_company_min_size"),
        priority_companies=[str(s) for s in crit.get("priority_companies") or []],
        match=match,
    )
    max_age = raw.get("max_age_hours", 24)
    if not isinstance(max_age, (int, float)) or max_age <= 0:
        raise ValueError(f"{path}: 'max_age_hours' must be a positive number, got {max_age!r}")
    every = raw.get("empty_email_every_hours")
    if every is not None and (not isinstance(every, (int, float)) or every <= 0):
        raise ValueError(f"{path}: 'empty_email_every_hours' must be a positive number, got {every!r}")
    quiet = raw.get("quiet_hours")
    if quiet is not None:
        if not (isinstance(quiet, list) and len(quiet) == 2 and all(isinstance(h, int) and 0 <= h <= 24 for h in quiet)):
            raise ValueError(f"{path}: 'quiet_hours' must be [start, end] hours 0-24, got {quiet!r}")
        quiet = (quiet[0], quiet[1])
    check_every = raw.get("check_every_minutes", 60)
    if not isinstance(check_every, int) or check_every <= 0:
        raise ValueError(f"{path}: 'check_every_minutes' must be a positive whole number, got {check_every!r}")
    return Config(lists=lists, criteria=criteria, max_age_hours=float(max_age),
                  empty_email_every_hours=float(every) if every else None, quiet_hours=quiet,
                  check_every_minutes=check_every)
