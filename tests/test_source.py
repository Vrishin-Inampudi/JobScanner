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
