import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobscanner import source
from jobscanner.__main__ import run
from jobscanner.emailer import EmailConfigError, pretty_location, render, send
from jobscanner.filters import evaluate

from .test_filters import CRIT, posting as _posting

NOW = datetime(2026, 10, 22, 3, 5, tzinfo=timezone.utc)

CONFIG = """
lists:
  - key: cs
  - key: swe
    title_include: [network]
max_age_hours: 2
match: any
criteria:
  min_hourly_pay: 35
  locations: [CA]
  large_company_min_size: 10000
  priority_companies: [NVIDIA]
"""


def posting(row_id, minutes_ago=10, **kw):
    kw.setdefault("apply_url", f"https://jobright.ai/jobs/info/{row_id}")
    kw.setdefault("title", f"Security Intern {row_id}")
    kw.setdefault("posted_at", NOW - timedelta(minutes=minutes_ago))
    return _posting(row_id=row_id, **kw)


@pytest.fixture
def site(monkeypatch):
    """Fake job feed: tests set ``jobs[key]``; fetch_jobs applies the age window like the real one."""
    jobs = {"cs": [], "swe": []}
    monkeypatch.setattr(source, "fetch_list_map", lambda s: {"cs": "intern:us:cyber_security", "swe": "intern:us:swe"})
    monkeypatch.setattr(
        source, "fetch_jobs",
        lambda s, category, key, since, now=None: [p for p in jobs[key] if p.posted_at >= since],
    )
    return jobs


@pytest.fixture
def paths(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG)
    return str(cfg), str(tmp_path / "state" / "seen.json")


class Outbox(list):
    def __call__(self, subject, text, html):
        self.append((subject, text, html))


def go(paths, outbox, now=NOW, **kw):
    return run(*paths, session=object(), sender=outbox, now=now, **kw)


def test_emails_recent_matches_once(site, paths):
    site["cs"] = [
        posting("nvidia", company="NVIDIA"),
        posting("boring", title="Help Desk Intern"),               # doesn't qualify
        posting("old", company="NVIDIA", minutes_ago=3 * 60),      # outside the 2h window
    ]
    outbox = Outbox()
    assert go(paths, outbox) == 0
    assert len(outbox) == 1
    text = outbox[0][1]
    assert "jobs/info/nvidia" in text and "boring" not in text and "jobs/info/old" not in text

    site["cs"].append(posting("next", company="NVIDIA", minutes_ago=5))
    go(paths, outbox, now=NOW + timedelta(hours=1))
    assert len(outbox) == 2
    assert "jobs/info/next" in outbox[1][1] and "jobs/info/nvidia" not in outbox[1][1]

    go(paths, outbox, now=NOW + timedelta(hours=1, minutes=5))
    assert len(outbox) == 2  # nothing re-sent


def test_title_filter_and_cross_list_dedup(site, paths):
    same = dict(company="NVIDIA", title="Network Security Intern", apply_url="https://jobright.ai/jobs/info/dup")
    site["cs"] = [posting("c1", **same)]
    site["swe"] = [
        posting("s1", list_key="swe", **same),
        posting("s2", list_key="swe", title="Frontend Intern", company="NVIDIA"),
    ]
    outbox = Outbox()
    go(paths, outbox)
    assert len(outbox) == 1
    assert outbox[0][1].count("jobs/info/dup") == 1
    assert "jobs/info/s2" not in outbox[0][1]


def test_failed_send_keeps_state(site, paths):
    site["cs"] = [posting("n", company="NVIDIA")]

    def boom(*a):
        raise EmailConfigError("no secrets")

    with pytest.raises(EmailConfigError):
        go(paths, boom)
    outbox = Outbox()
    go(paths, outbox)
    assert len(outbox) == 1  # retried


def test_unknown_list_fails_run_but_others_processed(site, paths):
    cfg, state = paths
    Path(cfg).write_text(CONFIG.replace("- key: swe", "- key: nope"))
    site["cs"] = [posting("a")]
    assert run(cfg, state, session=object(), sender=Outbox(), now=NOW) == 1
    assert "cs" in json.loads(Path(state).read_text())["lists"]


def test_dry_run_does_not_save(site, paths, capsys):
    site["cs"] = [posting("a", company="NVIDIA")]
    outbox = Outbox()
    go(paths, outbox, dry_run=True)
    assert outbox == [] and "jobs/info/a" in capsys.readouterr().out
    assert not Path(paths[1]).exists()


def test_preview_ignores_history(site, paths, capsys):
    site["cs"] = [posting("a", company="NVIDIA")]
    go(paths, Outbox())  # emails and records it
    go(paths, Outbox(), dry_run=True, preview=True)
    assert "jobs/info/a" in capsys.readouterr().out


def test_render_escapes_subject_and_age():
    ms = [evaluate(posting(str(i), minutes_ago=m, company=c, title="<b>Intern</b>", salary="$40 /hr"), CRIT)
          for i, (c, m) in enumerate([("NVIDIA", 12), ("CrowdStrike", 75), ("Booz Allen", 5), ("Apple", 1)])]
    subject, text, html = render(ms, now=NOW)
    assert subject == "🎯 4 new internships — NVIDIA, CrowdStrike, Booz Allen…"
    assert "&lt;b&gt;Intern&lt;/b&gt;" in html and "<b>Intern</b>" not in html
    assert "posted 12 min ago" in text and "posted 1h 15m ago" in text
    assert "Apply: https://jobright.ai/jobs/info/0" in text
    assert "4 new internships" in html
    assert "★ Top company" in html and "$40/hr" in html
    assert "11:05 PM ET" in html and ">21</span>" in html   # today badge
    assert "10:53 PM · 12 min ago" in html                   # posting time, ET


def test_pretty_location():
    assert pretty_location("Multi Location\nLehi, UT, United States\nSan Jose, CA, United States") == "Lehi, UT / San Jose, CA"
    assert pretty_location("Cambridge, MA") == "Cambridge, MA"
    assert pretty_location("Menlo Park, CA; New York, NY") == "Menlo Park, CA / New York, NY"
    assert pretty_location("") == ""


def test_send_requires_secrets():
    with pytest.raises(EmailConfigError):
        send("s", "t", "h", env={})


def test_github_list_is_checked_and_deduped_with_jobright(site, paths, monkeypatch):
    Path(paths[0]).write_text(CONFIG.replace("lists:\n", "lists:\n  - key: gh\n    repo: SimplifyJobs/Summer2027-Internships\n    title_include: [security]\n", 1))
    gh = [
        _posting(list_key="gh", row_id="g1", company="NVIDIA", title="Security Intern dup",
                 apply_url="https://jobs.example.com/g1", source="SimplifyJobs GitHub list", posted_at=None, date="Oct 26"),
        _posting(list_key="gh", row_id="g2", company="Acme", title="Security Intern SF",
                 location="SF", apply_url="https://jobs.example.com/g2", source="SimplifyJobs GitHub list", posted_at=None, date="Oct 26"),
        _posting(list_key="gh", row_id="g3", company="Acme", title="Security Intern TX",
                 apply_url="https://jobs.example.com/g3", source="SimplifyJobs GitHub list", posted_at=None, date="Oct 26"),
    ]
    calls = []
    monkeypatch.setattr(source, "fetch_github_list", lambda s, repo, key, now=None: calls.append(repo) or gh)
    site["cs"] = [posting("nv", company="NVIDIA", title="Security Intern dup")]   # same job on jobright
    outbox = Outbox()
    go(paths, outbox)
    assert calls == ["SimplifyJobs/Summer2027-Internships"]
    text = outbox[0][1]
    assert text.count("Security Intern dup") == 1 and "jobs/info/nv" in text      # sent once
    assert "jobs.example.com/g2" in text and "via SimplifyJobs GitHub list" in text and "posted Oct 26" in text
    assert "jobs.example.com/g3" not in text                                     # Austin, no pay: doesn't qualify
    go(paths, outbox, now=NOW + timedelta(minutes=30))
    assert len(outbox) == 1                                                      # not re-sent


def test_company_site_first_check_records_then_emails_new(site, paths, monkeypatch):
    Path(paths[0]).write_text(CONFIG + """
company_sites:
  title_require: [intern]
  sites:
    - {company: Cloudflare, greenhouse: cloudflare}
""")
    jobs = [_posting(list_key="site:cloudflare", row_id="old", company="Cloudflare", title="Security Intern A",
                     location="San Francisco, CA", apply_url="https://boards.greenhouse.io/cf/jobs/old",
                     source="Cloudflare careers site")]
    asked = []   # which jobs' own pages would be read, per run

    def fake(s, board, company, key, wants, now=None, details=None):
        asked.append([p.row_id for p in jobs if details(p.apply_url)])
        return list(jobs)
    monkeypatch.setattr("jobscanner.sites.fetch_greenhouse", fake)
    outbox = Outbox()
    go(paths, outbox)
    assert outbox == []                                        # first check: just records what's there
    jobs.append(_posting(list_key="site:cloudflare", row_id="new", company="Cloudflare", title="Security Intern B",
                         location="San Francisco, CA", apply_url="https://boards.greenhouse.io/cf/jobs/new",
                         source="Cloudflare careers site"))
    go(paths, outbox, now=NOW + timedelta(minutes=30))
    assert len(outbox) == 1 and "cf/jobs/new" in outbox[0][1] and "cf/jobs/old" not in outbox[0][1]
    assert "via company site" in outbox[0][1]
    assert asked == [[], ["new"]]   # job pages are only read for jobs that could be emailed


def test_only_title_matched_jobs_are_remembered(site, paths):
    site["swe"] = [posting("fe", list_key="swe", title="Frontend Intern"), posting("net", list_key="swe", title="Network Intern")]
    go(paths, Outbox())
    seen = json.loads(Path(paths[1]).read_text())["lists"]["swe"]
    assert any("net" in k for k in seen) and not any("fe" in k or "frontend" in k for k in seen)


DIGEST = "digest_hour: 8\n"
NOON_ET = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)


def test_urgent_now_rest_in_morning_digest(site, paths):
    _quiet_config(paths, DIGEST)
    at = lambda row_id, **kw: _posting(row_id=row_id, apply_url=f"https://jobright.ai/jobs/info/{row_id}",
                                       title=f"Security Intern {row_id}", posted_at=NOON_ET - timedelta(minutes=5), **kw)
    site["cs"] = [
        at("nv", company="NVIDIA"),                       # top company: urgent
        at("ca", location="San Jose, CA"),                # location only: can wait
        at("pay", salary="$40 /hr"),                      # pay: urgent
    ]
    outbox = Outbox()
    go(paths, outbox, now=NOON_ET)
    go(paths, outbox, now=NOON_ET + timedelta(minutes=30))   # same day: queued jobs wait for tomorrow
    assert len(outbox) == 1
    assert "jobs/info/nv" in outbox[0][1] and "jobs/info/pay" in outbox[0][1] and "jobs/info/ca" not in outbox[0][1]
    next_8am = datetime(2026, 10, 23, 12, 30, tzinfo=timezone.utc)          # 8:30 AM ET
    go(paths, outbox, now=next_8am)
    assert len(outbox) == 2 and outbox[1][0].startswith("☀️ Daily digest: 1 more internship")
    assert "jobs/info/ca" in outbox[1][1]
    go(paths, outbox, now=next_8am + timedelta(minutes=30))
    assert len(outbox) == 2                                                  # one digest a day


def test_company_matches_need_my_cities(site, paths):
    _quiet_config(paths, "")
    Path(paths[0]).write_text(Path(paths[0]).read_text().replace("  priority_companies: [NVIDIA]",
                                                                 "  priority_companies: [NVIDIA]\n  company_needs_location: true"))
    site["cs"] = [posting("hsv", company="NVIDIA", location="Huntsville, AL", minutes_ago=5),
                  posting("sj", company="NVIDIA", location="San Jose, CA", minutes_ago=5),
                  posting("rem", company="NVIDIA", location="United States", work_model="Remote", minutes_ago=5),
                  posting("hsvpay", location="Huntsville, AL", salary="$50 /hr", minutes_ago=5)]
    outbox = Outbox()
    go(paths, outbox)
    text = outbox[0][1]
    assert "jobs/info/hsv\n" not in text and "jobs/info/sj" in text and "jobs/info/rem" in text
    assert "jobs/info/hsvpay" in text                                       # pay alone: anywhere


def test_tracker_marks_hide_jobs_and_links_show(site, paths, monkeypatch):
    from jobscanner import tracker
    monkeypatch.setenv("TRACKER_URL", "https://script.google.com/macros/s/abc/exec")
    monkeypatch.setenv("TRACKER_TOKEN", "tok")
    monkeypatch.setattr(tracker, "load", lambda s: tracker.Marks(muted={"nvidia|security intern muted"}))
    site["cs"] = [posting("a", company="NVIDIA", minutes_ago=5),
                  posting("b", company="NVIDIA", title="Security Intern muted - Austin, TX", minutes_ago=5)]
    outbox = Outbox()
    go(paths, outbox)
    subject, text, html = outbox[0]
    assert "jobs/info/a" in text and "jobs/info/b" not in text
    assert "Mark applied: https://script.google.com/macros/s/abc/exec?" in text and "&#10003; Applied" in html


def test_one_failing_site_does_not_stop_the_rest(site, paths, monkeypatch):
    Path(paths[0]).write_text(CONFIG + """
company_sites:
  sites:
    - {company: Cloudflare, greenhouse: cloudflare}
""")
    def boom(*a, **k):
        raise RuntimeError("site down")
    monkeypatch.setattr("jobscanner.sites.fetch_greenhouse", boom)
    site["cs"] = [posting("nv", company="NVIDIA", minutes_ago=5)]
    outbox = Outbox()
    assert go(paths, outbox) == 0 and len(outbox) == 1  # a site being down is a warning, not a failed run
    assert "hasn't loaded" not in outbox[0][1]          # a brief outage isn't worth mentioning
    # Still down a day later: the next email says so, until the site loads again.
    later = NOW + timedelta(hours=25)
    site["cs"] = [posting("nv2", company="NVIDIA", title="Security Intern Two", posted_at=later - timedelta(minutes=5))]
    go(paths, outbox, now=later)
    assert "Cloudflare's career site hasn't loaded since Oct 21" in outbox[1][1]
    assert "Cloudflare&#x27;s career site hasn&#x27;t loaded" in outbox[1][2]
    monkeypatch.setattr("jobscanner.sites.fetch_greenhouse", lambda *a, **k: [])
    go(paths, outbox, now=later + timedelta(minutes=30))
    assert json.loads(Path(paths[1]).read_text())["trouble"] == {}


def test_test_email_sends_last_day_without_touching_state(site, paths):
    site["cs"] = [posting("recent", company="NVIDIA"), posting("today", company="NVIDIA", minutes_ago=20 * 60)]
    go(paths, Outbox())  # normal run emails "recent" and records it
    state_before = Path(paths[1]).read_text()

    outbox = Outbox()
    go(paths, outbox, test=True)
    assert len(outbox) == 1 and outbox[0][0].startswith("[Test] 🎯 2 new internships")
    assert "jobs/info/recent" in outbox[0][1] and "jobs/info/today" in outbox[0][1]
    assert Path(paths[1]).read_text() == state_before


def _quiet_config(paths, extra):
    Path(paths[0]).write_text(CONFIG + extra)


def test_quiet_email_after_5_hours_of_nothing(site, paths):
    _quiet_config(paths, "empty_email_every_hours: 5\n")
    site["cs"] = [posting("boring", title="Help Desk Intern", minutes_ago=5)]  # posted, doesn't qualify
    outbox = Outbox()
    go(paths, outbox)                                     # first run starts the clock, no email
    assert outbox == []
    go(paths, outbox, now=NOW + timedelta(hours=3))       # 3h of nothing: still quiet
    assert outbox == []
    go(paths, outbox, now=NOW + timedelta(hours=4, minutes=55))   # cron jitter: ~5h counts
    assert len(outbox) == 1
    subject, text, html = outbox[0]
    assert subject.startswith("Nothing new")
    assert "Hi, nothing has been posted!" in html and "Check back in an hour." in html
    assert "No new matches in the last 5 hours" in text
    go(paths, outbox, now=NOW + timedelta(hours=6))       # clock restarted
    assert len(outbox) == 1


def test_half_hourly_runs(site, paths):
    _quiet_config(paths, "empty_email_every_hours: 5\ncheck_every_minutes: 30\n")
    outbox = Outbox()
    go(paths, outbox)                                      # starts the clock
    for half_hours in range(1, 10):                        # every 30 min for 4.5h: nothing yet
        go(paths, outbox, now=NOW + timedelta(minutes=30 * half_hours))
    assert outbox == []
    go(paths, outbox, now=NOW + timedelta(hours=5))        # 5h: the "nothing" email
    assert len(outbox) == 1 and "Check back in 30 minutes." in outbox[0][2]
    assert "Checked every 30 minutes" in outbox[0][2]
    site["cs"] = [posting("nv", company="NVIDIA", minutes_ago=-310)]   # posted at +5h10m
    go(paths, outbox, now=NOW + timedelta(hours=5, minutes=30))
    assert len(outbox) == 2 and "jobs/info/nv" in outbox[1][1]   # the next half-hour picks it up
    go(paths, outbox, now=NOW + timedelta(hours=6))
    assert len(outbox) == 2                                 # not re-sent


def test_job_email_restarts_the_quiet_clock(site, paths):
    _quiet_config(paths, "empty_email_every_hours: 5\n")
    outbox = Outbox()
    go(paths, outbox)                                      # starts the clock
    site["cs"] = [posting("nv", company="NVIDIA", minutes_ago=5)]
    go(paths, outbox, now=NOW + timedelta(hours=4))        # job email at +4h
    go(paths, outbox, now=NOW + timedelta(hours=6))        # only 2h since the job email
    assert len(outbox) == 1 and "NVIDIA" in outbox[0][0]


def test_no_quiet_email_when_a_list_failed(site, paths):
    cfg, state = paths
    Path(cfg).write_text(CONFIG.replace("- key: swe", "- key: nope") + "empty_email_every_hours: 1\n")
    outbox = Outbox()
    run(cfg, state, session=object(), sender=outbox, now=NOW)
    assert run(cfg, state, session=object(), sender=outbox, now=NOW + timedelta(hours=3)) == 1
    assert outbox == []


def test_no_quiet_email_by_default(site, paths):
    outbox = Outbox()
    go(paths, outbox)
    go(paths, outbox, now=NOW + timedelta(hours=8))
    assert outbox == []


QUIET = "quiet_hours: [0, 4]\n"   # midnight to 4 AM ET


def test_quiet_hours_skip_the_run(site, paths, monkeypatch):
    _quiet_config(paths, QUIET)
    site["cs"] = [posting("nv", company="NVIDIA", minutes_ago=5)]
    calls = []
    monkeypatch.setattr(source, "fetch_list_map", lambda s: calls.append(1) or {})
    one_am_et = datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc)
    outbox = Outbox()
    assert go(paths, outbox, now=one_am_et) == 0
    assert outbox == [] and calls == [] and not Path(paths[1]).exists()


def test_four_am_run_catches_up_on_quiet_hours(site, paths):
    _quiet_config(paths, QUIET)
    eleven_pm_et = datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc)
    four_am_et = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
    outbox = Outbox()
    go(paths, outbox, now=eleven_pm_et)
    # posted at 12:30 AM ET: 3.5h before the 4 AM run, outside the 2h window
    site["cs"] = [_posting(row_id="night", company="NVIDIA", title="Night Intern",
                           apply_url="https://jobright.ai/jobs/info/night",
                           posted_at=datetime(2026, 10, 22, 4, 30, tzinfo=timezone.utc))]
    go(paths, outbox, now=four_am_et)
    assert len(outbox) == 1 and "jobs/info/night" in outbox[0][1]


def test_test_mode_ignores_quiet_hours(site, paths):
    _quiet_config(paths, QUIET)
    site["cs"] = [posting("nv", company="NVIDIA", minutes_ago=5)]
    outbox = Outbox()
    go(paths, outbox, now=datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc), test=True)
    assert len(outbox) == 1


def test_card_shows_short_locations_pay_only_when_known_and_all_sources(site, paths):
    long = "; ".join(f"{c}, California, United States of America" for c in ("San Jose", "Irvine", "Fresno", "Chico", "Napa"))
    listed = evaluate(posting("a", company="NVIDIA", salary="$40 /hr", location=long), CRIT)
    unlisted = evaluate(posting("b", company="NVIDIA", salary="", location="San Jose, CA"), CRIT)
    _, text, html = render([listed, unlisted], now=NOW, sources="intern-list.com and 3 company career sites")
    assert "San Jose, CA / Irvine, CA / Fresno, CA +2 more" in html and "United States of America" not in html
    assert "white-space:nowrap\">San Jose" not in html           # a long location can't push the card wider
    assert html.count("Pay not listed") == 0 and text.count("Pay not listed") == 1   # no chip; one plain-text fact
    assert "from intern-list.com and 3 company career sites" in html


def test_sources_line():
    from jobscanner.__main__ import _sources
    from jobscanner.config import ListSpec
    specs = [ListSpec("cs"), ListSpec("gh", repo="SimplifyJobs/Summer2027-Internships"),
             ListSpec("site:a", company="A", greenhouse="a"), ListSpec("site:b", company="B", lever="b")]
    assert _sources(specs) == "intern-list.com, the SimplifyJobs GitHub list and 2 company career sites"
    assert _sources(specs[:1]) == "intern-list.com"


class FakeAssistant:
    """Scores by title: 'great' jobs fit well. Counts what it was asked for."""

    def __init__(self):
        self.scored, self.drafted = [], []

    def score(self, p):
        from jobscanner.ai import Fit
        self.scored.append(p.row_id)
        return Fit(90 if "great" in p.title else 50, f"why {p.row_id}")

    def draft(self, p):
        self.drafted.append(p.row_id)
        return f"COVER LETTER for {p.title}"


class DraftBox(list):
    def __call__(self, subject, text, html, attachments=()):
        self.append((subject, text, html, list(attachments)))


def test_ai_scores_matches_and_attaches_drafts_for_the_best(site, paths):
    Path(paths[0]).write_text(CONFIG + "ai: {draft_min_score: 75, max_drafts_per_day: 1}\n")
    site["cs"] = [posting("a", company="NVIDIA", title="great Security Intern A"),
                  posting("b", company="NVIDIA", title="great Security Intern B"),
                  posting("c", company="NVIDIA", title="Security Intern C")]
    helper, outbox = FakeAssistant(), DraftBox()
    run(*paths, session=object(), sender=outbox, now=NOW, assistant=helper)
    subject, text, html, attached = outbox[0]
    assert sorted(helper.scored) == ["a", "b", "c"] and len(helper.drafted) == 1    # one draft a day
    assert "Fit 90" in html and "Fit 50" in html and "Fit: 90/100. why a" in text
    assert len(attached) == 1 and attached[0][0].startswith("Draft - NVIDIA - great Security Intern")
    assert "COVER LETTER for great Security Intern" in attached[0][1]
    saved = Path(paths[1]).read_text()
    assert "COVER LETTER" not in saved and "why a" not in saved   # the state file may be public
    # Later the same day the daily limit is used up; nothing already sent is scored again.
    site["cs"].append(posting("d", company="NVIDIA", title="great Security Intern D", minutes_ago=1))
    run(*paths, session=object(), sender=outbox, now=NOW + timedelta(minutes=30), assistant=helper)
    assert helper.scored[3:] == ["d"] and len(helper.drafted) == 1 and outbox[1][3] == []


def test_without_ai_nothing_changes(site, paths):
    site["cs"] = [posting("a", company="NVIDIA")]
    outbox = Outbox()
    go(paths, outbox, assistant=None)
    assert "Fit" not in outbox[0][2] and "Fit:" not in outbox[0][1]


def test_queued_matches_are_scored_for_the_digest_and_nothing_from_the_resume_is_saved(site, paths):
    Path(paths[0]).write_text(CONFIG.replace("locations: [CA]", "locations: [TX]") + "digest_hour: 8\n")
    site["cs"] = [posting("q", company="Acme", title="great Security Intern", salary="")]   # matches on location only
    helper, outbox = FakeAssistant(), DraftBox()
    run(*paths, session=object(), sender=outbox, now=NOW, assistant=helper)
    assert outbox == [] and helper.scored == []                     # not urgent: queued, and not scored yet
    assert "why q" not in Path(paths[1]).read_text()
    morning = datetime(2026, 10, 22, 13, 0, tzinfo=timezone.utc)    # 9 AM Eastern
    run(*paths, session=object(), sender=outbox, now=morning, assistant=helper)
    assert "Daily digest" in outbox[0][0] and "Fit 90" in outbox[0][2] and len(outbox[0][3]) == 1
    assert helper.scored == ["q"]                                   # scored once, when it was emailed
    saved = Path(paths[1]).read_text()
    assert "fits" not in json.loads(saved) and "COVER LETTER" not in saved and "why q" not in saved and '"fit"' not in saved


def test_weekly_summary_counts_the_week_and_lists_what_is_waiting(site, paths, monkeypatch):
    from jobscanner import tracker
    Path(paths[0]).write_text(CONFIG + "weekly_summary: {day: sunday, hour: 18}\nfollow_up_after_days: 10\n")
    site["cs"] = [posting("a", company="NVIDIA", title="Security Intern A"), posting("b", company="NVIDIA", title="Security Intern B")]
    outbox = Outbox()
    go(paths, outbox)                                               # Wednesday night ET: the two jobs are emailed
    sunday = datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc)     # Sunday 7 PM Eastern
    marks = tracker.Marks(loaded=True, jobs={"jobright.ai/jobs/info/a": "applied"}, rows=[
        {"status": "applied", "id": "jobright.ai/jobs/info/a", "company": "NVIDIA", "title": "Security Intern A",
         "link": "https://jobright.ai/jobs/info/a", "marked": sunday - timedelta(days=1)},
        {"status": "applied", "id": "old", "company": "Cisco", "title": "Network Intern", "link": "https://c/1",
         "marked": sunday - timedelta(days=12)},
        {"status": "interview", "id": "i", "company": "Okta", "title": "IAM Intern", "link": "javascript:alert(1)",
         "marked": sunday - timedelta(days=2)}])
    monkeypatch.setattr(tracker, "load", lambda session: marks)
    go(paths, outbox, now=sunday)
    subject, text, html = outbox[1]
    assert subject == "📊 Your week: 2 matches, 1 applied"
    assert "Applied: 1" in text and "Interviews: 1" in text
    assert "Security Intern B" in text and "Security Intern A" not in text.split("Still waiting on you:")[1].split("Worth")[0]
    assert "Cisco: Network Intern (applied 12 days ago)" in text and "javascript:" not in html
    go(paths, outbox, now=sunday + timedelta(minutes=30))           # once a week, not once a run
    assert len(outbox) == 2


def test_weekly_summary_works_without_the_tracker(site, paths):
    Path(paths[0]).write_text(CONFIG + "weekly_summary: {day: sunday, hour: 18}\n")
    outbox = Outbox()
    go(paths, outbox, now=datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc))
    assert outbox[0][0] == "📊 Your week: 0 matches" and "set up the tracker" in outbox[0][2]
