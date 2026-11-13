from urllib.parse import parse_qs, urlsplit

from jobscanner import tracker
from jobscanner.tracker import Marks, links, load, role_key

from .test_filters import posting

ENV = {"TRACKER_URL": "https://script.google.com/macros/s/abc/exec", "TRACKER_TOKEN": "secret"}


def test_role_key_drops_trailing_location():
    a = role_key("Booz Allen Hamilton", "University - 2027 Summer Games Cyber Security Intern - Honolulu, HI")
    b = role_key("Booz Allen Hamilton", "University - 2027 Summer Games Cyber Security Intern - Atlanta, GA")
    assert a == b == "booz allen hamilton|university 2027 summer games cyber security intern"
    assert role_key("Visa", "Network Intern - 2027, Ashburn, VA") == "visa|network intern 2027"


def test_links_carry_token_and_job():
    p = posting(company="NVIDIA", title="Security Intern", apply_url="https://jobright.ai/jobs/info/abc")
    got = links(p, ENV)
    q = parse_qs(urlsplit(got["applied"]).query)
    assert q["t"] == ["secret"] and q["a"] == ["applied"] and q["id"] == ["jobright.ai/jobs/info/abc"]
    assert parse_qs(urlsplit(got["skip"]).query)["a"] == ["skip"]
    assert links(p, {}) is None
    assert links(p, {**ENV, "TRACKER_URL": "https://evil.example/exec"}) is None


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_load_marks_and_hides():
    rows = [{"status": "Applied", "id": "jobright.ai/jobs/info/a", "role": "acme|security intern"},
            {"status": "Not interested", "id": "x/1", "role": "booz allen hamilton|cyber intern"}]

    class S:
        def get(self, url, params, timeout):
            assert params == {"t": "secret", "a": "list"}
            return Resp({"rows": rows})
    marks = load(S(), ENV)
    assert marks.hides(posting(apply_url="https://jobright.ai/jobs/info/a"))
    assert marks.hides(posting(company="Booz Allen Hamilton", title="Cyber Intern - Huntsville, AL", apply_url="https://y/2"))
    assert not marks.hides(posting(company="Acme", title="Security Intern", apply_url="https://z/3"))  # applied ≠ muted


def test_load_never_fails_the_run():
    class S:
        def get(self, *a, **k):
            raise ConnectionError("down")
    assert load(S(), ENV).jobs == {} and load(None, {}).jobs == {}


def test_rows_keep_stage_and_date_and_old_scripts_still_load():
    rows = [{"status": "Interview", "id": "x/1", "role": "acme|a", "company": "Acme", "title": "A", "link": "https://x/1",
             "marked": "2026-11-01T15:00:00.000Z"},
            {"status": "Applied", "id": "x/2", "role": "acme|b"}]   # an older Code.gs: no date or details

    class S:
        def get(self, url, params, timeout):
            return Resp({"rows": rows})
    marks = load(S(), ENV)
    assert marks.loaded and [r["title"] for r in marks.with_status(tracker.INTERVIEW)] == ["A"]
    assert marks.with_status(tracker.INTERVIEW)[0]["marked"].isoformat() == "2026-11-01T15:00:00+00:00"
    assert marks.with_status(tracker.APPLIED)[0]["marked"] is None
    assert marks.hides(posting(apply_url="https://x/1"))   # any stage means you've dealt with it


def test_push_sends_matches_to_the_dashboard_and_never_fails_the_run():
    from datetime import datetime, timezone

    from jobscanner.filters import evaluate

    from .test_filters import CRIT
    m = evaluate(posting(company="NVIDIA", salary="$40 /hr", apply_url="https://x/9"), CRIT)
    m.fit, m.fit_why, m.draft = 88, "Good.", "COVER LETTER..."
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)

    class S:
        sent = []

        def post(self, url, json, timeout):
            self.sent.append((url, json))
            return Resp({"ok": True})
    tracker.push(S(), [m], now, ENV)
    url, body = S.sent[0]
    assert url == ENV["TRACKER_URL"] and body["t"] == "secret" and body["a"] == "matches"
    row = body["rows"][0]
    assert (row["id"], row["fit"], row["pay"], row["top"], row["draft"]) == ("x/9", 88, 40, True, "COVER LETTER...")
    assert tracker.dashboard_url(ENV) == ENV["TRACKER_URL"] + "?t=secret&a=dashboard" and tracker.dashboard_url({}) is None

    class Down:
        def post(self, *a, **k):
            raise ConnectionError("down")
    tracker.push(Down(), [m], now, ENV)     # logged, not raised
    tracker.push(None, [m], now, {})        # not set up: nothing to do


class Sheet:
    """A fake web app: answers GETs by action and records POSTs."""

    def __init__(self, gets=None, post=None, fail=False):
        self.gets, self.post_reply, self.fail, self.asked, self.posted = gets or {}, post or {"ok": True}, fail, [], []

    def get(self, url, params=None, timeout=None):
        self.asked.append(params)
        if self.fail:
            raise OSError("https://script.google.com/secret-in-error")
        return Resp(self.gets.get(params["a"], {}))

    def post(self, url, json=None, timeout=None):
        self.posted.append(json)
        if self.fail:
            raise OSError("https://script.google.com/secret-in-error")
        return Resp(self.post_reply)


def test_matches_are_pushed_with_trimmed_posting_text():
    from datetime import datetime, timezone
    from jobscanner.filters import Match
    p = posting(company="NVIDIA", title="Security Intern", qualifications="Needs BGP.", description="d" * 9000)
    sheet = Sheet()
    tracker.push(sheet, [Match(posting=p, reasons=[], hourly=None, area=None, priority=None, size=None)], datetime(2026, 11, 1, tzinfo=timezone.utc), ENV)
    text = sheet.posted[0]["rows"][0]["posting"]
    assert text.startswith("Needs BGP.\nddd") and len(text) == tracker.POSTING_CHARS


def test_full_load_asks_for_and_returns_posting_text():
    sheet = Sheet({"list": {"rows": [{"status": "Applied", "id": "x/1", "role": "r", "company": "Cisco", "title": "Net",
                                      "posting": "Needs 802.1X"}, {"status": "Offer", "id": "x/2"}]}})
    marks = load(sheet, ENV, full=True)
    assert sheet.asked[0] == {"t": "secret", "a": "list", "full": "1"}
    assert [r["posting"] for r in marks.rows] == ["Needs 802.1X", ""]
    load(sheet, ENV)
    assert "full" not in sheet.asked[1]


def test_guides_lists_past_titles_or_none_when_it_cannot_tell(caplog):
    sheet = Sheet({"guides": {"guides": [{"sent": "2026-11-08T23:00:00Z", "title": "Earlier"}]}})
    assert tracker.guides(sheet, ENV) == [{"sent": "2026-11-08T23:00:00Z", "title": "Earlier"}]
    assert tracker.guides(Sheet({"guides": {"guides": []}}), ENV) == []
    assert tracker.guides(Sheet({"guides": {"rows": []}}), ENV) is None        # an older script: no 'guides' in its reply
    assert tracker.guides(Sheet(fail=True), ENV) is None and "secret-in-error" not in caplog.text
    assert tracker.guides(sheet, {}) is None


def test_guide_is_saved_as_json_and_linked(caplog):
    sheet = Sheet()
    assert tracker.push_guide(sheet, "2026-11-15-lab", {"title": "Lab", "parts": []}, ENV) is True
    sent = sheet.posted[0]
    assert sent["a"] == "guide" and sent["id"] == "2026-11-15-lab" and sent["title"] == "Lab" and sent["t"] == "secret"
    assert sent["json"] == '{"title":"Lab","parts":[]}'
    assert tracker.push_guide(Sheet(post={"ok": False, "error": "unknown action"}), "g", {}, ENV) is False
    assert tracker.push_guide(Sheet(fail=True), "g", {}, ENV) is False and "secret-in-error" not in caplog.text
    assert tracker.push_guide(sheet, "g", {}, {}) is False
    q = parse_qs(urlsplit(tracker.guide_url("2026-11-15-lab", ENV)).query)
    assert q == {"t": ["secret"], "a": ["guide"], "id": ["2026-11-15-lab"]} and tracker.guide_url("g", {}) is None
