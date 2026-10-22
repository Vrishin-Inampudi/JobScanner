import json
from pathlib import Path

import pytest

from jobscanner import source
from jobscanner.__main__ import run
from jobscanner.emailer import EmailConfigError, render, send

from .test_filters import CRIT, posting
from jobscanner.filters import evaluate

FIX = Path(__file__).parent / "fixtures"

CONFIG = """
lists:
  - key: cs
  - key: swe
    title_include: [network]
match: any
criteria:
  min_hourly_pay: 35
  locations: [CA]
  large_company_min_size: 10000
  priority_companies: [NVIDIA]
"""


@pytest.fixture
def site(monkeypatch):
    """Fake intern-list.com: tests mutate ``rows[key]`` between runs."""
    rows = {"cs": [], "swe": []}
    monkeypatch.setattr(source, "fetch_list_map", lambda s: {"cs": "u-cs", "swe": "u-swe", "eng": "u-eng"})
    monkeypatch.setattr(source, "fetch_view", lambda s, url, key: list(rows[key]))
    return rows


@pytest.fixture
def paths(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG)
    return str(cfg), str(tmp_path / "state" / "seen.json")


class Outbox(list):
    def __call__(self, subject, text, html):
        self.append((subject, text, html))


def test_first_run_baselines_then_sends_only_new(site, paths):
    site["cs"] = [posting(row_id="old", salary="$50 /hr", apply_url="https://x/old")]
    outbox = Outbox()
    assert run(*paths, session=object(), sender=outbox) == 0
    assert outbox == []

    site["cs"].append(posting(row_id="new", company="NVIDIA", apply_url="https://x/new"))
    site["cs"].append(posting(row_id="boring", title="Help Desk Intern", apply_url="https://x/boring"))  # doesn't qualify
    assert run(*paths, session=object(), sender=outbox) == 0
    assert len(outbox) == 1 and "NVIDIA" in outbox[0][0] and "https://x/new" in outbox[0][1]
    assert "boring" not in outbox[0][1]

    assert run(*paths, session=object(), sender=outbox) == 0
    assert len(outbox) == 1  # nothing re-sent


def test_title_filter_and_cross_list_dedup(site, paths):
    outbox = Outbox()
    run(*paths, session=object(), sender=outbox)  # baseline both lists
    same = dict(company="NVIDIA", apply_url="https://x/dup")
    site["cs"] = [posting(row_id="c1", title="Network Security Intern", **same)]
    site["swe"] = [
        posting(list_key="swe", row_id="s1", title="Network Security Intern", **same),
        posting(list_key="swe", row_id="s2", title="Frontend Intern", company="NVIDIA", apply_url="https://x/fe"),
    ]
    run(*paths, session=object(), sender=outbox)
    assert len(outbox) == 1
    assert outbox[0][1].count("https://x/dup") == 1
    assert "https://x/fe" not in outbox[0][1]


def test_failed_send_keeps_state(site, paths):
    run(*paths, session=object(), sender=Outbox())
    site["cs"] = [posting(row_id="n", company="NVIDIA")]

    def boom(*a):
        raise EmailConfigError("no secrets")

    with pytest.raises(EmailConfigError):
        run(*paths, session=object(), sender=boom)
    outbox = Outbox()
    run(*paths, session=object(), sender=outbox)
    assert len(outbox) == 1  # retried


def test_unknown_list_fails_run_but_others_processed(site, paths, tmp_path):
    cfg, state = paths
    Path(cfg).write_text(CONFIG.replace("- key: swe", "- key: nope"))
    assert run(cfg, state, session=object(), sender=Outbox()) == 1
    assert "cs" in json.loads(Path(state).read_text())["lists"]


def test_dry_run_does_not_save(site, paths):
    cfg, state = paths
    run(cfg, state, dry_run=True, session=object(), sender=Outbox())
    assert not Path(state).exists()


def test_render_escapes_and_subject():
    ms = [evaluate(posting(row_id=str(i), company=c, title="<b>Intern</b>", salary="$40 /hr", apply_url=f"https://x/{i}"), CRIT)
          for i, c in enumerate(["NVIDIA", "CrowdStrike", "Booz Allen", "Apple"])]
    subject, text, html = render(ms)
    assert subject == "🎯 4 new internships — NVIDIA, CrowdStrike, Booz Allen…"
    assert "&lt;b&gt;Intern&lt;/b&gt;" in html and "<b>Intern</b>" not in html
    assert "Apply: https://x/0" in text


def test_send_requires_secrets():
    with pytest.raises(EmailConfigError):
        send("s", "t", "h", env={})


def test_preview_shows_existing_without_saving(site, paths, capsys):
    cfg, state = paths
    site["cs"] = [posting(row_id="a", company="NVIDIA", apply_url="https://x/a")]
    outbox = Outbox()
    run(cfg, state, dry_run=True, preview=True, session=object(), sender=outbox)
    assert outbox == [] and "https://x/a" in capsys.readouterr().out
    assert not Path(state).exists()


def test_only_latest_date(site, paths):
    cfg, state = paths
    Path(cfg).write_text(CONFIG + "only_latest_date: true\n")
    run(cfg, state, session=object(), sender=Outbox())  # baseline
    site["cs"] = [
        posting(row_id="today", title="Today Intern", company="NVIDIA", date="2026-10-21", apply_url="https://x/today"),
        posting(row_id="old", title="Old Intern", company="NVIDIA", date="2026-10-04", apply_url="https://x/old"),
    ]
    outbox = Outbox()
    run(cfg, state, session=object(), sender=outbox)
    assert len(outbox) == 1
    assert "https://x/today" in outbox[0][1] and "https://x/old" not in outbox[0][1]
