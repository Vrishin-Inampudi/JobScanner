from jobscanner.state import State

from .test_filters import posting as _posting


def posting(row_id="rec1", **kw):
    """A distinct job per row_id unless company/title/location are given explicitly."""
    kw.setdefault("apply_url", f"https://jobright.ai/jobs/info/{row_id}")
    kw.setdefault("title", f"Intern {row_id}")
    return _posting(row_id=row_id, **kw)


def test_unknown_list_has_nothing_seen(tmp_path):
    s = State.load(tmp_path / "missing.json")
    assert [p.row_id for p in s.diff("cs", [posting(row_id="a")])] == ["a"]


def test_diff_returns_only_new_rows(tmp_path):
    s = State()
    s.mark_seen("cs", [posting(row_id="a")], today="2026-10-21")
    path = tmp_path / "seen.json"
    s.save(path, today="2026-10-21")

    s = State.load(path)
    new = s.diff("cs", [posting(row_id="a"), posting(row_id="b")])
    assert [p.row_id for p in new] == ["b"]


def test_first_seen_date_is_kept():
    s = State()
    s.mark_seen("cs", [posting(row_id="a")], today="2026-10-01")
    s.mark_seen("cs", [posting(row_id="a")], today="2026-10-21")
    assert set(s.lists["cs"].values()) == {"2026-10-01"}


def test_vanished_rows_forgotten_after_90_days():
    s = State()
    s.mark_seen("cs", [posting(row_id="old")], today="2026-06-01")
    s.mark_seen("cs", [posting(row_id="recent")], today="2026-10-01")
    s.mark_seen("cs", [], today="2026-10-21")
    assert set(s.lists["cs"].values()) == {"2026-10-01"}
    assert s.diff("cs", [posting(row_id="recent")]) == []
    assert "cs" in s.lists  # empty-ish list still counts as known, no re-baseline


def test_emailed():
    s = State()
    s.mark_emailed([posting(row_id="1")], today="2026-10-21")
    assert s.was_emailed(posting(row_id="1"))
    assert not s.was_emailed(posting(row_id="2"))


def test_table_rebuild_with_new_row_ids_is_not_new():
    s = State()
    s.mark_seen("cs", [posting(row_id="recOLD", apply_url="https://jobright.ai/jobs/info/abc?utm_campaign=Cyber")])
    rebuilt = [posting(row_id="recNEW", apply_url="https://jobright.ai/jobs/info/abc?utm_campaign=Cyber")]
    assert s.diff("cs", rebuilt) == []


def test_old_format_state_is_ignored(tmp_path):
    p = tmp_path / "seen.json"
    p.write_text('{"lists": {"cs": {"recOLD": "2026-10-21"}}, "emailed_urls": {}}')  # v1: row ids
    assert State.load(p).lists == {}


def test_same_job_with_new_link_is_not_new():
    s = State()
    s.mark_seen("cs", [posting(row_id="old", company="Adobe", title="Security Intern", location="San Jose, CA")])
    relinked = posting(row_id="new", company="Adobe", title="Security Intern", location="San Jose, CA")
    assert s.diff("cs", [relinked]) == []


def test_relinked_job_already_emailed_from_other_list():
    s = State()
    a = posting(row_id="a", company="Adobe", title="Security Intern")
    s.mark_emailed([a])
    assert s.was_emailed(posting(row_id="b", company="Adobe", title="Security Intern"))
    assert not s.was_emailed(posting(row_id="c", company="Adobe", title="Other Intern"))


def test_same_job_relisted_with_merged_locations_is_not_new():
    s = State()
    s.mark_seen("cs", [posting(row_id="old", company="Adobe", title="Security Intern", location="San Jose, CA, United States")])
    merged = posting(row_id="new", company="Adobe", title="Security Intern",
                     location="Multi Location\nLehi, UT, United States\nSan Jose, CA, United States")
    assert s.diff("cs", [merged]) == []
