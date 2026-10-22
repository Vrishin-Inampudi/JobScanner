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

    def wants(self, title: str) -> bool:
        t = title.lower()
        if self.title_include and not any(w.lower() in t for w in self.title_include):
            return False
        return not any(w.lower() in t for w in self.title_exclude)


@dataclass
class Config:
    lists: list[ListSpec]
    criteria: Criteria
    only_latest_date: bool = False


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
    return Config(lists=lists, criteria=criteria, only_latest_date=bool(raw.get("only_latest_date", False)))
