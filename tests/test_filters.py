import pytest

from jobscanner.filters import (
    Criteria, evaluate, location_match, parse_hourly, parse_size, priority_match, rank,
)
from jobscanner.source import Posting


def posting(**kw):
    base = dict(
        list_key="cs", row_id="rec1", title="Security Intern", company="Acme",
        location="Austin, TX, United States", salary="N/A", size="11-50",
        date="2026-10-21", work_model="On Site", apply_url="https://x/1",
    )
    base.update(kw)
    return Posting(**base)


CRIT = Criteria(
    min_hourly_pay=35, locations=["CA", "MA", "NY", "CT"], large_company_min_size=10000,
    priority_companies=["Palo Alto Networks", "CrowdStrike", "NVIDIA", "Meta", "Apple", "Amazon"],
)


@pytest.mark.parametrize("salary,expected", [
    ("$45-$60 /hr", 60),
    ("$22.60-$47.95 /hr", 47.95),
    ("$40 /hr", 40),
    ("$52900-$108000 /yr", round(108000 / 2080, 2)),
    ("N/A", None),
    ("Unpaid", None),
    ("", None),
])
def test_parse_hourly(salary, expected):
    assert parse_hourly(salary) == expected


def test_parse_size():
    assert parse_size("10000+") == 10000
    assert parse_size("1001-5000") == 1001
    assert parse_size(None) == 0


@pytest.mark.parametrize("loc,expected", [
    ("San Jose, CA, United States", "CA"),
    ("New York, NY", "NY"),
    ("Hartford, CT, United States", "CT"),
    ("Boston, Massachusetts", "MA"),
    ("United States", None),
    ("Remote, United States", None),
    ("Canton, OH, United States", None),
    ("Menlo Park, CA; New York, NY", "CA"),
    ("Austin, TX; Hartford, CT", "CT"),
])
def test_location_match(loc, expected):
    assert location_match(loc, ["CA", "MA", "NY", "CT"]) == expected


METRO_PREFS = ["Bay Area", "Los Angeles", "San Diego", "Seattle", "NYC", "Boston", "Chicago", "Austin", "DC", "CT"]


@pytest.mark.parametrize("loc,expected", [
    ("San Jose, CA", "Bay Area"),
    ("Sunnyvale, CA, United States", "Bay Area"),
    ("El Segundo, CA", "Los Angeles"),
    ("Irvine, CA", "Los Angeles"),
    ("San Diego, CA", "San Diego"),
    ("Redmond, WA", "Seattle"),
    ("New York, NY", "NYC"),
    ("Southeast, New York, United States", None),
    ("Cambridge, MA", "Boston"),
    ("Cambridge, UK", None),
    ("Chicago, IL", "Chicago"),
    ("North Chicago, IL", None),
    ("Austin, TX", "Austin"),
    ("Washington, DC", "DC"),
    ("McLean, VA", "DC"),
    ("Bethesda, MD", "DC"),
    ("Springfield, IL", None),
    ("Hartford, CT, United States", "CT"),
    ("Sacramento, CA", None),
    ("Dallas, TX", None),
    ("Remote, United States", None),
    ("Multi Location\nDallas, TX, United States\nSeattle, WA, United States", "Seattle"),
    ("Atlanta, GA; Palo Alto, CA", "Bay Area"),
    ("SF; Pittsburgh, PA", "Bay Area"),
    ("NYC", "NYC"),
    ("LA", "Los Angeles"),
    ("Remote in USA", None),
])
def test_metro_match(loc, expected):
    assert location_match(loc, METRO_PREFS) == expected


def test_city_entries():
    assert location_match("Denver, CO, United States", ["Denver, CO"]) == "Denver, CO"
    assert location_match("Denver, CO", ["Denver"]) == "Denver"
    assert location_match("Denver, PA", ["Denver, CO"]) is None


@pytest.mark.parametrize("company,expected", [
    ("NVIDIA Corporation", "NVIDIA"),
    ("Palo Alto Networks, Inc.", "Palo Alto Networks"),
    ("crowdstrike", "CrowdStrike"),
    ("Meta", "Meta"),
    ("Metaview", None),
    ("Apple Bank", None),
    ("Amazon Web Services (AWS)", "Amazon"),
    ("Federal Reserve Bank of San Francisco", None),
])
def test_priority_match(company, expected):
    assert priority_match(company, CRIT.priority_companies) == expected


def test_evaluate_any():
    assert evaluate(posting(), CRIT) is None
    assert evaluate(posting(salary="$36 /hr"), CRIT) is not None
    assert evaluate(posting(location="Palo Alto, CA, United States"), CRIT) is not None
    assert evaluate(posting(size="10000+"), CRIT) is not None
    m = evaluate(posting(company="NVIDIA", salary="$52 /hr", location="Santa Clara, CA, United States"), CRIT)
    assert m.reasons == ["NVIDIA", "$52/hr", "Santa Clara, CA"]


def test_evaluate_all_unknown_pay_fails():
    c = Criteria(**{**CRIT.__dict__, "match": "all"})
    full = dict(company="NVIDIA", location="Santa Clara, CA, United States")
    assert evaluate(posting(salary="N/A", **full), c) is None
    assert evaluate(posting(salary="$50 /hr", **full), c) is not None


def test_rank_order():
    ms = [evaluate(p, CRIT) for p in [
        posting(row_id="pay_unknown_ca", location="Irvine, CA"),
        posting(row_id="pay40", salary="$40 /hr"),
        posting(row_id="pay60", salary="$60 /hr"),
        posting(row_id="nvidia", company="NVIDIA"),
        posting(row_id="pay60_ny_big", salary="$60 /hr", location="New York, NY", size="10000+"),
    ]]
    assert [m.posting.row_id for m in rank(ms)] == [
        "nvidia", "pay60_ny_big", "pay60", "pay40", "pay_unknown_ca",
    ]


from jobscanner.filters import eligible, grad_fits, grad_window, phd_only, pretty_month, requirement_flags


def test_grad_window_and_fit():
    assert grad_window("2027-December / 2028-June") == (2027 * 12 + 12, 2028 * 12 + 6)
    assert grad_window("2028") == (2028 * 12 + 1, 2028 * 12 + 12)
    assert grad_window("") is None
    assert grad_fits("2027-December / 2028-June", (2028, 5))
    assert grad_fits("2027-December / 2028-June", (2028, 7))          # a month of slack
    assert not grad_fits("2027-December / 2028-June", (2029, 5))
    assert grad_fits("", (2029, 5)) and grad_fits("2026", None)       # unknown either side: keep it
    assert pretty_month("2027-December / 2028-June") == "Dec 2027 / Jun 2028"
    assert pretty_month("2027-May") == "May 2027"


@pytest.mark.parametrize("title,quals,expected", [
    ("PhD Research Intern, Security and Privacy - 2027", "", True),
    ("Security Intern (BS/MS/PhD)", "", False),
    ("Security Intern", "1. Currently pursuing a PhD in Computer Science", True),
    ("Security Intern", "1. Pursuing a Bachelor's, Master's or PhD", False),
    ("Security Intern", "", False),
])
def test_phd_only(title, quals, expected):
    assert phd_only(posting(title=title, qualifications=quals)) is expected


def test_requirement_flags():
    assert requirement_flags(posting(qualifications="Must possess an active Secret clearance. U.S. citizenship required.")) \
        == ["Needs clearance", "US citizens"]
    assert requirement_flags(posting(qualifications="Ability to obtain a security clearance")) == ["Clearance-eligible"]
    assert requirement_flags(posting(title="Cyber Intern (TS/SCI)")) == ["Needs clearance"]
    assert requirement_flags(posting()) == []


def test_eligibility_filters_before_criteria():
    crit = Criteria(**{**CRIT.__dict__, "graduation": (2029, 5), "skip_phd_only": True})
    nvidia = dict(company="NVIDIA")
    assert evaluate(posting(**nvidia), crit) is not None
    assert evaluate(posting(**nvidia, grad_time="2027-December / 2028-June"), crit) is None
    assert evaluate(posting(**nvidia, title="PhD Security Intern"), crit) is None
    m = evaluate(posting(**nvidia, qualifications="US citizen required"), crit)
    assert m.flags == ["US citizens"]
    assert eligible(posting(title="PhD Intern"), CRIT)                 # off by default


def test_priority_names_with_the_and_suffixes():
    names = ["Boeing", "Anduril", "Cisco", "Apple"]
    assert priority_match("The Boeing Company", names) == "Boeing"
    assert priority_match("Anduril Industries", names) == "Anduril"
    assert priority_match("Cisco Systems, Inc.", names) == "Cisco"
    assert priority_match("Apple Federal Credit Union", names) is None


from jobscanner.filters import outside_us


@pytest.mark.parametrize("loc,expected", [
    ("Bucharest, Romania", True),
    ("Winnersh, United Kingdom", True),
    ("Toronto, ON, Canada", True),
    ("Office - Israel - Petach Tikva", True),
    ("United States; Canada", False),
    ("Remote in USA; Remote in Canada", False),
    ("Westford, Massachusetts, United States of America", False),
    ("San Francisco", False),
    ("Hybrid", False),
    ("", False),
    ("Bangalore, India; Austin, TX", False),
    # Only American cities: a city with no US state or country counts as abroad.
    ("London", True),
    ("Toronto, ON", True),
    ("Bengaluru, Karnataka", True),
    ("Remote - Canada", True),
    ("Paris; Berlin", True),
    ("Remote", False),
    ("Multiple Locations", False),
    ("Paris, TX", False),
    ("Seattle", False),
    ("NYC", False),
    ("Texas", False),
    ("London; Arlington", False),
    ("Hybrid - Austin", False),
    ("Remote (Seattle)", False),
    ("Greater Birmingham, Alabama Area", False),
    ("Greater Indianapolis", False),
    ("MA-TEWKSBURY-TB1 ~ 50 Apple Hill Dr ~ ASSABET BLDG", False),
    ("San Juan, PR", False),
    ("Bucharest", True),
])
def test_outside_us(loc, expected):
    assert outside_us(loc) is expected


@pytest.mark.parametrize("loc,expected", [
    ("California - San Francisco; Washington - Seattle", "Bay Area"),
    ("Westford, Massachusetts, United States of America", None),
    ("Cambridge, Massachusetts, United States of America", "Boston"),
    ("Washington, D.C.", "DC"),
])
def test_more_location_formats(loc, expected):
    assert location_match(loc, METRO_PREFS) == expected


def test_skills_are_highlighted_not_filtered():
    from dataclasses import replace
    from jobscanner.emailer import render
    from jobscanner.skills import Skills, skill
    crit = replace(CRIT, skills=Skills(have=[skill("C++"), skill("Linux")], lack=[skill("Java")]))
    m = evaluate(posting(company="NVIDIA", qualifications="C/C++ on Linux", description="Java a plus"), crit)
    assert (m.have, m.lack) == (["C++", "Linux"], ["Java"])
    subject, text, html = render([m])
    assert "Skills: ✓ C++, Linux · ✗ Java" in text
    assert "✓ C++" in html and "✗ Java" in html
    assert evaluate(posting(company="NVIDIA", description="Java only"), crit) is not None  # still sent
