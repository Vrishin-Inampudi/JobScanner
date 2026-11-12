"""Also post urgent matches to a Discord channel, which reaches a phone faster than email.

Optional: set the DISCORD_WEBHOOK_URL secret to a channel webhook (Channel settings →
Integrations → Webhooks). Problems are logged, never fatal: the email is the record.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from .filters import MAX_PLACES_SHOWN, Match, pretty_location
from .source import TIMEOUT

log = logging.getLogger("jobscanner")

EMBEDS_PER_MESSAGE = 10   # Discord's limit
MAX_MESSAGES = 3          # a first run can match dozens; the email has them all
TOP_COLOR, OTHER_COLOR = 0xEB5757, 0x4DA3FF   # the email's red (top company) and blue


def webhook(env=os.environ) -> str | None:
    url = (env.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        return None
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in ("discord.com", "discordapp.com") or not u.path.startswith("/api/webhooks/"):
        log.warning("DISCORD_WEBHOOK_URL must be a https://discord.com/api/webhooks/... URL; Discord alerts are off")
        return None
    return url


def _embed(m: Match) -> dict:
    p = m.posting
    facts = [pretty_location(p.location, MAX_PLACES_SHOWN), f"${m.hourly:,.0f}/hr" if m.hourly is not None else "",
             f"Fit {m.fit}" if m.fit is not None else "", *m.flags]
    lines = [" · ".join(x for x in facts if x)]
    if m.fit_why:
        lines.append(m.fit_why)
    return {"title": f"{p.company}: {p.title}"[:256], "url": p.apply_url, "description": "\n".join(lines)[:1000],
            "color": TOP_COLOR if m.priority else OTHER_COLOR}


def send(session, matches: list[Match], env=os.environ) -> bool:
    """Post the matches; True if they were sent. Does nothing when Discord isn't set up."""
    url = webhook(env)
    if not url or not matches:
        return False
    shown = matches[: EMBEDS_PER_MESSAGE * MAX_MESSAGES]
    noun = "internship" if len(matches) == 1 else "internships"
    try:
        for i in range(0, len(shown), EMBEDS_PER_MESSAGE):
            more = len(matches) - len(shown)
            head = f"🎯 {len(matches)} new {noun}" + (f" ({more} more in your email)" if more else "") if i == 0 else ""
            # Titles come from job sites: never let one ping @everyone.
            resp = session.post(url, json={"content": head, "embeds": [_embed(m) for m in shown[i:i + EMBEDS_PER_MESSAGE]],
                                           "allowed_mentions": {"parse": []}}, timeout=TIMEOUT)
            resp.raise_for_status()
    except Exception as e:  # noqa: BLE001 - Discord trouble mustn't stop the alerts
        log.warning("couldn't post to Discord: %s", type(e).__name__)   # the error text can contain the webhook URL
        return False
    log.info("posted %d postings to Discord", len(shown))
    return True
