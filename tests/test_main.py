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
    assert "4 new internships worth a look" in html
    assert "★ Top company" in html and "$40/hr" in html
    assert "Wednesday, Oct 21 · 11:05 PM ET" in html


def test_pretty_location():
    assert pretty_location("Multi Location\nLehi, UT, United States\nSan Jose, CA, United States") == "Lehi, UT / San Jose, CA"
    assert pretty_location("Cambridge, MA") == "Cambridge, MA"
    assert pretty_location("Menlo Park, CA; New York, NY") == "Menlo Park, CA / New York, NY"
    assert pretty_location("") == ""


def test_send_requires_secrets():
    with pytest.raises(EmailConfigError):
        send("s", "t", "h", env={})
