"""Render and send the alert email through Gmail SMTP."""

from __future__ import annotations

import html
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from .filters import Match, pretty_location

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587


class EmailConfigError(RuntimeError):
    pass


DISPLAY_TZ = ZoneInfo("America/New_York")

# Palette: warm paper background, white cards, ink text, one orange accent.
BG, CARD, BORDER = "#F5F2EC", "#FFFFFF", "#E7E1D6"
INK, MUTED, ACCENT = "#1C1C1C", "#6B665E", "#D9480F"
TAG_BG, TOP_BG = "#F1EDE5", "#FFF0E6"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
SERIF = "Georgia,'Times New Roman',serif"


def _age(m: Match, now: datetime) -> str:
    when = m.posting.posted_at
    if when is None:
        return m.posting.date
    minutes = max(0, int((now - when).total_seconds() // 60))
    return f"{minutes} min ago" if minutes < 60 else f"{minutes // 60}h {minutes % 60}m ago"


def _pay(m: Match) -> str:
    return f"${m.hourly:,.0f}/hr" if m.hourly is not None else "Pay not listed"


def _tags(m: Match) -> list[tuple[str, bool]]:
    """Why it matched, as (label, highlighted) pairs."""
    tags = []
    if m.priority:
        tags.append(("★ Top company", True))
    if m.hourly is not None:
        tags.append((_pay(m), False))
    if m.state:
        tags.append((f"In {m.state}", False))
    if m.size >= 10000:
        tags.append(("10k+ employees", False))
    return tags


def _subject(matches: list[Match]) -> str:
    companies = list(dict.fromkeys(m.posting.company for m in matches))
    names = ", ".join(companies[:3]) + ("…" if len(companies) > 3 else "")
    noun = "internship" if len(matches) == 1 else "internships"
    return f"🎯 {len(matches)} new {noun} — {names}"


def _text(matches: list[Match], now: datetime) -> str:
    parts = []
    for m in matches:
        p = m.posting
        facts = " · ".join(x for x in (pretty_location(p.location), _pay(m), p.work_model, f"posted {_age(m, now)}") if x)
        why = ", ".join(label for label, _ in _tags(m))
        parts.append(f"{p.company.upper()}\n{p.title}\n{facts}\nWhy: {why}\nApply: {p.apply_url}\n")
    return "\n".join(parts)


def _card(m: Match, now: datetime) -> str:
    e = lambda s: html.escape(str(s), quote=True)
    p = m.posting
    facts = " &nbsp;·&nbsp; ".join(
        f'<span style="white-space:nowrap">{e(x)}</span>'
        for x in (pretty_location(p.location), _pay(m), p.work_model, _age(m, now)) if x
    )
    tags = "".join(
        f'<span style="display:inline-block;margin:0 6px 6px 0;padding:4px 10px;border-radius:999px;'
        f'background:{TOP_BG if top else TAG_BG};color:{ACCENT if top else INK};font-size:12px;'
        f'font-weight:{600 if top else 500}">{e(label)}</span>'
        for label, top in _tags(m)
    )
    return f"""
<tr><td style="padding:0 0 14px 0">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CARD};border:1px solid {BORDER};border-radius:12px">
    <tr><td style="padding:20px 22px">
      <div style="font:700 11px/1.4 {SANS};letter-spacing:1.2px;text-transform:uppercase;color:{ACCENT}">{e(p.company)}</div>
      <div style="font:700 18px/1.35 {SANS};color:{INK};margin:6px 0 8px">
        <a href="{e(p.apply_url)}" style="color:{INK};text-decoration:none">{e(p.title)}</a></div>
      <div style="font:400 14px/1.5 {SANS};color:{MUTED};margin-bottom:12px">{facts}</div>
      <div style="font-family:{SANS};margin-bottom:8px">{tags}</div>
      <a href="{e(p.apply_url)}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:{INK};
         color:#FFFFFF;font:600 14px/1 {SANS};text-decoration:none">Apply &rarr;</a>
    </td></tr>
  </table>
</td></tr>"""


def _html(matches: list[Match], now: datetime) -> str:
    noun = "internship" if len(matches) == 1 else "internships"
    local = now.astimezone(DISPLAY_TZ)
    stamp = f"{local:%A, %b} {local.day} · {local:%I:%M %p}".replace(" 0", " ") + " ET"
    preheader = html.escape(", ".join(dict.fromkeys(m.posting.company for m in matches)))
    cards = "".join(_card(m, now) for m in matches)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>JobScanner</title></head>
<body style="margin:0;padding:0;background:{BG}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG}">
<tr><td align="center" style="padding:28px 12px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px">
    <tr><td style="padding:0 4px 18px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="font:800 13px/1 {SANS};letter-spacing:2px;color:{INK}">JOBSCANNER</td>
        <td align="right" style="font:400 12px/1 {SANS};color:{MUTED}">{stamp}</td>
      </tr></table>
      <div style="height:2px;background:{INK};margin-top:12px"></div>
    </td></tr>
    <tr><td style="padding:6px 4px 22px">
      <div style="font:700 30px/1.2 {SERIF};color:{INK}">{len(matches)} new {noun} worth a look</div>
      <div style="font:400 15px/1.5 {SANS};color:{MUTED};margin-top:8px">
        Posted since your last check, best first: top companies, then pay, then location.</div>
    </td></tr>
    {cards}
    <tr><td style="padding:14px 4px 0;font:400 12px/1.6 {SANS};color:{MUTED};text-align:center">
      Checked hourly from intern-list.com &middot; Edit <b>config.yaml</b> in your JobScanner repo to change what you get.
    </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""


def render(matches: list[Match], now: datetime | None = None) -> tuple[str, str, str]:
    now = now or datetime.now(timezone.utc)
    return _subject(matches), _text(matches, now), _html(matches, now)


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
