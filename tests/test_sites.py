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


def test_fetch_workday_without_intern_filter_searches():
    class S(WorkdaySession):
        def post(self, url, json, timeout):
            self.bodies.append(json)
            return Resp({"facets": [], "jobPostings": self.jobs if json["searchText"] else []})
    s = S([job("Security Intern")])
    got = fetch_workday(s, WD, "NVIDIA", "site:nvidia", wants, now=NOW)
    assert [p.title for p in got] == ["Security Intern"]
    assert s.bodies[1] == {"limit": 20, "offset": 0, "searchText": "intern", "appliedFacets": {}}


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


from jobscanner.sites import fetch_amazon, fetch_apple, fetch_google, fetch_oracle, oracle_parts

ORACLE = "https://edel.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001"


def test_oracle_parts():
    assert oracle_parts(ORACLE) == ("edel.fa.us2.oraclecloud.com", "CX_2001")
    with pytest.raises(SourceError):
        oracle_parts("https://evil.com/hcmUI/CandidateExperience/en/sites/CX_1")


class GetSession:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.pages.pop(0)


class Text(Resp):
    def __init__(self, text):
        super().__init__(None)
        self.text = text


def test_fetch_oracle_stops_at_old_jobs():
    page = {"items": [{"requisitionList": [
        {"Id": "1", "Title": "Network Security Intern", "PostedDate": "2026-11-01", "PrimaryLocation": "Sunnyvale, California, United States"},
        {"Id": "2", "Title": "Sales Manager", "PostedDate": "2026-11-01"},
        {"Id": "3", "Title": "Security Intern old", "PostedDate": "2026-10-01"},
    ]}]}
    s = GetSession([Resp(page)])
    got = fetch_oracle(s, ORACLE, "Fortinet", "site:fortinet", wants, now=NOW)
    assert [p.row_id for p in got] == ["1"] and len(s.calls) == 1
    assert got[0].apply_url == ORACLE + "/job/1" and got[0].date == "Nov 1"
    assert "sortBy=POSTING_DATES_DESC" in s.calls[0][1]["finder"]


def test_fetch_amazon():
    jobs = [{"title": "Security Engineer Intern", "posted_date": "October 30, 2026", "job_path": "/en/jobs/1/sec",
             "id_icims": "1", "city": "Seattle", "state": "WA"},
            {"title": "Security Intern old", "posted_date": "September 1, 2026", "job_path": "/en/jobs/2/x", "id_icims": "2"}]
    got = fetch_amazon(GetSession([Resp({"jobs": jobs})]), "Amazon", "site:amazon", wants, now=NOW)
    assert [(p.row_id, p.location, p.apply_url) for p in got] == [("1", "Seattle, WA", "https://www.amazon.jobs/en/jobs/1/sec")]


def test_fetch_google():
    stamp = int(NOW.timestamp()) - 3600
    data = [[["123", "Security Engineering Intern, BS/MS", "x", None, None, None, None, "Google", "en",
              [["Sunnyvale, CA, USA"], ["New York, NY, USA"]], None, None, [stamp, 0]],
             ["456", "Marketing Intern", "x", None, None, None, None, "Google", "en", [["NYC"]], None, None, [stamp, 0]]],
            None, 2, 20]
    html = "<script>AF_initDataCallback({key: 'ds:1', hash: '2', data:" + __import__("json").dumps(data) + ", sideChannel: {}});</script>"
    got = fetch_google(GetSession([Text(html)]), "Google", "site:google", wants, now=NOW)
    assert [(p.row_id, p.location) for p in got] == [("123", "Sunnyvale, CA, USA; New York, NY, USA")]
    assert got[0].apply_url.endswith("/jobs/results/123")
    with pytest.raises(SourceError):
        fetch_google(GetSession([Text("<html>changed</html>")]), "Google", "site:google", wants, now=NOW)


def test_fetch_apple():
    import json
    hydration = {"loaderData": {"search": {"searchResults": [
        {"postingTitle": "Security Engineering Internship", "positionId": "200-1", "postDateInGMT": "2026-11-01T18:58:20.01Z",
         "transformedPostingTitle": "security-engineering-internship",
         "locations": [{"city": "Cupertino", "stateProvince": "California", "name": "Cupertino"}]},
    ]}}}
    html = 'window.__staticRouterHydrationData = JSON.parse(' + json.dumps(json.dumps(hydration)) + ');'
    got = fetch_apple(GetSession([Text(html)]), "Apple", "site:apple", wants, now=NOW)
    assert [(p.row_id, p.location, p.apply_url) for p in got] == [
        ("200-1", "Cupertino, California", "https://jobs.apple.com/en-us/details/200-1/security-engineering-internship")]


from jobscanner.sites import fetch_ashby, fetch_lever


def test_fetch_lever():
    stamp = int(NOW.timestamp() * 1000) - 3_600_000
    jobs = [{"id": "a", "text": "Security Intern", "createdAt": stamp, "hostedUrl": "https://jobs.lever.co/p/a",
             "categories": {"location": "Washington, D.C.", "allLocations": ["Washington, D.C.", "Palo Alto, CA"]}},
            {"id": "b", "text": "Security Intern old", "createdAt": stamp - 30 * 86_400_000, "hostedUrl": "https://jobs.lever.co/p/b"}]
    got = fetch_lever(GetSession([Resp(jobs)]), "palantir", "Palantir", "site:palantir", wants, now=NOW)
    assert [(p.row_id, p.location) for p in got] == [("a", "Washington, D.C.; Palo Alto, CA")]
    with pytest.raises(SourceError):
        fetch_lever(GetSession([]), "../x", "P", "k", wants, now=NOW)


def test_fetch_ashby():
    addr = lambda city, region: {"postalAddress": {"addressLocality": city, "addressRegion": region}}
    jobs = [{"id": "a", "title": "Security Engineering Intern", "jobUrl": "https://jobs.ashbyhq.com/o/a",
             "publishedAt": "2026-11-01T10:00:00+00:00", "location": "San Francisco", "address": addr("San Francisco", "California"),
             "secondaryLocations": [{"location": "NYC", "address": addr("New York", "New York")}]},
            {"id": "b", "title": "Security Intern unlisted", "jobUrl": "https://jobs.ashbyhq.com/o/b", "isListed": False,
             "publishedAt": "2026-11-01T10:00:00+00:00"}]
    got = fetch_ashby(GetSession([Resp({"jobs": jobs})]), "openai", "OpenAI", "site:openai", wants, now=NOW)
    assert [(p.row_id, p.location) for p in got] == [("a", "San Francisco, California; New York, New York")]


def test_session_retries_server_errors():
    from jobscanner.source import new_session
    retry = new_session().get_adapter("https://bah.wd1.myworkdayjobs.com").max_retries
    assert retry.total == 2 and 502 in retry.status_forcelist and retry.allowed_methods is None


def test_intern_facet_ignores_internal_audit():
    from jobscanner.sites import _intern_facet
    facets = [{"facetParameter": "workerSubType", "values": [{"descriptor": "Internal Audit Quality Assurance", "id": "ia"}]},
              {"facetParameter": "Job_Family_Group", "values": [{"descriptor": "Intern - Group", "id": "int"}]}]
    assert _intern_facet(facets) == ("Job_Family_Group", "int")


def test_banks_summer_analyst_counts_as_internship():
    from jobscanner.config import ListSpec
    spec = ListSpec(key="site:citi", title_require=["intern", "co-op", "summer analyst"], title_include=["cyber"])
    assert spec.wants("Technology, Cyber Security, Summer Analyst, Irving")
