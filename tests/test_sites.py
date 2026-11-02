from datetime import datetime, timezone

import pytest

from jobscanner.sites import (
    _workday_location, fetch_greenhouse, fetch_workday, workday_parts, workday_posted,
)
from jobscanner.source import SourceError

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
WD = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
wants = lambda t: "intern" in t.lower() and "security" in t.lower()


def test_workday_parts():
    assert workday_parts(WD) == ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite")
    assert workday_parts("https://x.wd1.myworkdayjobs.com/en-US/Careers/")[2] == "Careers"
    for bad in ("http://nvidia.wd5.myworkdayjobs.com/x", "https://evil.com/x", "https://a.myworkdayjobs.com.evil.com/x"):
        with pytest.raises(SourceError):
            workday_parts(bad)


@pytest.mark.parametrize("text,days", [("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 3 Days Ago", 3),
                                       ("Posted 30+ Days Ago", None), ("", None)])
def test_workday_posted(text, days):
    got = workday_posted(text, NOW)
    assert got is None if days is None else (NOW.date() - got.date()).days == days


def test_workday_location():
    assert _workday_location("US, CA, Santa Clara; US, MA, Westford") == "Santa Clara, CA; Westford, MA"
    assert _workday_location("USA - Sunnyvale, CA") == "Sunnyvale, CA"


class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.ok = data, status, status < 400

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._data


FACETS = [{"facetParameter": "workerSubType", "values": [{"descriptor": "Regular", "id": "r"},
                                                       {"descriptor": "Intern (Fixed Term)", "id": "INT"}]}]


class WorkdaySession:
    def __init__(self, jobs):
        self.jobs, self.bodies = jobs, []

    def post(self, url, json, timeout):
        self.bodies.append(json)
        assert url == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
        return Resp({"facets": FACETS, "jobPostings": self.jobs if json["appliedFacets"] else []})

    def get(self, url, timeout):
        return Resp({"jobPostingInfo": {"location": "US, CA, Santa Clara", "additionalLocations": ["US, MA, Westford"]}})


def job(title, posted="Posted Today", path="/job/US-CA/Security-Intern_JR1"):
    return {"title": title, "externalPath": path, "postedOn": posted, "locationsText": "2 Locations"}


def test_fetch_workday_uses_intern_filter_and_detail_locations():
    s = WorkdaySession([job("Security Intern"), job("Security Intern old", "Posted 30+ Days Ago"),
                        job("Marketing Intern")])
    got = fetch_workday(s, WD, "NVIDIA", "site:nvidia", wants, now=NOW)
    assert s.bodies[1]["appliedFacets"] == {"workerSubType": ["INT"]}
    assert [p.title for p in got] == ["Security Intern"]
    p = got[0]
    assert p.location == "Santa Clara, CA; Westford, MA" and p.row_id == "JR1" and p.company == "NVIDIA"
    assert p.apply_url == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA/Security-Intern_JR1"
    assert p.posted_at is None and p.date == "Nov 2" and p.source == "NVIDIA careers site"


def test_fetch_workday_without_intern_filter_fails():
    class S(WorkdaySession):
        def post(self, url, json, timeout):
            return Resp({"facets": [], "jobPostings": []})
    with pytest.raises(SourceError):
        fetch_workday(S([]), WD, "NVIDIA", "site:nvidia", wants, now=NOW)


def test_fetch_greenhouse():
    jobs = [
        {"id": 1, "title": "Security Engineering Intern", "absolute_url": "https://boards.greenhouse.io/cf/jobs/1",
         "first_published": "2026-11-01T10:00:00-04:00", "location": {"name": "San Francisco, CA"}},
        {"id": 2, "title": "Security Intern (old)", "absolute_url": "https://boards.greenhouse.io/cf/jobs/2",
         "first_published": "2026-09-01T10:00:00-04:00", "location": {"name": "Austin, TX"}},
        {"id": 3, "title": "Sales Intern", "absolute_url": "https://boards.greenhouse.io/cf/jobs/3",
         "first_published": "2026-11-01T10:00:00-04:00", "location": {"name": "Austin, TX"}},
    ]

    class S:
        def get(self, url, timeout):
            assert url == "https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs"
            return Resp({"jobs": jobs})
    got = fetch_greenhouse(S(), "cloudflare", "Cloudflare", "site:cloudflare", wants, now=NOW)
    assert [p.row_id for p in got] == ["1"]
    assert got[0].posted_at == datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc)
    with pytest.raises(SourceError):
        fetch_greenhouse(S(), "../evil", "X", "k", wants, now=NOW)
