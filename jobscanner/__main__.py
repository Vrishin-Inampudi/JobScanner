"""Check intern-list.com and email new qualifying internships.

Usage: python -m jobscanner [--config config.yaml] [--state state/seen.json] [--dry-run] [--preview] [--test] [--show-lists]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import emailer, source
from .config import load_config
from .filters import Match, evaluate, rank
from .state import State

log = logging.getLogger("jobscanner")

TEST_WINDOW_HOURS = 24
CATCH_UP_SLACK = timedelta(hours=3)    # look back past the last run: jobright sometimes adds jobs with earlier timestamps
MAX_CATCH_UP = timedelta(hours=24)
DUE_SLACK = timedelta(minutes=10)      # cron jitter: a run at 4h55m still counts as 5h


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
    available = source.fetch_list_map(session)

    fetched: dict[str, list[source.Posting]] = {}
    failures: list[str] = []
    matches: list[Match] = []
    batch_keys: set[str] = set()

    for spec in cfg.lists:
        if spec.key not in available:
            failures.append(f"unknown list '{spec.key}' (available: {', '.join(sorted(available))})")
            continue
        try:
            postings = source.fetch_jobs(session, available[spec.key], spec.key, since, now=now)
        except Exception as e:  # keep going so other lists still get checked
            failures.append(f"list '{spec.key}': {e}")
            continue
        fetched[spec.key] = postings

        new = postings if preview else state.diff(spec.key, postings)
        kept = 0
        for p in new:
            already_sent = not preview and state.was_emailed(p)
            if not spec.wants(p.title) or already_sent or any(k in batch_keys for k in p.job_keys):
                continue
            m = evaluate(p, cfg.criteria)
            if m:
                matches.append(m)
                batch_keys.update(p.job_keys)
                kept += 1
        log.info("list '%s': %d posted in the last %.1fh, %d new, %d qualify",
                 spec.key, len(postings), (now - since).total_seconds() / 3600, len(new), kept)

    matches = rank(matches)
    if matches:
        subject, text, html_body = emailer.render(matches, now=now, check_every=cfg.check_every_minutes)
        if test:
            subject = f"[Test] {subject}"
        if dry_run:
            print(f"Subject: {subject}\n\n{text}")
        else:
            sender(subject, text, html_body)  # raises -> state not saved, retried next run
            log.info("emailed %d postings", len(matches))
        state.last_email_at = now
    elif cfg.empty_email_every_hours and not failures and not test:
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
            if dry_run:
                print(f"Subject: {subject}\n\n{text}")
            else:
                sender(subject, text, html_body)
                log.info("nothing new for %.1fh; sent the 'nothing has been posted' email", quiet_for)
            state.last_email_at = now
        else:
            log.info("nothing new qualifies; next 'nothing posted' email after %gh of quiet", cfg.empty_email_every_hours)
    else:
        log.info("nothing new qualifies; no email")

    if dry_run or test:
        log.info("%s: state not saved", "test" if test else "dry run")
    else:
        for key, postings in fetched.items():
            state.mark_seen(key, postings)
        state.mark_emailed([m.posting for m in matches])
        if not failures:
            state.last_run_at = now
        state.save(state_path)

    for f in failures:
        log.error(f)
    return 1 if failures else 0


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
