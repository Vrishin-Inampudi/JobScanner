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
