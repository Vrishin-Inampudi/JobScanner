# Internship Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hourly GitHub Actions job that emails every newly posted, qualifying intern-list.com internship, ranked, driven by `config.yaml`.

**Architecture:** Small Python package `jobscanner/`: `source` fetches the site's list map and each Airtable shared view into `Posting` objects; `filters` parses pay/size/location and decides + ranks; `state` tracks seen ids per list and emailed URLs; `emailer` renders and sends via Gmail SMTP; `main` wires it with `--dry-run`. Workflow commits `state/seen.json` back.

**Tech Stack:** Python 3.12, requests, PyYAML, pytest; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-21-internship-alerts-design.md`

## Global Constraints

- Only `/us/` entries of intern-list.com are used; `short-link` = config `key`.
- `/yr` salaries ÷ 2080; compare on top of range; `N/A`/`Unpaid`/unparseable → `None`.
- Every fetched row is recorded seen; a list key without state is baselined silently.
- State saved only after successful send (or when nothing to send). `--dry-run` never sends or saves.
- Fetch/parse failure, empty list, or missing expected columns → non-zero exit.
- Secrets: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_TO`. No personal data in repo.

## Review Focus

- Company names with suffixes/casing ("NVIDIA Corporation", "Palo Alto Networks, Inc.") must match priority list; but "Meta" must not match "Metaview" → word-boundary match (test in Task 2).
- Location "Remote, United States" or "United States" must not match a state; "New York, NY" and "Hartford, CT, United States" must (Task 2).
- Salary like "$22.60-$47.95 /hr", "$52900-$108000 /yr", "$40 /hr", "Unpaid" (Task 2).
- Same job in two lists in one hour appears once in the email (Task 3).
- Missing secrets with pending email → clear error, state not saved (Task 4).

---

### Task 1: Source (site map + Airtable view)

**Files:** Create `jobscanner/__init__.py`, `jobscanner/source.py`; Test `tests/test_source.py`, fixtures `tests/fixtures/site.html`, `tests/fixtures/view.json`.

**Interfaces — Produces:**
- `@dataclass(frozen=True) Posting(list_key, row_id, title, company, location, salary, size, date, work_model, apply_url)`; `uid` property = `f"{list_key}:{row_id}"`.
- `parse_list_map(html: str) -> dict[str, str]` key → embed URL (no query string), US only.
- `parse_view(payload: dict, list_key: str) -> list[Posting]`; raises `SourceError` on missing columns/empty rows.
- `fetch_list_map(session) -> dict[str,str]`, `fetch_view(session, embed_url, list_key) -> list[Posting]`.

- [ ] Tests: `parse_list_map` on fixture returns `cs → https://airtable.com/embed/appVWWidEOaCW7Biy/shrC0SNKnwMV3Fem9` and excludes `/ca/`; `parse_view` resolves select names (Company Size "10000+") and Apply url; missing "Company" column raises `SourceError`.
- [ ] Implement per spec's 4-step fetch; run `pytest tests/test_source.py`; commit.

### Task 2: Filters + ranking

**Files:** Create `jobscanner/filters.py`; Test `tests/test_filters.py`.

**Interfaces — Produces:**
- `parse_hourly(s: str) -> float | None`; `parse_size(s: str | None) -> int` (lower bound, "10000+" → 10000, None → 0).
- `location_match(loc: str, states: list[str]) -> str | None` returns matched code.
- `priority_match(company: str, names: list[str]) -> str | None`.
- `@dataclass Criteria(min_hourly_pay, locations, large_company_min_size, priority_companies, match="any")`.
- `@dataclass Match(posting, hourly, state, priority, size, reasons: list[str])`.
- `evaluate(p: Posting, c: Criteria) -> Match | None`; `rank(ms: list[Match]) -> list[Match]` per spec order.

- [ ] Tests: values from Review Focus; `parse_hourly("$52900-$108000 /yr") == 108000/2080`; `evaluate` any vs all (all + N/A pay → None); rank puts priority first, then pay desc (None last), then location, size, date desc.
- [ ] Implement; run `pytest tests/test_filters.py`; commit.

### Task 3: Config + state

**Files:** Create `jobscanner/config.py`, `jobscanner/state.py`, `config.yaml`; Test `tests/test_state.py`, `tests/test_config.py`.

**Interfaces — Produces:**
- `@dataclass ListSpec(key, title_include=[], title_exclude=[])`, `ListSpec.wants(title) -> bool`; `@dataclass Config(lists, criteria)`; `load_config(path) -> Config`.
- `State.load(path) -> State` (missing file → empty); `State.diff(list_key, postings) -> (new: list[Posting], baselined: bool)`; `State.mark_seen(list_key, postings)`; `State.mark_emailed(urls)`; `State.was_emailed(url) -> bool`; `State.save(path)`. JSON: `{"lists": {key: {row_id: "YYYY-MM-DD last seen"}}, "emailed_urls": {url: date}}`; prune entries not seen for 30 days on save.

- [ ] Tests: unknown list → baselined True, new empty; second diff returns only new ids; emailed URL dedup; `wants` include/exclude case-insensitive; load_config on repo `config.yaml`.
- [ ] Implement; commit.

### Task 4: Email + main + workflow + docs

**Files:** Create `jobscanner/emailer.py`, `jobscanner/__main__.py`, `.github/workflows/alerts.yml`, `requirements.txt`, `README.md`, `LICENSE`, `.gitignore`; Test `tests/test_emailer.py`, `tests/test_main.py`.

**Interfaces — Consumes:** all above. **Produces:**
- `render(matches: list[Match]) -> (subject, text, html)`; `send(subject, text, html, env=os.environ)` raises `EmailConfigError` if secrets missing.
- `run(config_path, state_path, dry_run, session, sender) -> int` exit code.

- [ ] Tests: subject `"🛡️ 3 new internships — NVIDIA, CrowdStrike, Booz Allen…"` shape; html escapes titles; `run` with fake session: first run baselines & sends nothing; second run with one new qualifying row sends once and saves; duplicate apply URL across lists sent once; sender raising → state unchanged.
- [ ] Workflow: cron `0 * * * *` + dispatch, `permissions: contents: write`, concurrency group, setup-python 3.12, run `python -m jobscanner`, commit `state/seen.json` if changed.
- [ ] Live dry-run against real site; commit.
