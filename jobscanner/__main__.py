"""Check intern-list.com and email new qualifying internships.

Usage: python -m jobscanner [--config config.yaml] [--state state/seen.json] [--dry-run] [--preview] [--show-lists]
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


def run(
    config_path: str,
    state_path: str,
    dry_run: bool = False,
    preview: bool = False,
    session=None,
    sender: Callable[[str, str, str], None] = emailer.send,
    now: datetime | None = None,
) -> int:
    cfg = load_config(config_path)
    state = State.load(state_path)
    session = session or source.new_session()
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=cfg.max_age_hours)
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
        log.info("list '%s': %d posted in the last %gh, %d new, %d qualify",
                 spec.key, len(postings), cfg.max_age_hours, len(new), kept)

    matches = rank(matches)
    if matches:
        subject, text, html_body = emailer.render(matches, now=now)
        if dry_run:
            print(f"Subject: {subject}\n\n{text}")
        else:
            sender(subject, text, html_body)  # raises -> state not saved, retried next run
            log.info("emailed %d postings", len(matches))
    else:
        log.info("nothing new qualifies; no email")

    if dry_run:
        log.info("dry run: state not saved")
    else:
        for key, postings in fetched.items():
            state.mark_seen(key, postings)
        state.mark_emailed([m.posting for m in matches])
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
    ap.add_argument("--show-lists", action="store_true", help="print the list keys intern-list.com offers")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.show_lists:
        for key, category in sorted(source.fetch_list_map(source.new_session()).items()):
            print(f"{key:8} {category}")
        return 0
    try:
        return run(args.config, args.state, dry_run=args.dry_run or args.preview, preview=args.preview)
    except Exception as e:
        log.error("%s: %s", type(e).__name__, e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
