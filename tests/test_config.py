from pathlib import Path

import pytest

from jobscanner.config import ListSpec, load_config

ROOT = Path(__file__).parent.parent


def test_repo_config_loads():
    cfg = load_config(ROOT / "config.yaml")
    assert [l.key for l in cfg.lists] == ["cs", "swe", "eng"]
    assert cfg.criteria.min_hourly_pay == 35
    assert cfg.criteria.locations == ["CA", "MA", "NY", "CT"]
    assert "NVIDIA" in cfg.criteria.priority_companies
    assert cfg.max_age_hours == 2
    assert cfg.empty_email_every_hours == 5
    assert cfg.quiet_hours == (0, 4)


def test_list_spec_wants():
    s = ListSpec("eng", title_include=["Network Engineer"], title_exclude=["electrical"])
    assert s.wants("Network Engineering Intern, Summer 2027")
    assert not s.wants("Electrical Network Engineer Intern")
    assert not s.wants("Mechanical Intern")
    assert ListSpec("cs").wants("anything")


def test_bad_match_value(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("lists: [cs]\nmatch: some\n")
    with pytest.raises(ValueError, match="match"):
        load_config(p)
