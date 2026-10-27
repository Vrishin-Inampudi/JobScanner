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
    return _posting(row_id=row_id, posted_at=NOW - timedelta(minutes=minutes_ago), **kw)


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
                 apply_url="https://jobs.example.com/g1", source="SimplifyJobs", posted_at=None, date="Oct 26"),
        _posting(list_key="gh", row_id="g2", company="Acme", title="Security Intern SF",
                 location="SF", apply_url="https://jobs.example.com/g2", source="SimplifyJobs", posted_at=None, date="Oct 26"),
        _posting(list_key="gh", row_id="g3", company="Acme", title="Security Intern TX",
                 apply_url="https://jobs.example.com/g3", source="SimplifyJobs", posted_at=None, date="Oct 26"),
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
