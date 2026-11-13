from pathlib import Path

import pytest

from jobscanner.config import ListSpec, load_config

ROOT = Path(__file__).parent.parent


def test_repo_config_loads():
    cfg = load_config(ROOT / "config.yaml")
    assert [l.key for l in cfg.lists][:4] == ["cs", "swe", "eng", "github"]
    sites = {l.key: l for l in cfg.lists if l.is_site}
    assert "site:nvidia" in sites and sites["site:cloudflare"].greenhouse == "cloudflare"
    assert sites["site:nvidia"].wants("Security Engineering Intern") and not sites["site:nvidia"].wants("Security Engineer")
    assert not sites["site:nvidia"].wants("Internal Network Engineer")
    assert cfg.criteria.skip_phd_only and cfg.criteria.graduation == (2028, 5)
    assert "SpaceX" in cfg.criteria.priority_companies and "Tesla" in cfg.criteria.priority_companies
    assert cfg.lists[3].repo == "SimplifyJobs/Summer2027-Internships"
    assert cfg.criteria.min_hourly_pay == 35
    assert cfg.criteria.locations[0] == "Bay Area" and "CT" in cfg.criteria.locations
    assert "NVIDIA" in cfg.criteria.priority_companies
    assert cfg.max_age_hours == 2
    assert cfg.empty_email_every_hours == 5
    assert cfg.quiet_hours == (0, 4)
    assert cfg.check_every_minutes == 30


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


def test_company_sites_and_eligibility(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("""
lists: [cs]
company_sites:
  title_require: [intern]
  sites:
    - {company: Palo Alto Networks, greenhouse: pan}
eligibility: {graduation: 2028-05}
""")
    cfg = load_config(p)
    site = cfg.lists[-1]
    assert site.key == "site:palo-alto-networks" and site.company == "Palo Alto Networks"
    assert cfg.criteria.graduation == (2028, 5) and not cfg.criteria.skip_phd_only
    for bad in ("company_sites: {sites: [{company: X}]}",
                "company_sites: {sites: [{company: X, greenhouse: a, workday: b}]}",
                "company_sites: {sites: [{company: X, careers: meta}]}",
                "eligibility: {graduation: 2028}"):
        p.write_text("lists: [cs]\n" + bad + "\n")
        with pytest.raises(ValueError):
            load_config(p)


def test_skills_section(tmp_path):
    from jobscanner.config import load_config
    p = tmp_path / "c.yaml"
    p.write_text("lists: [cs]\nskills:\n  have: [Python, [Security+, Sec+]]\n  lack: [Java]\n")
    s = load_config(p).criteria.skills
    assert [x.name for x in s.have] == ["Python", "Security+"] and [x.name for x in s.lack] == ["Java"]
    p.write_text("lists: [cs]\nskills:\n  have: [[]]\n")
    with pytest.raises(ValueError, match="skills"):
        load_config(p)


def test_title_words_match_from_the_start_of_a_word():
    spec = ListSpec(key="x", title_include=["cyber", "noc", "network"], title_exclude=["neural network", "power"])
    assert spec.wants("Cybersecurity Intern") and spec.wants("NOC Technician Intern") and spec.wants("Networking Intern")
    assert not spec.wants("Innocent Bystander Intern")          # 'noc' inside a word
    assert not spec.wants("Research Intern - Neural Network Training")
    assert not spec.wants("Network Intern, Powertrain")


def test_ai_and_weekly_settings(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("lists: [cs]\nweekly_summary: {day: Sunday, hour: 18}\nai: {draft_min_score: 80, score_model: claude-haiku-5-5}\n")
    loaded = load_config(cfg)
    assert loaded.weekly_summary == (6, 18) and loaded.ai.draft_min_score == 80 and loaded.ai.max_drafts_per_day == 3
    assert load_config(ROOT / "config.yaml").weekly_summary == (6, 18)
    for bad in ("weekly_summary: {day: someday}", "ai: {draft_min_score: high}", "ai: {modle: x}", "follow_up_after_days: 0"):
        cfg.write_text(f"lists: [cs]\n{bad}\n")
        with pytest.raises(ValueError):
            load_config(cfg)


def test_new_site_kinds(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("lists: [cs]\ncompany_sites:\n  sites:\n    - {company: ServiceNow, smartrecruiters: ServiceNow}\n"
                   "    - {company: Netflix, eightfold: 'https://explore.jobs.netflix.net/careers?domain=netflix.com'}\n")
    specs = [s for s in load_config(cfg).lists if s.is_site]
    assert [(s.key, bool(s.smartrecruiters), bool(s.eightfold)) for s in specs] == [
        ("site:servicenow", True, False), ("site:netflix", False, True)]


def test_projects_section(tmp_path):
    import pytest
    from jobscanner.config import load_config
    from jobscanner.projects import ProjectsConfig
    path = tmp_path / "c.yaml"
    base = "lists:\n  - key: cs\n"
    path.write_text(base)
    assert load_config(path).projects is None                                   # left out: off
    path.write_text(base + "projects: {day: Sunday, hour: 18}\n")
    assert load_config(path).projects == ProjectsConfig(day=6, hour=18, min_applied=3, lookback_days=120)
    path.write_text(base + "projects: {day: friday, hour: 9, min_applied: 5, lookback_days: 60}\n")
    assert load_config(path).projects == ProjectsConfig(day=4, hour=9, min_applied=5, lookback_days=60)
    for bad in ("{day: someday}", "{day: sunday, hour: 24}", "{day: sunday, min_applied: 0}", "{day: sunday, often: yes}",
                "weekly", "{day: sunday, lookback_days: 1.5}", "{day: sunday, hour: true}"):
        path.write_text(base + f"projects: {bad}\n")
        with pytest.raises(ValueError, match="'projects' must look like"):
            load_config(path)
