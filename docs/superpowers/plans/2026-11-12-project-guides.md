# Project Guides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once a week, email one advanced project guide (with PDF, Word and AI-helper files and a private link) built from the jobs the owner applied to.

**Architecture:** `projects.py` gathers applied-to jobs from the Sheet and makes two Claude requests (gaps, then guide) whose JSON is checked in code. `guide.py` turns the guide JSON into pictures, a PDF, a Word file and Markdown. The emailer renders a dark project email; the Apps Script web app stores the guide and serves it as a private page.

**Tech Stack:** Python 3.13, anthropic 2.8.0, Pillow 13.0.0, fpdf2 2.9.0, python-docx 1.2.0, Google Apps Script.

**Spec:** `docs/superpowers/specs/2026-11-12-project-guides-design.md`

## Global Constraints

- Nothing derived from the resume goes to the state file or the logs (the repo and its Actions logs are public). State keeps only `project = {on, tries, sent}`.
- The model never supplies a number shown in the email; counts come from validated posting IDs.
- No model output is rendered as markup: all text is escaped; pictures are drawn from a node/link description.
- Any failure in this feature is logged by type and skipped; alerts are never stopped.
- At most 3 attempts on the project day.
- Fonts: PDF uses built-in Helvetica (text reduced to Latin-1); pictures use Pillow's built-in font. No font files.

## Review Focus

- A guide whose text has characters outside Latin-1 (arrows, curly quotes): the PDF must still be written.
- A diagram naming a link to a node that doesn't exist, or 30 nodes: drawn without the bad parts, never an error.
- Applied-to jobs with no stored posting text: still counted, judged by title.
- The Sheet script not yet redeployed (old reply shape): feature skips with one log line.
- Guide JSON larger than one Sheet cell: split and rejoined intact, including pieces starting with `=`.

## Tasks

### Task 1: Tracker carries posting text and guides
**Files:** `jobscanner/tracker.py`, `tracker/Code.gs`, `tests/test_tracker.py`
**Produces:** `push` sends `posting` (first 6,000 chars); `load(session, env, full=False)` rows gain `posting`; `guides(session, env) -> list[dict] | None` (`{sent, title}`; None when unreachable or old script); `push_guide(session, guide_id, guide, env) -> bool`; `guide_url(guide_id, env) -> str | None`. Code.gs: `Posting` columns, `a=list&full=1`, `a=guides`, POST `a=guide`, `a=guide&id=` page.
- [x] Tests for each function with a fake session; Node stub run of Code.gs
- [x] Implement; run tests; commit

### Task 2: Gap finding and guide writing
**Files:** `jobscanner/ai.py`, `jobscanner/projects.py`, `tests/test_projects.py`
**Produces:** `Assistant.structured(what, instructions, content, schema, max_tokens) -> dict | None`; `ProjectsConfig(day, hour, min_applied=3, lookback_days=120)`; `applied_jobs(marks, now, lookback_days) -> list[dict]`; `find_gaps(assistant, jobs, past) -> dict | None`; `problems(guide) -> list[str]`; `write_guide(assistant, gaps) -> dict | None`; `check_links(session, guide)`; `build(session, assistant, jobs, past) -> Project | None` where `Project` has `guide, roles, total, also`; `SAMPLE` guide.
- [x] Tests: unknown IDs dropped, counts computed in code, step checks, one retry then None, dead link keeps title
- [x] Implement; run tests; commit

### Task 3: Files
**Files:** `jobscanner/guide.py`, `tests/test_guide.py`, `requirements.txt`
**Produces:** `picture(diagram, dark=True, width=640) -> bytes | None` (PNG); `pdf(project) -> bytes`; `docx(project) -> bytes`; `helper_md(project) -> str`; `files(project) -> list[tuple[str, bytes]]`.
- [x] Tests: PNG/PDF/DOCX open and contain steps; non-Latin-1 text; bad diagrams; Markdown has every step
- [x] Implement; run tests; commit

### Task 4: Email, config, state, scheduling, preview
**Files:** `jobscanner/emailer.py`, `config.py`, `state.py`, `__main__.py`, `config.yaml`, `.github/workflows/alerts.yml`, `.gitignore`, `README.md`, tests
**Produces:** `emailer.render_project(project, link, now) -> (subject, text, html, inline)`; `send(..., inline=())` and bytes attachments; `Config.projects`; `State.project`; weekly trigger in `run`; `--project-preview`.
- [x] Tests: email escapes text and shows computed counts; state holds only on/tries/sent; logs hold no title; 3-try cap; failure doesn't stop alerts
- [x] Implement; run all tests; render the sample and look at it; commit
