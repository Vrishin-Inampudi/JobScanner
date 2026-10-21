from jobscanner.state import State

from .test_filters import posting as _posting


def posting(row_id="rec1", **kw):
    kw.setdefault("apply_url", f"https://jobright.ai/jobs/info/{row_id}")
    return _posting(row_id=row_id, **kw)


def test_unknown_list_is_baselined(tmp_path):
    s = State.load(tmp_path / "missing.json")
    new, baselined = s.diff("cs", [posting(row_id="a")])
    assert (new, baselined) == ([], True)


def test_diff_returns_only_new_rows(tmp_path):
    s = State()
    s.mark_seen("cs", [posting(row_id="a")], today="2026-10-21")
    path = tmp_path / "seen.json"
    s.save(path, today="2026-10-21")

    s = State.load(path)
    new, baselined = s.diff("cs", [posting(row_id="a"), posting(row_id="b")])
    assert [p.row_id for p in new] == ["b"] and baselined is False


def test_first_seen_date_is_kept():
    s = State()
    s.mark_seen("cs", [posting(row_id="a")], today="2026-10-01")
    s.mark_seen("cs", [posting(row_id="a")], today="2026-10-21")
    assert s.lists["cs"]["jobright.ai/jobs/info/a"] == "2026-10-01"


def test_vanished_rows_forgotten_after_90_days():
    s = State()
    s.mark_seen("cs", [posting(row_id="old"), posting(row_id="recent")], today="2026-06-01")
    s.lists["cs"]["jobright.ai/jobs/info/recent"] = "2026-10-01"
    s.mark_seen("cs", [], today="2026-10-21")
    assert s.lists["cs"] == {"jobright.ai/jobs/info/recent": "2026-10-01"}
    assert "cs" in s.lists  # empty-ish list still counts as known, no re-baseline


def test_emailed_keys():
    s = State()
    s.mark_emailed(["jobright.ai/jobs/info/1", ""], today="2026-10-21")
    assert s.was_emailed("jobright.ai/jobs/info/1")
    assert not s.was_emailed("")
    assert not s.was_emailed("jobright.ai/jobs/info/2")


def test_table_rebuild_with_new_row_ids_is_not_new():
    s = State()
    s.mark_seen("cs", [posting(row_id="recOLD", apply_url="https://jobright.ai/jobs/info/abc?utm_campaign=Cyber")])
    rebuilt = [posting(row_id="recNEW", apply_url="https://jobright.ai/jobs/info/abc?utm_campaign=Cyber")]
    assert s.diff("cs", rebuilt) == ([], False)


def test_old_format_state_is_rebaselined(tmp_path):
    p = tmp_path / "seen.json"
    p.write_text('{"lists": {"cs": {"recOLD": "2026-10-21"}}, "emailed_urls": {}}')  # v1: row ids
    new, baselined = State.load(p).diff("cs", [posting(row_id="recNEW")])
    assert (new, baselined) == ([], True)
