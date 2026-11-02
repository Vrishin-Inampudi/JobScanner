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
# Shorthand some lists use for a whole location ("SF", "NYC").
_SHORT_PLACES = {"sf": ("san francisco", "CA"), "la": ("los angeles", "CA"), "nyc": ("new york", "NY"),
                 "dc": ("washington", "DC")}


def _places(location: str) -> list[tuple[str, str]]:
    """'San Jose, CA, United States; New York, NY' -> [('san jose', 'CA'), ('new york', 'NY')]."""
    out = []
    for part in re.split(r"[;\n]", location):
        bits = [b.strip() for b in part.split(",") if b.strip()]
        if len(bits) == 1 and bits[0].lower() in _SHORT_PLACES:
            out.append(_SHORT_PLACES[bits[0].lower()])
            continue
        if bits and bits[-1].lower() in ("united states", "united states of america", "us", "usa"):
            bits = bits[:-1]
        if len(bits) == 1 and " - " in bits[0]:          # 'California - San Francisco'
            state, _, city = bits[0].partition(" - ")
            if state.strip().lower() in _STATE_CODES:
                bits = [city.strip(), state.strip()]
        if len(bits) >= 2:
            last = bits[-1].upper().replace(".", "")
            st = last if last in STATES else _STATE_CODES.get(bits[-1].lower(), "")
            out.append((bits[0].lower(), st))
    return out


# Countries that mark a location as outside the US (company career sites list jobs worldwide).
FOREIGN = re.compile(r"\b(" + "|".join([
    "canada", "united kingdom", "uk", "england", "scotland", "ireland", "india", "germany", "france", "israel",
    "romania", "poland", "spain", "italy", "netherlands", "belgium", "switzerland", "austria", "sweden",
    "denmark", "norway", "finland", "portugal", "czech republic", "czechia", "hungary", "greece", "türkiye",
    "turkey", "japan", "china", "hong kong", "taiwan", "korea", "singapore", "malaysia", "vietnam",
    "philippines", "indonesia", "thailand", "australia", "new zealand", "mexico", "brazil", "argentina",
    "colombia", "chile", "costa rica", "united arab emirates", "uae", "saudi arabia", "qatar", "egypt",
    "south africa", "luxembourg", "lithuania", "ukraine", "serbia", "bulgaria", "croatia", "estonia",
]) + r")\b", re.I)
_US = re.compile(r"\b(united states|usa|u\.s\.a?\.?|us)\b", re.I)


def outside_us(location: str) -> bool:
    """True only when every listed place names another country: unknown places count as US."""
    parts = [x.strip() for x in re.split(r"[;\n]", location or "") if x.strip()]
    if not parts:
        return False
    for part in parts:
        if _US.search(part) or _places(part) and any(st for _, st in _places(part)) or not FOREIGN.search(part):
            return False
    return True


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
    "industries", "systems", "labs", "laboratory", "technology", "solutions", "international", "federal",
}


def priority_match(company: str, names: list[str]) -> str | None:
    """Return the configured name if the company is that name plus only corporate words."""
    c = _norm(re.sub(r"\(.*?\)", " ", company)).split()
    if c[:1] == ["the"]:
        c = c[1:]
    for name in names:
        n = _norm(name).split()
        if n and c[: len(n)] == n and all(w in CORPORATE_WORDS for w in c[len(n):]):
            return name
    return None


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}


def parse_month(text: str) -> tuple[int, int | None] | None:
    """'2027-December' -> (2027, 12); '2028' -> (2028, None); '2028-05' -> (2028, 5)."""
    m = re.fullmatch(r"\s*(\d{4})(?:\s*-\s*([A-Za-z]+|\d{1,2}))?\s*", text or "")
    if not m:
        return None
    year, month = int(m.group(1)), m.group(2)
    if month is None:
        return year, None
    num = int(month) if month.isdigit() else _MONTHS.get(month.lower())
    return (year, num) if num and 1 <= num <= 12 else None


def grad_window(text: str) -> tuple[int, int] | None:
    """'2027-December / 2028-June' -> earliest and latest month (as year*12+month) the role accepts."""
    months = []
    for part in (text or "").split("/"):
        ym = parse_month(part)
        if ym:
            y, m = ym
            months += [y * 12 + 1, y * 12 + 12] if m is None else [y * 12 + m]
    return (min(months), max(months)) if months else None


def grad_fits(text: str, graduation: tuple[int, int] | None, slack_months: int = 1) -> bool:
    """Unknown windows count as a fit; otherwise your graduation must be inside it (give or take a month)."""
    window = grad_window(text)
    if not graduation or not window:
        return True
    you = graduation[0] * 12 + graduation[1]
    return window[0] - slack_months <= you <= window[1] + slack_months


def pretty_month(text: str) -> str:
    """'2027-December / 2028-June' -> 'Dec 2027 / Jun 2028'."""
    out = []
    for part in (text or "").split("/"):
        ym = parse_month(part)
        if ym:
            y, m = ym
            out.append(f"{list(_MONTHS)[m - 1][:3].title()} {y}" if m else str(y))
    return " / ".join(out)


_PHD = re.compile(r"\bph\.?\s?d\b|doctoral", re.I)
_NOT_PHD = re.compile(r"bachelor|undergrad|\bb\.?s\.?\b|\bb\.?a\.?\b|master|\bm\.?s\.?\b", re.I)
_CLEARANCE = re.compile(r"(active|current|existing|interim)\s+(\w+\s+){0,3}clearance|"
                        r"clearance\s+(is\s+)?required|must\s+(possess|hold|have)\s+(an?\s+)?(active\s+)?"
                        r"(\w+\s+){0,2}clearance|ts/sci|polygraph", re.I)
_OBTAIN_CLEARANCE = re.compile(r"(obtain|eligib\w*\s+(for|to)|able\s+to\s+(get|obtain))\s+(\w+\s+){0,3}clearance", re.I)
_CITIZEN = re.compile(r"\bu\.?\s?s\.?\s+citizen|united states citizen", re.I)


def phd_only(p: Posting) -> bool:
    """A PhD role: says PhD in the title, or asks only for a PhD in its requirements."""
    if _PHD.search(p.title) and not _NOT_PHD.search(p.title):
        return True
    q = p.qualifications
    return bool(q and _PHD.search(q) and not _NOT_PHD.search(q))


def requirement_flags(p: Posting) -> list[str]:
    """Hard requirements worth knowing before applying."""
    text = f"{p.title}\n{p.qualifications}"
    flags = []
    if _CLEARANCE.search(text):
        flags.append("Needs clearance")
    elif _OBTAIN_CLEARANCE.search(text):
        flags.append("Clearance-eligible")
    if _CITIZEN.search(text):
        flags.append("US citizens")
    return flags


@dataclass
class Criteria:
    min_hourly_pay: float | None = None
    locations: list[str] = field(default_factory=list)
    large_company_min_size: int | None = None
    priority_companies: list[str] = field(default_factory=list)
    match: str = "any"
    # Eligibility: hard filters applied before the criteria above.
    graduation: tuple[int, int] | None = None  # (year, month) you graduate
    skip_phd_only: bool = False
    # Company matches (priority or large) must also be in `locations`, remote, or unspecific.
    company_needs_location: bool = False


@dataclass
class Match:
    posting: Posting
    hourly: float | None
    area: str | None
    priority: str | None
    size: int
    reasons: list[str]
    flags: list[str] = field(default_factory=list)


def anywhere(p: Posting) -> bool:
    """Remote, or no specific city given ('United States', 'Hybrid', '')."""
    return (p.work_model or "").lower() == "remote" or "remote" in p.location.lower() or not _places(p.location)


def eligible(p: Posting, c: Criteria) -> bool:
    if outside_us(p.location):
        return False
    if c.skip_phd_only and phd_only(p):
        return False
    return grad_fits(p.grad_time, c.graduation)


def evaluate(p: Posting, c: Criteria) -> Match | None:
    if not eligible(p, c):
        return None
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
        placed = not c.company_needs_location or area is not None or anywhere(p)
        checks.append((big or priority is not None) and placed)

    ok = any(checks) if c.match == "any" else bool(checks) and all(checks)
    if not ok:
        return None

    reasons = [priority or p.company]
    if hourly is not None:
        reasons.append(f"${hourly:g}/hr")
    reasons.append(pretty_location(p.location) or "location unknown")
    return Match(p, hourly, area, priority, size, reasons, requirement_flags(p))


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
