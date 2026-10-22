import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobscanner import source
from jobscanner.source import SourceError, fetch_jobs, parse_jobs, parse_list_map, posted_at, stamps_are_pacific

from .test_filters import posting

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 22, 3, 23, tzinfo=timezone.utc)
MICROSOFT_MS = 1792608805000  # shown on the site as "1 hour ago" at about 03:00 UTC


def test_parse_list_map_us_only():
    lists = parse_list_map((FIX / "site.html").read_text())
    assert lists == {
        "swe": "intern:us:swe",
        "eng": "intern:us:engineering_development",
        "cs": "intern:us:cyber_security",
    }


def test_parse_list_map_empty_page_raises():
    with pytest.raises(SourceError):
        parse_list_map("<html></html>")


def test_stamps_are_pacific_wall_time():
    assert posted_at(MICROSOFT_MS, pacific=True) == datetime(2026, 10, 22, 1, 53, 25, tzinfo=timezone.utc)
    assert stamps_are_pacific(MICROSOFT_MS, NOW)
    # if jobright ever sends real UTC, Pacific conversion lands in the future -> use UTC
    real_utc_ms = int((NOW - timedelta(minutes=5)).timestamp() * 1000)
    assert not stamps_are_pacific(real_utc_ms, NOW)


def test_parse_jobs():
    postings = parse_jobs(json.loads((FIX / "jobs_page.json").read_text()), "cs")
    p = postings[0]
    assert (p.company, p.location, p.salary, p.size) == ("Microsoft", "Cambridge, MA", "$5690-$11210/mon", "10000+")
    assert p.apply_url == "https://jobright.ai/jobs/info/6ad953c2b0725dc268ddbf33"
    assert p.date == "2026-10-21"  # Pacific date, as the site shows it
    assert p.posted_at == datetime(2026, 10, 22, 1, 53, 25, tzinfo=timezone.utc)
    assert [x.company for x in postings] == ["Microsoft", "Intel", "Gulfstream Aerospace"]


def test_parse_jobs_api_error():
    with pytest.raises(SourceError, match="boom"):
        parse_jobs({"success": False, "errorMsg": "boom"}, "cs")


def test_parse_jobs_rejects_bad_id():
    payload = {"success": True, "result": {"jobList": [{"jobId": "../evil", "postedAt": 1, "properties": {}}]}}
    with pytest.raises(SourceError):
        parse_jobs(payload, "cs")


def _job(i, ms):
    return {"jobId": f"{i:024x}", "postedAt": ms, "properties": {"title": f"T{i}", "company": "C"}}


class FakeSession:
    """Serves ``jobs`` (newest first) in pages like the real API."""

    def __init__(self, jobs):
        self.jobs, self.calls = jobs, []

    def post(self, url, params, json, timeout):
        assert url == source.JOBS_API and json == {"category": "intern:us:cyber_security"}
        self.calls.append(params["position"])
        page = self.jobs[params["position"]: params["position"] + params["count"]]

        class R:
            def raise_for_status(self): pass
            def json(self): return {"success": True, "result": {"jobList": page, "total": 999}}
        return R()


def test_fetch_jobs_pages_until_window(monkeypatch):
    monkeypatch.setattr(source, "PAGE_SIZE", 3)
    newest = MICROSOFT_MS
    jobs = [_job(i, newest - i * 20 * 60_000) for i in range(10)]  # one every 20 minutes
    s = FakeSession(jobs)
    got = fetch_jobs(s, "intern:us:cyber_security", "cs", since=NOW - timedelta(hours=2), now=NOW)
    # newest is 1h30m old, so within 2h: jobs 0 (1:30 ago) and 1 (1:50 ago)
    assert [p.title for p in got] == ["T0", "T1"]
    assert s.calls == [0]  # first page already reached older jobs


def test_fetch_jobs_reads_more_pages_when_needed(monkeypatch):
    monkeypatch.setattr(source, "PAGE_SIZE", 3)
    jobs = [_job(i, MICROSOFT_MS - i * 60_000) for i in range(10)]  # one a minute
    s = FakeSession(jobs)
    got = fetch_jobs(s, "intern:us:cyber_security", "cs", since=NOW - timedelta(hours=24), now=NOW)
    assert len(got) == 10 and s.calls == [0, 3, 6, 9]


def test_fetch_jobs_empty_list_raises():
    with pytest.raises(SourceError):
        fetch_jobs(FakeSession([]), "intern:us:cyber_security", "cs", since=NOW, now=NOW)


def test_job_keys_ignore_tracking_query():
    a = posting(list_key="cs", apply_url="https://jobright.ai/jobs/info/abc?utm_campaign=Cyber Security")
    b = posting(list_key="eng", apply_url="https://jobright.ai/jobs/info/abc?utm_campaign=Engineering")
    assert a.job_keys == b.job_keys
    assert a.job_keys == ("jobright.ai/jobs/info/abc", "acme|security intern")
    c = posting(apply_url="", company="Acme", title="Intern  ", location="Austin, TX")
    assert c.job_keys == ("acme|intern",)
