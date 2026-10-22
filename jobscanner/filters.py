"""Decide which postings qualify and how to rank them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .source import Posting

HOURS_PER_YEAR = 2080

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(k)?", re.I)


def parse_hourly(salary: str) -> float | None:
    """Top of the posted pay range as an hourly rate, or None if unknown."""
    amounts = [float(n.replace(",", "")) * (1000 if k else 1) for n, k in _MONEY.findall(salary or "")]
    if not amounts:
        return None
    top = max(amounts)
    text = salary.lower()
    if "/yr" in text or "year" in text or "annual" in text:
        return round(top / HOURS_PER_YEAR, 2)
    if "/mo" in text or "month" in text:
        return round(top * 12 / HOURS_PER_YEAR, 2)
    if "/wk" in text or "week" in text:
        return round(top / 40, 2)
    return top


def parse_size(size: str | None) -> int:
    """Lower bound of a company-size bucket like '1001-5000' or '10000+'."""
    m = re.match(r"\s*([\d,]+)", size or "")
    return int(m.group(1).replace(",", "")) if m else 0


def location_match(location: str, states: list[str]) -> str | None:
    """Return the first configured state code found in a location string."""
    for code in states:
        code = code.upper()
        name = STATES.get(code, code)
        if re.search(rf"(^|,)\s*{code}\s*(,|$)", location) or re.search(rf"\b{re.escape(name)}\b", location, re.I):
            return code
    return None


def pretty_location(location: str) -> str:
    """'Multi Location\\nLehi, UT, United States\\nSan Jose, CA, United States' -> 'Lehi, UT / San Jose, CA'."""
    parts = [line.strip() for line in location.splitlines() if line.strip() and line.strip().lower() != "multi location"]
    short = [p[: -len(", United States")] if p.endswith(", United States") else p for p in parts]
    return " / ".join(short)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# Words allowed after a priority name, so "NVIDIA Corporation" matches NVIDIA but "Apple Bank" doesn't match Apple.
CORPORATE_WORDS = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "llc", "ltd", "limited", "plc",
    "group", "holdings", "technologies", "platforms", "networks", "web", "services", "usa", "us", "the",
}


def priority_match(company: str, names: list[str]) -> str | None:
    """Return the configured name if the company is that name plus only corporate words."""
    c = _norm(re.sub(r"\(.*?\)", " ", company)).split()
    for name in names:
        n = _norm(name).split()
        if n and c[: len(n)] == n and all(w in CORPORATE_WORDS for w in c[len(n):]):
            return name
    return None


@dataclass
class Criteria:
    min_hourly_pay: float | None = None
    locations: list[str] = field(default_factory=list)
    large_company_min_size: int | None = None
    priority_companies: list[str] = field(default_factory=list)
    match: str = "any"


@dataclass
class Match:
    posting: Posting
    hourly: float | None
    state: str | None
    priority: str | None
    size: int
    reasons: list[str]


def evaluate(p: Posting, c: Criteria) -> Match | None:
    hourly = parse_hourly(p.salary)
    state = location_match(p.location, c.locations) if c.locations else None
    priority = priority_match(p.company, c.priority_companies)
    size = parse_size(p.size)

    checks = []
    if c.min_hourly_pay is not None:
        checks.append(hourly is not None and hourly >= c.min_hourly_pay)
    if c.locations:
        checks.append(state is not None)
    if c.large_company_min_size is not None or c.priority_companies:
        big = c.large_company_min_size is not None and size >= c.large_company_min_size
        checks.append(big or priority is not None)

    ok = any(checks) if c.match == "any" else bool(checks) and all(checks)
    if not ok:
        return None

    reasons = [priority or p.company]
    if hourly is not None:
        reasons.append(f"${hourly:g}/hr")
    reasons.append(pretty_location(p.location) or "location unknown")
    return Match(p, hourly, state, priority, size, reasons)


def rank(matches: list[Match]) -> list[Match]:
    """Priority company, then pay (unknown last), then location, size, newest."""
    def key(m: Match):
        return (
            m.priority is None,
            m.hourly is None,
            -(m.hourly or 0),
            m.state is None,
            -m.size,
            _neg_date(m.posting.date),
        )
    return sorted(matches, key=key)


def _neg_date(date: str) -> tuple:
    digits = re.sub(r"\D", "", date)
    return (-int(digits),) if digits else (0,)
