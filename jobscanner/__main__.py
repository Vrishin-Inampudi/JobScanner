"""Check intern-list.com and email new qualifying internships.

Usage: python -m jobscanner [--config config.yaml] [--state state/seen.json] [--dry-run] [--preview] [--test] [--show-lists]
"""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import ai, emailer, notify, sites, source, tracker
from .config import load_config
from .filters import Match, evaluate, rank
from .state import State

log = logging.getLogger("jobscanner")

TEST_WINDOW_HOURS = 24
CATCH_UP_SLACK = timedelta(hours=3)    # look back past the last run: jobright sometimes adds jobs with earlier timestamps
MAX_CATCH_UP = timedelta(hours=24)
DUE_SLACK = timedelta(minutes=10)      # cron jitter: a run at 4h55m still counts as 5h
FETCH_WORKERS = 12
TROUBLE_AFTER = timedelta(hours=24)    # a company site failing this long is mentioned in your emails


def in_quiet_hours(now: datetime, quiet: tuple[int, int] | None) -> bool:
    if not quiet:
        return False
    start, end = quiet
    hour = now.astimezone(emailer.DISPLAY_TZ).hour
    return start <= hour < end if start <= end else hour >= start or hour < end


def run(
    config_path: str,
    state_path: str,
    dry_run: bool = False,
    preview: bool = False,
    test: bool = False,
    session=None,
    sender: Callable[..., None] = emailer.send,
    now: datetime | None = None,
    assistant: ai.Assistant | None | str = "auto",
) -> int:
    cfg = load_config(config_path)
    now = now or datetime.now(timezone.utc)
    if not test and in_quiet_hours(now, cfg.quiet_hours):
        log.info("quiet hours (%02d:00-%02d:00 ET): skipping this run", *cfg.quiet_hours)
        return 0
    state = State.load(state_path)
    session = session or source.new_session()
    # A test email shows what would match over the last day, ignoring and not touching history.
    preview = preview or test
    window = max(cfg.max_age_hours, TEST_WINDOW_HOURS) if test else cfg.max_age_hours
    since = now - timedelta(hours=window)
    if not preview and state.last_run_at:
        # Cover everything since the last full run (quiet hours, skipped runs).
        since = max(min(since, state.last_run_at - CATCH_UP_SLACK), now - MAX_CATCH_UP)
    available = source.fetch_list_map(session) if any(not (s.repo or s.is_site) for s in cfg.lists) else {}
    # intern-list first: when a job is on both, its copy has pay and an exact posting time.
    specs = sorted(cfg.lists, key=lambda s: (bool(s.repo), s.is_site))
    results = _fetch_all(session, specs, available, since, now, _details(state, preview))
    marks = tracker.Marks() if preview else tracker.load(session)

    fetched: dict[str, list[source.Posting]] = {}
    wanted: dict[str, list[source.Posting]] = {}
    failures: list[str] = []
    site_trouble: list[str] = []  # one company's site being down shouldn't fail the run
    matches: list[Match] = []
    batch_keys: set[str] = set()

    for spec in specs:
        postings = results[spec.key]
        if isinstance(postings, Exception):
            (site_trouble if spec.is_site else failures).append(f"list '{spec.key}': {postings}")
            if spec.is_site:
                state.trouble.setdefault(spec.key, now.isoformat())
            continue
        state.trouble.pop(spec.key, None)
        fetched[spec.key] = postings
        wanted[spec.key] = [p for p in postings if spec.wants(p.title)]

        # Career sites only say which day a job went up, so on the first check of a site
        # everything there is "new": record it without emailing.
        if spec.is_site and spec.key not in state.lists and not preview:
            log.info("list '%s': first check, recording %d current jobs without emailing", spec.key, len(wanted[spec.key]))
            continue
        new = wanted[spec.key] if preview else state.diff(spec.key, wanted[spec.key])
        kept = 0
        for p in new:
            already_sent = not preview and state.was_emailed(p)
            if already_sent or marks.hides(p) or any(k in batch_keys for k in p.job_keys):
                continue
            m = evaluate(p, cfg.criteria)
            if m:
                matches.append(m)
                batch_keys.update(p.job_keys)
                kept += 1
        span = sites.RECENT if spec.is_site else source.GITHUB_LOOKBACK if spec.repo else now - since
        log.info("list '%s': %d posted in the last %.1fh, %d new, %d qualify",
                 spec.key, len(postings), span.total_seconds() / 3600, len(new), kept)

    matches = rank(matches)
    state.trouble = {k: v for k, v in state.trouble.items() if any(s.key == k for s in specs)}  # sites since removed
    look = dict(now=now, check_every=cfg.check_every_minutes, notes=_trouble_notes(state.trouble, specs, now),
                sources=_sources(specs), dashboard=tracker.dashboard_url() or "")
    today = now.astimezone(emailer.DISPLAY_TZ)
    if matches:
        if assistant == "auto":
            assistant = ai.from_env(cfg.ai)
        _assess(matches, assistant, cfg.ai, state, today.date().isoformat())
    # With a daily digest, only urgent matches are emailed now; the rest wait for the digest.
    digest_on = cfg.digest_hour is not None and not test
    urgent = [m for m in matches if not digest_on or is_urgent(m, cfg)]
    later = [m for m in matches if m not in urgent]

    def deliver(subject: str, text: str, html_body: str, what: str, drafts=()) -> None:
        if dry_run:
            print(f"Subject: {subject}\n\n{text}")
        else:
            # raises -> state not saved, retried next run
            sender(subject, text, html_body, attachments=drafts) if drafts else sender(subject, text, html_body)
            log.info("sent %s", what)
        state.last_email_at = now

    emailed_now = False
    if urgent:
        subject, text, html_body = emailer.render(urgent, links=tracker.links, **look)
        deliver(f"[Test] {subject}" if test else subject, text, html_body, f"{len(urgent)} postings",
                emailer.attachments(urgent))
        emailed_now = True
        if not (dry_run or test):
            notify.send(session, urgent)   # after the email: if that failed, this run is retried
    # The digest covers what was queued before this run; this run's go in the next one.
    if digest_on and today.hour >= cfg.digest_hour and state.last_digest_on != today.date().isoformat():
        queued = rank([m for p in state.pending if not marks.hides(p) and (m := evaluate(p, cfg.criteria))])
        for m in queued:   # scored when they were found
            saved = state.fits.get(m.posting.job_keys[0]) or {}
            m.fit, m.fit_why, m.draft = saved.get("score"), saved.get("why", ""), saved.get("draft", "")
        if queued:
            subject, text, html_body = emailer.render(queued, links=tracker.links, digest=True, **look)
            deliver(subject, text, html_body, f"the daily digest of {len(queued)} postings", emailer.attachments(queued))
            emailed_now = True
        state.pending, state.fits, state.last_digest_on = [], {}, today.date().isoformat()
    if later:
        state.pending += [m.posting for m in later]
        state.fits.update({m.posting.job_keys[0]: {"score": m.fit, "why": m.fit_why, "draft": m.draft}
                           for m in later if m.fit is not None})
        log.info("queued %d less-urgent postings for the %02d:00 digest", len(later), cfg.digest_hour)

    if not preview:
        state.sent += [{"at": now.isoformat(), "id": m.posting.job_keys[0], "company": m.posting.company,
                        "title": m.posting.title, "url": m.posting.apply_url, "fit": m.fit} for m in matches]
        if not dry_run:
            tracker.push(session, matches, now)   # the dashboard gets every match, urgent or not
        day, hour = cfg.weekly_summary or (None, None)
        if today.weekday() == day and today.hour >= hour and state.last_weekly_on != today.date().isoformat():
            subject, text, html_body = emailer.render_weekly(
                *_week(state.sent, marks, now, cfg.follow_up_after_days), follow_up_days=cfg.follow_up_after_days, **look)
            deliver(subject, text, html_body, "the weekly summary")
            emailed_now, state.last_weekly_on = True, today.date().isoformat()

    if not emailed_now and cfg.empty_email_every_hours and not failures and not test:
        # Only claim "nothing posted" when every list was actually checked.
        every = timedelta(hours=cfg.empty_email_every_hours)
        if state.last_email_at is None:
            state.last_email_at = now  # start the clock
            log.info("nothing new qualifies; quiet-email clock started")
        elif now - state.last_email_at >= every - DUE_SLACK:
            quiet_for = (now - state.last_email_at).total_seconds() / 3600
            scanned = sum(len(p) for p in fetched.values())
            subject, text, html_body = emailer.render_empty(quiet_for, scanned, len(fetched), **look)
            deliver(subject, text, html_body, f"the 'nothing has been posted' email after {quiet_for:.1f}h")
        else:
            log.info("nothing new qualifies; next 'nothing posted' email after %gh of quiet", cfg.empty_email_every_hours)
    elif not emailed_now:
        log.info("nothing new qualifies; no email")

    if dry_run or test:
        log.info("%s: state not saved", "test" if test else "dry run")
    else:
        # Only jobs passing a list's title filter are remembered; the rest can never be emailed.
        for key, postings in wanted.items():
            state.mark_seen(key, postings)
        state.mark_emailed([m.posting for m in matches])  # queued for the digest counts as handled
        if not failures:
            state.last_run_at = now
        state.save(state_path)

    for f in site_trouble:
        log.warning("%s (checking again next run)", f)
    for f in failures:
        log.error(f)
    return 1 if failures else 0


def _assess(matches: list[Match], assistant, cfg: ai.AIConfig, state: State, today: str) -> None:
    """Score each match against your resume, and draft a cover letter for the best few per day."""
    if assistant is None:
        return
    for m in matches[: cfg.max_scored_per_run]:   # ranked best-first, so the cap drops the least important
        fit = assistant.score(m.posting)
        if fit:
            m.fit, m.fit_why = fit.score, fit.why
    scored = sum(m.fit is not None for m in matches)
    used = state.drafts.get("count", 0) if state.drafts.get("on") == today else 0
    best = sorted((m for m in matches if m.fit is not None and m.fit >= cfg.draft_min_score), key=lambda m: -m.fit)
    written = 0
    for m in best[: max(0, cfg.max_drafts_per_day - used)]:
        m.draft = assistant.draft(m.posting) or ""
        written += bool(m.draft)
    state.drafts = {"on": today, "count": used + written}
    log.info("AI scored %d of %d matches and wrote %d drafts", scored, len(matches), written)


def _week(sent: list[dict], marks: tracker.Marks, now: datetime, follow_up_days: int):
    """(matches found, still waiting, worth a follow-up, your counts) for the weekly summary."""
    since = now - timedelta(days=7)
    recent = [s for s in sent if datetime.fromisoformat(s["at"]) >= since]
    waiting = [s for s in recent if s["id"] not in marks.jobs]
    waiting.sort(key=lambda s: (s.get("fit") is None, -(s.get("fit") or 0), s["at"]))
    if not marks.loaded:   # no tracker: we can't know what you've applied to
        return len(recent), waiting, [], None
    counts = {status: sum(1 for r in marks.with_status(status) if r["marked"] and r["marked"] >= since)
              for status in (tracker.APPLIED, tracker.INTERVIEW, tracker.OFFER, tracker.REJECTED)}
    stale = [r for r in marks.with_status(tracker.APPLIED)
             if r["marked"] and now - r["marked"] >= timedelta(days=follow_up_days)]
    stale.sort(key=lambda r: r["marked"])
    follow_up = [{"company": r["company"], "title": r["title"], "days": (now - r["marked"]).days,
                  "url": r["link"] if r["link"].startswith("https://") else "#"} for r in stale]
    return len(recent), waiting, follow_up, counts


def is_urgent(m: Match, cfg) -> bool:
    """Worth an email right away: a top company, or pay at or above your bar."""
    bar = cfg.criteria.min_hourly_pay
    return m.priority is not None or (bar is not None and m.hourly is not None and m.hourly >= bar)


def _details(state: State, preview: bool) -> Callable:
    """For a site, which jobs still need their own page read (description, exact locations).

    Jobs seen on an earlier run are never emailed again, so their pages aren't downloaded again;
    on the first check of a site nothing is emailed, so none are.
    """
    def for_site(spec) -> Callable[[str], bool]:
        if preview:
            return lambda url: True
        seen = state.lists.get(spec.key)
        if seen is None:
            return lambda url: False
        return lambda url: source.url_key(url) not in seen
    return for_site


def _sources(specs) -> str:
    """'intern-list.com, the SimplifyJobs GitHub list and 106 company career sites'."""
    parts = ["intern-list.com"] if any(not (s.repo or s.is_site) for s in specs) else []
    parts += [f"the {s.repo.split('/')[0]} GitHub list" for s in specs if s.repo]
    n = sum(1 for s in specs if s.is_site)
    if n:
        parts.append(f"{n} company career site{'s' if n != 1 else ''}")
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else ""


def _trouble_notes(trouble: dict[str, str], specs, now: datetime) -> list[str]:
    """One line per company site that has failed on every check for a day or more."""
    names = {s.key: s.company for s in specs if s.is_site}
    notes = []
    for key, since in sorted(trouble.items()):
        began = datetime.fromisoformat(since)
        if key in names and now - began >= TROUBLE_AFTER:
            day = began.astimezone(emailer.DISPLAY_TZ)
            notes.append(f"{names[key]}'s career site hasn't loaded since {day:%b} {day.day}. "
                         f"Its address in config.yaml may need updating.")
    return notes


def _fetch_all(session, specs, available, since, now, details=None) -> dict[str, list[source.Posting] | Exception]:
    """Fetch every list at once (most of a run is waiting on ~100 sites); errors are returned, not raised."""
    def one(spec):
        try:
            if spec.is_site:
                return sites.fetch_site(session, spec, now=now, **({"details": details(spec)} if details else {}))
            if spec.repo:
                return source.fetch_github_list(session, spec.repo, spec.key, now=now)
            if spec.key not in available:
                return source.SourceError(f"unknown list (available: {', '.join(sorted(available))})")
            return source.fetch_jobs(session, available[spec.key], spec.key, since, now=now)
        except Exception as e:  # keep going so other lists still get checked
            return e
    with ThreadPoolExecutor(FETCH_WORKERS) as pool:
        return dict(zip([s.key for s in specs], pool.map(one, specs)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobscanner", description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--state", default="state/seen.json")
    ap.add_argument("--dry-run", action="store_true", help="print the email instead of sending; don't save state")
    ap.add_argument("--preview", action="store_true", help="dry run that ignores what was already seen or emailed")
    ap.add_argument("--test", action="store_true",
                    help=f"send a [Test] email of matches from the last {TEST_WINDOW_HOURS}h; doesn't touch state")
    ap.add_argument("--show-lists", action="store_true", help="print the list keys intern-list.com offers")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.show_lists:
        for key, category in sorted(source.fetch_list_map(source.new_session()).items()):
            print(f"{key:8} {category}")
        return 0
    try:
        return run(args.config, args.state, dry_run=args.dry_run or args.preview, preview=args.preview, test=args.test)
    except Exception as e:
        log.error("%s: %s", type(e).__name__, e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
