from datetime import datetime, timedelta, timezone

import pytest

from jobscanner import source
from jobscanner.source import SourceError, fetch_github_list, parse_github_listings

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
REPO = "SimplifyJobs/Summer2027-Internships"


def item(id, hours_ago=1, **kw):
    base = dict(id=id, company_name="Microsoft", title="Software Engineer Intern - Security",
                active=True, is_visible=True, url=f"https://jobs.example.com/{id}",
                locations=["Redmond, WA", "SF"], date_posted=int((NOW - timedelta(hours=hours_ago)).timestamp()))
    base.update(kw)
    return base


def test_parse_keeps_active_recent_jobs_newest_first():
    midnight = int(datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp())
    items = [
        item("old", hours_ago=24 * 5),
        item("closed", active=False),
        item("hidden", is_visible=False),
        item("nourl", url="javascript:alert(1)"),
        item("day", date_posted=midnight),
        item("new", hours_ago=1),
        item("newer", hours_ago=0.5),
    ]
    got = parse_github_listings(items, "github", REPO, NOW - timedelta(days=3))
    assert [p.row_id for p in got] == ["newer", "new", "day"]
    p = got[0]
    assert p.company == "Microsoft" and p.location == "Redmond, WA; SF" and p.source == "SimplifyJobs"
    assert p.posted_at == NOW - timedelta(minutes=30)
    day = got[-1]
    assert day.posted_at is None and day.date == "Oct 26"   # day-only stamp: no fake time of day


def test_parse_rejects_non_list():
    with pytest.raises(SourceError):
        parse_github_listings({"oops": 1}, "github", REPO, NOW)


class Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, by_branch):
        self.by_branch, self.urls = by_branch, []

    def get(self, url, timeout):
        self.urls.append(url)
        return next((r for b, r in self.by_branch.items() if f"/{b}/" in url), Resp(404))


def test_fetch_falls_back_to_main_branch():
    s = FakeSession({"main": Resp(200, [item("a")])})
    got = fetch_github_list(s, REPO, "github", now=NOW)
    assert [p.row_id for p in got] == ["a"]
    assert s.urls[0].startswith("https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/")


def test_fetch_validates_repo_and_missing_file():
    with pytest.raises(SourceError):
        fetch_github_list(FakeSession({}), "evil.com/x/y", "github", now=NOW)
    with pytest.raises(SourceError):
        fetch_github_list(FakeSession({}), REPO, "github", now=NOW)
