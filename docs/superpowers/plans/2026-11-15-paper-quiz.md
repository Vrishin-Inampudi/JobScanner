# Paper Quiz Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email one research paper three times a week with a quiz that is graded and logged in the owner's Sheet.

**Architecture:** `papers.py` picks a paper from three lanes (lab release, fixed reading list, fresh arXiv), reads it, and has Claude write a quiz that is checked in code. The Sheet web app stores each paper and quiz, serves the quiz and a scores page, and logs answers.

**Tech Stack:** Python 3.13, anthropic 2.8.0, defusedxml 0.7.1, Google Apps Script.

**Spec:** `docs/superpowers/specs/2026-11-15-paper-quiz-design.md`

## Global Constraints

- The resume is never sent in these requests (`Assistant.ask` carries no resume).
- Only fixed addresses are requested: lab sites, the reading list, arxiv.org, Hacker News. Nothing the model writes is fetched or linked.
- arXiv: at most one request every three seconds; papers are dated by arXiv id.
- Any failure is logged and skipped; alerts never stop. At most 3 attempts on a paper day.
- A quiz has exactly 6 multiple-choice and 2 open questions and no web addresses.

## Review Focus

- arXiv answering 429: the fresh lane still produces candidates from Hacker News alone.
- A lab page with no feed, seen for the first time: its links are recorded, not all sent as new.
- A paper whose site is down or whose PDF is too large: passed over, the next one is picked.
- A quiz that keeps failing for one paper: given up after 3 so the list doesn't stall.
- Paper or quiz text containing markup or formulas: shown as text on the pages, stored as text in the Sheet.

## Tasks

- [x] **Task 1: Picking and reading** — `jobscanner/papers.py`, `tests/test_papers.py`: `parse_feed`, `parse_links`, `lab_items`, `hn_arxiv`, `parse_arxiv`, `fresh`, `worthwhile`, `pick`, `read`, `READING_LIST`.
- [x] **Task 2: Quiz** — `ai.Assistant.ask`, `ai.without_resume`, `papers.make_quiz`, `papers.problems`, `SAMPLE_QUIZ`.
- [x] **Task 3: Sheet** — `tracker.push_paper`, `quiz_url`, `papers_url`; `tracker/Code.gs`: `Papers` and `Quiz log` tabs, `a=quiz`, `a=papers`, `logAnswer`.
- [x] **Task 4: Email, config, state, schedule, preview** — `emailer.render_paper`, `Config.papers`, `State.papers`, `_paper` in `__main__`, `--paper-preview`, README, `config.yaml`.
