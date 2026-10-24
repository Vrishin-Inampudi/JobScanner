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


# Metro areas usable in `locations`, as (state, cities). Postings name the actual city,
# so each metro lists the places its jobs are usually filed under.
METROS = {
    "Bay Area": ("CA", [
        "San Francisco", "South San Francisco", "San Jose", "Palo Alto", "Mountain View", "Sunnyvale",
        "Santa Clara", "Menlo Park", "Redwood City", "Cupertino", "Milpitas", "Fremont", "Oakland",
        "Berkeley", "Emeryville", "San Mateo", "Foster City", "Burlingame", "San Carlos", "San Bruno",
        "Los Gatos", "Campbell", "Pleasanton", "Livermore", "San Ramon", "Hayward", "Newark",
        "Brisbane", "Walnut Creek", "Dublin", "Belmont", "Los Altos",
    ]),
    "Los Angeles": ("CA", [
        "Los Angeles", "Santa Monica", "Culver City", "Playa Vista", "Marina del Rey", "El Segundo",
        "Hawthorne", "Torrance", "Redondo Beach", "Manhattan Beach", "Gardena", "Long Beach",
        "Pasadena", "Burbank", "Glendale", "Irvine", "Costa Mesa", "Newport Beach", "Anaheim",
        "Santa Ana", "Carson", "Inglewood", "Venice",
    ]),
    "San Diego": ("CA", ["San Diego", "La Jolla", "Carlsbad", "Del Mar", "Chula Vista", "Poway", "Oceanside"]),
    "Seattle": ("WA", ["Seattle", "Bellevue", "Redmond", "Kirkland", "Bothell", "Tukwila", "Renton", "Everett"]),
    "NYC": ("NY", ["New York", "New York City", "Brooklyn", "Manhattan", "Queens", "Long Island City", "Bronx"]),
    "Boston": ("MA", [
        "Boston", "Cambridge", "Somerville", "Waltham", "Lexington", "Burlington", "Bedford", "Woburn",
        "Newton", "Needham", "Watertown", "Quincy", "Marlborough", "Framingham", "Andover",
    ]),
    "Chicago": ("IL", ["Chicago", "Evanston", "Schaumburg", "Oak Brook", "Naperville", "Rosemont", "Deerfield", "Northbrook"]),
    "Austin": ("TX", ["Austin", "Round Rock", "Cedar Park", "Georgetown", "Pflugerville"]),
    "DC": ("DC", ["Washington"]),
}
# DC jobs are mostly filed under Northern Virginia and Maryland suburbs.
DC_SUBURBS = {
    "VA": ["Arlington", "Alexandria", "McLean", "Tysons", "Tysons Corner", "Vienna", "Fairfax", "Reston",
           "Herndon", "Chantilly", "Falls Church", "Springfield", "Sterling", "Dulles", "Ashburn", "Annandale"],
    "MD": ["Bethesda", "Rockville", "Silver Spring", "College Park", "Gaithersburg", "Chevy Chase", "Greenbelt", "Hyattsville",
           "Laurel", "Columbia", "Fort Meade", "Annapolis Junction", "Hanover"],
}
METRO_ALIASES = {
    "sf bay area": "Bay Area", "san francisco bay area": "Bay Area", "los angeles area": "Los Angeles",
    "new york city": "NYC", "washington dc": "DC", "washington, dc": "DC", "d.c.": "DC",
}
_STATE_CODES = {name.lower(): code for code, name in STATES.items()}


def _places(location: str) -> list[tuple[str, str]]:
    """'San Jose, CA, United States; New York, NY' -> [('san jose', 'CA'), ('new york', 'NY')]."""
    out = []
    for part in re.split(r"[;\n]", location):
        bits = [b.strip() for b in part.split(",") if b.strip()]
        if bits and bits[-1].lower() in ("united states", "us", "usa"):
            bits = bits[:-1]
        if len(bits) >= 2:
            st = bits[-1].upper() if bits[-1].upper() in STATES else _STATE_CODES.get(bits[-1].lower(), "")
            out.append((bits[0].lower(), st))
    return out


def _metro_cities(name: str) -> set[tuple[str, str]]:
    state, cities = METROS[name]
    pairs = {(c.lower(), state) for c in cities}
    if name == "DC":
        pairs |= {(c.lower(), st) for st, cs in DC_SUBURBS.items() for c in cs}
    return pairs


def location_match(location: str, wanted: list[str]) -> str | None:
    """Return the first configured location the posting is in.

    Entries can be a state code ("CT"), a metro from METROS ("Bay Area"), or a city ("Denver, CO").
    """
    places = _places(location)
    for entry in wanted:
        key = entry.strip()
        metro = METRO_ALIASES.get(key.lower()) or next((m for m in METROS if m.lower() == key.lower()), None)
        if metro:
            cities = _metro_cities(metro)
            if any(p in cities for p in places):
                return metro
        elif key.upper() in STATES:
            code = key.upper()
            if any(st == code for _, st in places) or re.search(rf"\b{re.escape(STATES[code])}\b", location, re.I):
                return code
        else:
            city, _, st = (x.strip() for x in key.partition(","))
            if any(c == city.lower() and (not st or s == st.upper()) for c, s in places):
                return key
    return None


def pretty_location(location: str) -> str:
    """'Multi Location\\nLehi, UT, United States\\nSan Jose, CA, United States' -> 'Lehi, UT / San Jose, CA'."""
    parts = [x.strip() for x in re.split(r"[;\n]", location) if x.strip() and x.strip().lower() != "multi location"]
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
    area: str | None
    priority: str | None
    size: int
    reasons: list[str]


def evaluate(p: Posting, c: Criteria) -> Match | None:
    hourly = parse_hourly(p.salary)
    area = location_match(p.location, c.locations) if c.locations else None
    priority = priority_match(p.company, c.priority_companies)
    size = parse_size(p.size)

    checks = []
    if c.min_hourly_pay is not None:
        checks.append(hourly is not None and hourly >= c.min_hourly_pay)
    if c.locations:
        checks.append(area is not None)
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
    return Match(p, hourly, area, priority, size, reasons)


def rank(matches: list[Match]) -> list[Match]:
    """Priority company, then pay (unknown last), then location, size, newest."""
    def key(m: Match):
        return (
            m.priority is None,
            m.hourly is None,
            -(m.hourly or 0),
            m.area is None,
            -m.size,
            -m.posting.posted_at.timestamp() if m.posting.posted_at else 0,
            _neg_date(m.posting.date),
        )
    return sorted(matches, key=key)


def _neg_date(date: str) -> tuple:
    digits = re.sub(r"\D", "", date)
    return (-int(digits),) if digits else (0,)
