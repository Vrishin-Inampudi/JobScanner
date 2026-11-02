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

from . import emailer, sites, source, tracker
from .config import load_config
from .filters import Match, evaluate, rank
from .state import State

log = logging.getLogger("jobscanner")

TEST_WINDOW_HOURS = 24
CATCH_UP_SLACK = timedelta(hours=3)    # look back past the last run: jobright sometimes adds jobs with earlier timestamps
MAX_CATCH_UP = timedelta(hours=24)
DUE_SLACK = timedelta(minutes=10)      # cron jitter: a run at 4h55m still counts as 5h
FETCH_WORKERS = 12


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
    sender: Callable[[str, str, str], None] = emailer.send,
    now: datetime | None = None,
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
    results = _fetch_all(session, specs, available, since, now)
    marks = tracker.Marks() if preview else tracker.load(session)

    fetched: dict[str, list[source.Posting]] = {}
    wanted: dict[str, list[source.Posting]] = {}
    failures: list[str] = []
    matches: list[Match] = []
    batch_keys: set[str] = set()

    for spec in specs:
        postings = results[spec.key]
        if isinstance(postings, Exception):
            failures.append(f"list '{spec.key}': {postings}")
            continue
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
        window = sites.RECENT if spec.is_site else source.GITHUB_LOOKBACK if spec.repo else now - since
        log.info("list '%s': %d posted in the last %.1fh, %d new, %d qualify",
                 spec.key, len(postings), window.total_seconds() / 3600, len(new), kept)

    matches = rank(matches)
    # With a daily digest, only urgent matches are emailed now; the rest wait for the digest.
    digest_on = cfg.digest_hour is not None and not test
    urgent = [m for m in matches if not digest_on or is_urgent(m, cfg)]
    later = [m for m in matches if m not in urgent]

    def deliver(subject: str, text: str, html_body: str, what: str) -> None:
        if dry_run:
            print(f"Subject: {subject}\n\n{text}")
        else:
            sender(subject, text, html_body)  # raises -> state not saved, retried next run
            log.info("sent %s", what)
        state.last_email_at = now

    emailed_now = False
    if urgent:
        subject, text, html_body = emailer.render(urgent, now=now, check_every=cfg.check_every_minutes, links=tracker.links)
        deliver(f"[Test] {subject}" if test else subject, text, html_body, f"{len(urgent)} postings")
        emailed_now = True
    # The digest covers what was queued before this run; this run's go in the next one.
    today = now.astimezone(emailer.DISPLAY_TZ)
    if digest_on and today.hour >= cfg.digest_hour and state.last_digest_on != today.date().isoformat():
        queued = rank([m for p in state.pending if not marks.hides(p) and (m := evaluate(p, cfg.criteria))])
        if queued:
            subject, text, html_body = emailer.render(queued, now=now, check_every=cfg.check_every_minutes,
                                                      links=tracker.links, digest=True)
            deliver(subject, text, html_body, f"the daily digest of {len(queued)} postings")
            emailed_now = True
        state.pending, state.last_digest_on = [], today.date().isoformat()
    if later:
        state.pending += [m.posting for m in later]
        log.info("queued %d less-urgent postings for the %02d:00 digest", len(later), cfg.digest_hour)

    if not emailed_now and cfg.empty_email_every_hours and not failures and not test:
        # Only claim "nothing posted" when every list was actually checked.
        every = timedelta(hours=cfg.empty_email_every_hours)
        if state.last_email_at is None:
            state.last_email_at = now  # start the clock
            log.info("nothing new qualifies; quiet-email clock started")
        elif now - state.last_email_at >= every - DUE_SLACK:
            quiet_for = (now - state.last_email_at).total_seconds() / 3600
            scanned = sum(len(p) for p in fetched.values())
            subject, text, html_body = emailer.render_empty(quiet_for, scanned, len(fetched), now=now,
                                                            check_every=cfg.check_every_minutes)
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

    for f in failures:
        log.error(f)
    return 1 if failures else 0


def is_urgent(m: Match, cfg) -> bool:
    """Worth an email right away: a top company, or pay at or above your bar."""
    bar = cfg.criteria.min_hourly_pay
    return m.priority is not None or (bar is not None and m.hourly is not None and m.hourly >= bar)


def _fetch_all(session, specs, available, since, now) -> dict[str, list[source.Posting] | Exception]:
    """Fetch every list at once (most of a run is waiting on ~80 sites); errors are returned, not raised."""
    def one(spec):
        try:
            if spec.is_site:
                return sites.fetch_site(session, spec, now=now)
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
