"""Render and send the alert email through Gmail SMTP."""

from __future__ import annotations

import html
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from .filters import Match, pretty_location

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587


class EmailConfigError(RuntimeError):
    pass


def _pay(m: Match) -> str:
    return m.posting.salary if m.hourly is not None else "Pay not listed"


def _posted(m: Match, now: datetime) -> str:
    when = m.posting.posted_at
    if when is None:
        return f"posted {m.posting.date}"
    minutes = max(0, int((now - when).total_seconds() // 60))
    return f"posted {minutes} min ago" if minutes < 60 else f"posted {minutes // 60}h {minutes % 60}m ago"


def render(matches: list[Match], now: datetime | None = None) -> tuple[str, str, str]:
    now = now or datetime.now(timezone.utc)
    companies = list(dict.fromkeys(m.posting.company for m in matches))
    names = ", ".join(companies[:3]) + ("…" if len(companies) > 3 else "")
    noun = "internship" if len(matches) == 1 else "internships"
    subject = f"🎯 {len(matches)} new {noun} — {names}"

    text_parts, html_parts = [], []
    for m in matches:
        p = m.posting
        why = " · ".join(m.reasons)
        text_parts.append(
            f"{p.title}\n{p.company} — {pretty_location(p.location)}\n{_pay(m)} · {p.size or '?'} employees · {_posted(m, now)}\n"
            f"Why: {why}\nApply: {p.apply_url}\n"
        )
        e = html.escape
        html_parts.append(
            f'<div style="padding:12px 0;border-bottom:1px solid #ddd">'
            f'<div style="font-size:16px;font-weight:600"><a href="{e(p.apply_url, quote=True)}">{e(p.title)}</a></div>'
            f'<div>{e(p.company)} — {e(pretty_location(p.location))}</div>'
            f'<div style="color:#555">{e(_pay(m))} · {e(p.size or "?")} employees · {e(_posted(m, now))}</div>'
            f'<div style="color:#0a6">Why: {e(why)}</div></div>'
        )
    text = "\n".join(text_parts)
    body = (
        '<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:680px">'
        + "".join(html_parts)
        + '<p style="color:#888;font-size:12px">From intern-list.com · sent by JobScanner</p></div>'
    )
    return subject, text, body


def send(subject: str, text: str, html_body: str, env=os.environ) -> None:
    sender, password = env.get("GMAIL_ADDRESS"), env.get("GMAIL_APP_PASSWORD")
    if not sender or not password:
        raise EmailConfigError("set GMAIL_ADDRESS and GMAIL_APP_PASSWORD (see README) to send email")
    recipients = [r.strip() for r in (env.get("EMAIL_TO") or sender).split(",") if r.strip()]

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(recipients)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.send_message(msg)
