"""Load config.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ai import AIConfig
from .filters import Criteria, parse_month
from .skills import Skills, skill

BUILTIN_CAREERS = ("amazon", "google", "apple")  # sites.BUILTIN
SITE_KINDS = ("workday", "greenhouse", "lever", "ashby", "oracle", "smartrecruiters", "eightfold", "careers")


@dataclass
class ListSpec:
    key: str
    title_include: list[str] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)
    repo: str | None = None  # a GitHub internship repo (owner/name) instead of an intern-list.com list
    title_require: list[str] = field(default_factory=list)  # also need one of these words ("intern" also matches internship)
    company: str | None = None    # company career sites: the company's name
    workday: str | None = None    # ... its Workday careers URL
    greenhouse: str | None = None  # ... or its Greenhouse board name
    lever: str | None = None      # ... or its Lever board name
    ashby: str | None = None      # ... or its Ashby board name
    oracle: str | None = None     # ... or its Oracle Recruiting careers URL
    smartrecruiters: str | None = None  # ... or its SmartRecruiters company name
    eightfold: str | None = None  # ... or its Eightfold careers URL (https://<site>/careers?domain=<company.com>)
    careers: str | None = None    # ... or a built-in site: amazon, google, apple

    @property
    def is_site(self) -> bool:
        return any(getattr(self, kind) for kind in SITE_KINDS)

    def wants(self, title: str) -> bool:
        t = title.lower()
        if self.title_require and not any(re.search(rf"\b{re.escape(w.lower())}(s|ships?)?\b", t) for w in self.title_require):
            return False
        if self.title_include and not any(_starts_word(w, t) for w in self.title_include):
            return False
        return not any(_starts_word(w, t) for w in self.title_exclude)


def _starts_word(word: str, title: str) -> bool:
    """The title has a word starting with ``word``: 'cyber' is in 'Cybersecurity Intern', 'noc' isn't in 'Innocent'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(word.lower())}", title) is not None


@dataclass
class Config:
    lists: list[ListSpec]
    criteria: Criteria
    max_age_hours: float = 24
    empty_email_every_hours: float | None = None
    quiet_hours: tuple[int, int] | None = None  # [start, end) hours, Eastern time
    check_every_minutes: int = 60                # how often the scheduler runs it; used in the email copy
    digest_hour: int | None = None               # ET hour for the daily digest of non-urgent matches
    ai: AIConfig = field(default_factory=AIConfig)  # resume fit scores and drafts (only with the AI secrets set)
    weekly_summary: tuple[int, int] | None = None  # (weekday 0=Mon, ET hour) for the weekly summary email
    follow_up_after_days: int = 10               # applications this old with no news are listed in the summary


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    lists = [ListSpec(**item) if isinstance(item, dict) else ListSpec(key=str(item)) for item in raw.get("lists") or []]
    if not lists:
        raise ValueError(f"{path}: 'lists' must name at least one intern-list.com list")
    match = raw.get("match", "any")
    if match not in ("any", "all"):
        raise ValueError(f"{path}: 'match' must be 'any' or 'all', got {match!r}")
    site_specs = _company_sites(path, raw.get("company_sites") or {})
    lists += site_specs
    elig = raw.get("eligibility") or {}
    graduation = None
    if elig.get("graduation"):
        ym = parse_month(str(elig["graduation"]))
        if not ym or not ym[1]:
            raise ValueError(f"{path}: 'eligibility.graduation' must look like 2028-05, got {elig['graduation']!r}")
        graduation = (ym[0], ym[1])
    crit = raw.get("criteria") or {}
    criteria = Criteria(
        min_hourly_pay=crit.get("min_hourly_pay"),
        locations=[str(s) for s in crit.get("locations") or []],
        large_company_min_size=crit.get("large_company_min_size"),
        # Every company whose career site you watch is a top company too.
        priority_companies=list(dict.fromkeys([str(s) for s in crit.get("priority_companies") or []]
                                              + [s.company for s in site_specs])),
        match=match,
        graduation=graduation,
        skip_phd_only=bool(elig.get("skip_phd_only", False)),
        skip_grad_level=bool(elig.get("skip_grad_level", False)),
        company_needs_location=bool(crit.get("company_needs_location", False)),
        skills=_skills(path, raw.get("skills") or {}),
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
    digest = raw.get("digest_hour")
    if digest is not None and not (isinstance(digest, int) and 0 <= digest <= 23):
        raise ValueError(f"{path}: 'digest_hour' must be an hour 0-23, got {digest!r}")
    follow_up = raw.get("follow_up_after_days", 10)
    if not isinstance(follow_up, int) or follow_up <= 0:
        raise ValueError(f"{path}: 'follow_up_after_days' must be a positive whole number, got {follow_up!r}")
    return Config(lists=lists, criteria=criteria, max_age_hours=float(max_age),
                  empty_email_every_hours=float(every) if every else None, quiet_hours=quiet,
                  check_every_minutes=check_every, digest_hour=digest, ai=_ai(path, raw.get("ai") or {}),
                  weekly_summary=_weekly(path, raw.get("weekly_summary")), follow_up_after_days=follow_up)


DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _weekly(path, section) -> tuple[int, int] | None:
    """``weekly_summary: {day: sunday, hour: 18}`` -> (6, 18)."""
    if section is None:
        return None
    day, hour = str((section or {}).get("day", "")).lower(), (section or {}).get("hour", 18)
    if not isinstance(section, dict) or day not in DAYS or not (isinstance(hour, int) and 0 <= hour <= 23):
        raise ValueError(f"{path}: 'weekly_summary' must look like {{day: sunday, hour: 18}}, got {section!r}")
    return DAYS.index(day), hour


def _ai(path, section: dict) -> AIConfig:
    known = AIConfig.__dataclass_fields__
    extra = sorted(set(section) - set(known))
    if extra:
        raise ValueError(f"{path}: ai: unknown setting {extra[0]!r} (known: {', '.join(known)})")
    for name, value in section.items():
        wanted = str if name.endswith("_model") else int
        if not isinstance(value, wanted) or isinstance(value, bool) or (wanted is int and value < 0):
            raise ValueError(f"{path}: ai.{name} must be a {'model name' if wanted is str else 'whole number'}, got {value!r}")
    return AIConfig(**section)


def _skills(path, section: dict) -> Skills:
    """``skills: {have: [...], lack: [...]}``; each entry is a name or [name, other spellings...]."""
    try:
        return Skills(have=[skill(e) for e in section.get("have") or []],
                      lack=[skill(e) for e in section.get("lack") or []])
    except ValueError as e:
        raise ValueError(f"{path}: skills: {e}") from None


def _company_sites(path, section: dict) -> list[ListSpec]:
    """Turn ``company_sites`` into lists; the title filters apply to every site."""
    shared = dict(title_require=section.get("title_require") or [], title_include=section.get("title_include") or [],
                  title_exclude=section.get("title_exclude") or [])
    specs, keys = [], set()
    for site in section.get("sites") or []:
        company = str(site.get("company") or "").strip()
        kinds = [k for k in SITE_KINDS if site.get(k)]
        if not company or len(kinds) != 1:
            raise ValueError(f"{path}: each company site needs a company and exactly one of "
                             f"{'/'.join(SITE_KINDS)}: {site!r}")
        if site.get("careers") and site["careers"] not in BUILTIN_CAREERS:
            raise ValueError(f"{path}: careers must be one of {', '.join(BUILTIN_CAREERS)}, got {site['careers']!r}")
        key = "site:" + re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
        if key in keys:
            raise ValueError(f"{path}: company site {company!r} is listed twice")
        keys.add(key)
        specs.append(ListSpec(key=key, company=company, **{k: site.get(k) for k in SITE_KINDS}, **shared))
    return specs
