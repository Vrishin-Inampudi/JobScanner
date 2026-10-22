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

# Dark palette after Notion Calendar: near-black canvas, hairline borders, event-style
# blocks with a colored left bar (red = top company, blue = other match).
BG, PANEL, LINE = "#191919", "#202020", "#2F2F2F"
TEXT, MUTED, FAINT = "#ECECEC", "#9B9B9B", "#6F6F6F"
TODAY = "#EB5757"
TOP = {"bar": "#EB5757", "fill": "#3A2422", "tag": "#4A2A27", "tag_text": "#F3A49E"}
OTHER = {"bar": "#4DA3FF", "fill": "#1F2C3A", "tag": "#26394D", "tag_text": "#A9CFF7"}
SANS = "-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Inter,Helvetica,Arial,sans-serif"


def _age(m: Match, now: datetime) -> str:
    when = m.posting.posted_at
    if when is None:
        return m.posting.date
    minutes = max(0, int((now - when).total_seconds() // 60))
    return f"{minutes} min ago" if minutes < 60 else f"{minutes // 60}h {minutes % 60}m ago"


def _clock(when: datetime) -> str:
    local = when.astimezone(DISPLAY_TZ)
    return f"{local:%I:%M %p}".lstrip("0")


def _pay(m: Match) -> str:
    return f"${m.hourly:,.0f}/hr" if m.hourly is not None else "Pay not listed"


def _tags(m: Match) -> list[tuple[str, bool]]:
    """Why it matched (plus pay, always), as (label, highlighted) pairs."""
    tags = []
    if m.priority:
        tags.append(("★ Top company", True))
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


def _event(m: Match, now: datetime) -> str:
    """One job, drawn like a calendar event block."""
    e = lambda s: html.escape(str(s), quote=True)
    p = m.posting
    c = TOP if m.priority else OTHER
    when = f"{_clock(p.posted_at)} · {_age(m, now)}" if p.posted_at else p.date
    facts = " &nbsp;·&nbsp; ".join(
        f'<span style="white-space:nowrap">{e(x)}</span>'
        for x in (p.company, pretty_location(p.location), p.work_model) if x
    )
    tags = "".join(
        f'<span style="display:inline-block;margin:0 6px 6px 0;padding:3px 9px;border-radius:6px;'
        f'background:{c["tag"] if top else LINE};color:{c["tag_text"] if top else MUTED};'
        f'font:{600 if top else 500} 12px/1.5 {SANS}">{e(label)}</span>'
        for label, top in _tags(m)
    )
    return f"""
<tr><td style="padding:0 0 10px 0">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{c['fill']}"
         style="background:{c['fill']};border-radius:8px;border-left:4px solid {c['bar']}">
    <tr><td style="padding:14px 16px 12px 14px">
      <div style="font:600 16px/1.35 {SANS};color:{TEXT}">
        <a href="{e(p.apply_url)}" style="color:{TEXT};text-decoration:none">{e(p.title)}</a></div>
      <div style="font:500 13px/1.5 {SANS};color:{c['tag_text']};margin:3px 0 6px">{e(when)}</div>
      <div style="font:400 13px/1.55 {SANS};color:{MUTED};margin-bottom:10px">{facts}</div>
      <div>{tags}</div>
      <a href="{e(p.apply_url)}" style="display:inline-block;margin-top:4px;padding:7px 14px;border-radius:7px;
         background:#2A2A2A;border:1px solid #3A3A3A;color:{TEXT};font:600 13px/1 {SANS};text-decoration:none">Apply &rarr;</a>
    </td></tr>
  </table>
</td></tr>"""


def _legend_dot(color: str, label: str) -> str:
    return (f'<span style="display:inline-block;margin-right:16px;white-space:nowrap">'
            f'<span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:{color};'
            f'vertical-align:middle;margin-right:7px"></span>'
            f'<span style="vertical-align:middle">{label}</span></span>')


def _page(now: datetime, preheader: str, headline: str, sub: str, body: str, legend: bool) -> str:
    """The shared dark shell: header with today badge, headline, body rows, footer."""
    local = now.astimezone(DISPLAY_TZ)
    key = f"{_legend_dot(TOP['bar'], 'Top company')}{_legend_dot(OTHER['bar'], 'Other match')}" if legend else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark"><title>JobScanner</title></head>
<body style="margin:0;padding:0;background:{BG}" bgcolor="{BG}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{BG}" style="background:{BG}">
<tr><td align="center" style="padding:24px 12px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px">
    <tr><td style="padding:0 2px 14px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="font:600 13px/1 {SANS};color:{MUTED}">JobScanner</td>
        <td align="right" style="font:500 15px/1 {SANS};color:{TEXT}">
          {local:%a} <span style="display:inline-block;padding:3px 6px;border-radius:6px;background:{TODAY};color:#FFFFFF;font-weight:600">{local.day}</span>
          <span style="color:{FAINT};font-size:12px;margin-left:6px">{_clock(now)} ET</span></td>
      </tr></table>
    </td></tr>
    <tr><td style="padding:6px 2px 4px;font:700 30px/1.2 {SANS};color:{TEXT};letter-spacing:-0.4px">
      {html.escape(headline)}</td></tr>
    <tr><td style="padding:0 2px 16px;font:400 14px/1.5 {SANS};color:{MUTED};border-bottom:1px solid {LINE}">
      {html.escape(sub)}</td></tr>
    <tr><td style="height:16px;line-height:16px;font-size:0">&nbsp;</td></tr>
    {body}
    <tr><td style="padding:14px 2px 0;border-top:1px solid {LINE};font:400 12px/1.6 {SANS};color:{MUTED}">
      {key}
      <div style="margin-top:8px;color:{FAINT}">Checked hourly from intern-list.com &middot; edit config.yaml in your JobScanner repo to change what you get.</div>
    </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""


def _html(matches: list[Match], now: datetime) -> str:
    noun = "internship" if len(matches) == 1 else "internships"
    return _page(
        now,
        preheader=", ".join(dict.fromkeys(m.posting.company for m in matches)),
        headline=f"{len(matches)} new {noun}",
        sub="Posted since your last check · best first",
        body="".join(_event(m, now) for m in matches),
        legend=True,
    )


def render(matches: list[Match], now: datetime | None = None) -> tuple[str, str, str]:
    now = now or datetime.now(timezone.utc)
    return _subject(matches), _text(matches, now), _html(matches, now)


EMPTY_HEADLINE = "Hi, nothing has been posted!"
EMPTY_SUB = "Check back in an hour."


def render_empty(quiet_hours: float, scanned: int, lists: int, now: datetime | None = None) -> tuple[str, str, str]:
    """The 'nothing has been posted' email, in the same template."""
    now = now or datetime.now(timezone.utc)
    hours = round(quiet_hours)
    detail = (f"No new matches in the last {hours} hour{'s' if hours != 1 else ''}. "
              f"This check scanned {scanned} recent posting{'s' if scanned != 1 else ''} across {lists} list{'s' if lists != 1 else ''}.")
    slot = f"""
<tr><td style="padding:0 0 10px 0">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px dashed {LINE};border-radius:8px">
    <tr><td style="padding:22px 16px;text-align:center">
      <div style="font:600 15px/1.4 {SANS};color:{MUTED}">Nothing new to show</div>
      <div style="font:400 13px/1.5 {SANS};color:{FAINT};margin-top:4px">{html.escape(detail)}</div>
    </td></tr>
  </table>
</td></tr>"""
    subject = f"Nothing new in the last {hours}h · {_clock(now)} ET"
    text = f"{EMPTY_HEADLINE}\n{EMPTY_SUB}\n\n{detail}\n"
    return subject, text, _page(now, f"{EMPTY_HEADLINE} {EMPTY_SUB}", EMPTY_HEADLINE, EMPTY_SUB, slot, legend=False)


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
