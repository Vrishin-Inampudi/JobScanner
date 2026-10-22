import copy
import json
from pathlib import Path

import pytest

from jobscanner.source import SourceError, parse_list_map, parse_view

FIX = Path(__file__).parent / "fixtures"


def test_parse_list_map_us_only():
    lists = parse_list_map((FIX / "site.html").read_text())
    assert lists["cs"] == "https://airtable.com/embed/appVWWidEOaCW7Biy/shrC0SNKnwMV3Fem9"
    assert lists["swe"] == "https://airtable.com/embed/app17F0kkWQZhC6HB/shrOTtndhc6HSgnYb"
    assert lists["eng"] == "https://airtable.com/embed/appmXHK6JoqQcSKf6/shruwVkAKPjHFdVJu"
    assert "app742LMLO7tQP9dO" not in "".join(lists.values())  # Canadian lists excluded


def test_parse_list_map_empty_page_raises():
    with pytest.raises(SourceError):
        parse_list_map("<html></html>")


def test_parse_view_resolves_cells():
    payload = json.loads((FIX / "view.json").read_text())
    postings = parse_view(payload, "cs")
    assert len(postings) == 3
    p = postings[0]
    assert p.uid == "cs:recoduPGtutvsfL40"
    assert p.company == "Galaxy"
    assert p.size == "501-1000"
    assert p.salary == "$55-$55 /hr"
    assert p.apply_url.startswith("https://jobright.ai/jobs/info/")
    assert (postings[2].company, postings[2].size) == ("ByteDance", "10000+")
    assert postings[2].location == "San Jose, CA, United States"


def test_parse_view_missing_column_raises():
    payload = json.loads((FIX / "view.json").read_text())
    broken = copy.deepcopy(payload)
    broken["data"]["table"]["columns"] = [
        c for c in broken["data"]["table"]["columns"] if c["name"] != "Company"
    ]
    with pytest.raises(SourceError, match="Company"):
        parse_view(broken, "cs")


def test_parse_view_empty_rows_raises():
    payload = json.loads((FIX / "view.json").read_text())
    payload["data"]["table"]["rows"] = []
    with pytest.raises(SourceError):
        parse_view(payload, "cs")


@pytest.mark.parametrize("url", [
    "http://airtable.com/embed/appX/shrY",
    "https://airtable.com.evil.com/embed/appX/shrY",
    "https://evil.com/embed/appX/shrY",
    "https://airtable.com/other/embed/appX/shrY",
    "https://user@airtable.com/embed/appX/shrY",
    "https://airtable.com:8443/embed/appX/shrY",
    "https://airtable.com\\@evil.com/embed/appX/shrY",
])
def test_rejects_non_airtable_embed(url):
    from jobscanner.source import _airtable_embed
    with pytest.raises(SourceError):
        _airtable_embed(url)


def test_embed_url_is_rebuilt():
    from jobscanner.source import _airtable_embed
    assert _airtable_embed("https://airtable.com/embed/appX/shrY/") == ("appX", "https://airtable.com/embed/appX/shrY")


def test_rejects_off_host_data_path():
    from jobscanner.source import fetch_view

    class Resp:
        text = 'urlWithParams: "@evil.com\\u002Fv0.3\\u002Fview"'
        def raise_for_status(self): pass

    class Session:
        def get(self, url, **kw):
            assert url == "https://airtable.com/embed/appX/shrY"  # never reaches the data request
            return Resp()

    with pytest.raises(SourceError, match="unexpected Airtable data URL"):
        fetch_view(Session(), "https://airtable.com/embed/appX/shrY", "cs")


def test_job_keys_ignore_tracking_query():
    from .test_filters import posting
    a = posting(list_key="cs", apply_url="https://jobright.ai/jobs/info/abc?utm_campaign=Cyber Security")
    b = posting(list_key="eng", apply_url="https://jobright.ai/jobs/info/abc?utm_campaign=Engineering")
    assert a.job_keys == b.job_keys
    assert a.job_keys[0] == "jobright.ai/jobs/info/abc"
    assert a.job_keys[1] == "acme|security intern|austin, tx, united states"
    c = posting(apply_url="", company="Acme", title="Intern  ", location="Austin, TX")
    assert c.job_keys == ("acme|intern|austin, tx",)
