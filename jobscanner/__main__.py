"""Check intern-list.com and email new qualifying internships.

Usage: python -m jobscanner [--config config.yaml] [--state state/seen.json] [--dry-run] [--preview] [--show-lists]
"""

from __future__ import annotations

import argparse
import logging
import sys
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
) -> int:
    cfg = load_config(config_path)
    state = State.load(state_path)
    session = session or source.new_session()
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
            postings = source.fetch_view(session, available[spec.key], spec.key)
        except Exception as e:  # keep going so other lists still get checked
            failures.append(f"list '{spec.key}': {e}")
            continue
        fetched[spec.key] = postings

        new, baselined = (postings, False) if preview else state.diff(spec.key, postings)
        if baselined:
            log.info("list '%s': first run, recorded %d existing postings without emailing", spec.key, len(postings))
            continue
        if cfg.only_latest_date:
            latest = max((p.date for p in postings), default="")
            new = [p for p in new if p.date == latest]
            log.info("list '%s': only postings dated %s", spec.key, latest)
        kept = 0
        for p in new:
            if not spec.wants(p.title) or state.was_emailed(p) or any(k in batch_keys for k in p.job_keys):
                continue
            m = evaluate(p, cfg.criteria)
            if m:
                matches.append(m)
                batch_keys.update(p.job_keys)
                kept += 1
        log.info("list '%s': %d postings, %d new, %d qualify", spec.key, len(postings), len(new), kept)

    matches = rank(matches)
    if matches:
        subject, text, html_body = emailer.render(matches)
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
    ap.add_argument("--preview", action="store_true", help="dry run that treats every current posting as new")
    ap.add_argument("--show-lists", action="store_true", help="print the list keys intern-list.com offers")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.show_lists:
        for key, url in sorted(source.fetch_list_map(source.new_session()).items()):
            print(f"{key:8} {url}")
        return 0
    try:
        return run(args.config, args.state, dry_run=args.dry_run or args.preview, preview=args.preview)
    except Exception as e:
        log.error("%s: %s", type(e).__name__, e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
