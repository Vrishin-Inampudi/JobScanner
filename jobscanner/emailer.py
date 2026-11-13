"""Render and send the alert email through Gmail SMTP."""

from __future__ import annotations

import html
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from .ai import draft_filename
from .filters import MAX_PLACES_SHOWN, Match, pretty_location, pretty_month

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
HAVE = {"fill": "#1E3326", "text": "#8FD6A4"}   # skills you have
LACK = {"fill": "#3A3020", "text": "#E9BE74"}   # skills you don't
MAX_SKILLS = 8
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


def _where(p) -> str:
    return pretty_location(p.location, MAX_PLACES_SHOWN)


def _tags(m: Match) -> list[tuple[str, bool]]:
    """Why it matched (plus pay when the posting gives it), as (label, highlighted) pairs."""
    tags = []
    if m.priority:
        tags.append(("★ Top company", True))
    if m.fit is not None:
        tags.append((f"Fit {m.fit}", True))
    if m.hourly is not None:
        tags.append((_pay(m), True))
    if m.area:
        tags.append((f"In {m.area}", False))
    if m.size >= 10000:
        tags.append(("10k+ employees", False))
    tags += [(flag, False) for flag in m.flags]
    return tags


def _skills_text(m: Match) -> str:
    """'✓ Python, C++ · ✗ Java' or '' when the posting mentions none of your skills."""
    parts = [f"✓ {', '.join(m.have)}" if m.have else "", f"✗ {', '.join(m.lack)}" if m.lack else ""]
    return " · ".join(x for x in parts if x)


def _skill_chips(m: Match) -> str:
    """Green chips for skills you have, amber for ones you don't (a few, then '+N more')."""
    e = lambda s: html.escape(str(s), quote=True)
    chip = lambda label, c: (f'<span style="display:inline-block;margin:0 6px 6px 0;padding:2px 8px;border-radius:6px;'
                             f'background:{c["fill"]};color:{c["text"]};font:500 12px/1.5 {SANS}">{e(label)}</span>')
    out = [chip(f"✓ {s}", HAVE) for s in m.have[:MAX_SKILLS]]
    if len(m.have) > MAX_SKILLS:
        out.append(chip(f"+{len(m.have) - MAX_SKILLS} more", HAVE))
    out += [chip(f"✗ {s}", LACK) for s in m.lack]
    return f'<div>{"".join(out)}</div>' if out else ""


def _timing(p) -> str:
    """'Starts May 2027 · For Dec 2027 / Jun 2028 grads'."""
    start, grad = pretty_month(p.start_time), pretty_month(p.grad_time)
    return " · ".join(x for x in (start and f"Starts {start}", grad and f"For {grad} grads") if x)


def _subject(matches: list[Match]) -> str:
    companies = list(dict.fromkeys(m.posting.company for m in matches))
    names = ", ".join(companies[:3]) + ("…" if len(companies) > 3 else "")
    noun = "internship" if len(matches) == 1 else "internships"
    return f"🎯 {len(matches)} new {noun} — {names}"


def _text(matches: list[Match], now: datetime, links=None) -> str:
    parts = []
    for m in matches:
        p = m.posting
        facts = " · ".join(x for x in (_where(p), _pay(m), p.work_model, f"posted {_age(m, now)}",
                                         _timing(p), _via(p)) if x)
        why = ", ".join(label for label, _ in _tags(m)) or "matches your rules"
        track = (links or (lambda _: None))(p)
        mark = f"Mark applied: {track['applied']}\nNot interested: {track['skip']}\n" if track else ""
        skills = f"Skills: {_skills_text(m)}\n" if _skills_text(m) else ""
        fit = f"Fit: {m.fit}/100. {m.fit_why}\n" if m.fit is not None else ""
        draft = f"Draft attached: {draft_filename(p)}\n" if m.draft else ""
        parts.append(f"{p.company.upper()}\n{p.title}\n{facts}\nWhy: {why}\n{fit}{skills}Apply: {p.apply_url}\n{mark}{draft}")
    return "\n".join(parts)


def _via(p) -> str:
    """Where we found it, unless that's intern-list. The card already names the company, so its own site is just that."""
    if p.source == "intern-list":
        return ""
    return "via company site" if p.source == f"{p.company} careers site" else f"via {p.source}"


def _event(m: Match, now: datetime, links=None) -> str:
    """One job, drawn like a calendar event block."""
    e = lambda s: html.escape(str(s), quote=True)
    p = m.posting
    c = TOP if m.priority else OTHER
    when = f"{_clock(p.posted_at)} · {_age(m, now)}" if p.posted_at else f"Posted {p.date}"
    # Each fact stays on one line where it fits, but may wrap: a long one must never widen the card.
    facts = " &nbsp;·&nbsp; ".join(
        f'<span style="display:inline-block;max-width:100%">{e(x)}</span>'
        for x in (_where(p), p.work_model, _timing(p), _via(p)) if x
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
    <tr><td style="padding:14px 16px 12px 14px;word-break:break-word">
      <div style="font:700 13px/1.4 {SANS};color:{c['tag_text']};letter-spacing:0.2px">{e(p.company)}
        <span style="font-weight:400;color:{MUTED}">&nbsp;·&nbsp; {e(when)}</span></div>
      <div style="font:600 17px/1.35 {SANS};color:{TEXT};margin:3px 0 5px">
        <a href="{e(p.apply_url)}" style="color:{TEXT};text-decoration:none">{e(p.title)}</a></div>
      <div style="font:400 13px/1.55 {SANS};color:{MUTED};margin-bottom:10px">{facts}</div>
      <div>{tags}</div>{_skill_chips(m)}{_fit_line(m)}
      <a href="{e(p.apply_url)}" style="display:inline-block;margin-top:4px;padding:7px 14px;border-radius:7px;
         background:#2A2A2A;border:1px solid #3A3A3A;color:{TEXT};font:600 13px/1 {SANS};text-decoration:none">Apply &rarr;</a>{_track(p, links)}
    </td></tr>
  </table>
</td></tr>"""


def _fit_line(m: Match) -> str:
    """Why the resume fits (or doesn't), and a pointer to the attached draft."""
    lines = [m.fit_why] if m.fit_why else []
    if m.draft:
        lines.append(f"Draft cover letter attached: {draft_filename(m.posting)}")
    return "".join(f'<div style="font:400 13px/1.5 {SANS};color:{MUTED};margin:0 0 6px">{html.escape(x)}</div>' for x in lines)


def attachments(matches: list[Match]) -> list[tuple[str, str]]:
    """(filename, text) for each match that has a draft."""
    return [(draft_filename(m.posting), f"{m.posting.company}: {m.posting.title}\n{m.posting.apply_url}\n\n{m.draft}\n")
            for m in matches if m.draft]


def _interval(minutes: int) -> str:
    """60 -> 'an hour', 30 -> '30 minutes', 120 -> '2 hours'."""
    if minutes % 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    return "an hour" if hours == 1 else f"{hours} hours"


def _track(p, links) -> str:
    """'✓ Applied' and 'Not interested' links that record to your tracker, when it's set up."""
    track = links(p) if links else None
    if not track:
        return ""
    style = f"display:inline-block;margin:4px 0 0 12px;color:{MUTED};font:500 13px/1 {SANS};text-decoration:none"
    return (f'<a href="{html.escape(track["applied"], quote=True)}" style="{style}">&#10003; Applied</a>'
            f'<a href="{html.escape(track["skip"], quote=True)}" style="{style}">Not interested</a>')


def _legend_dot(color: str, label: str) -> str:
    return (f'<span style="display:inline-block;margin-right:16px;white-space:nowrap">'
            f'<span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:{color};'
            f'vertical-align:middle;margin-right:7px"></span>'
            f'<span style="vertical-align:middle">{label}</span></span>')


def _notes(notes) -> str:
    """Things that need your attention (a company site that stopped loading), above the footer."""
    return "".join(f'<div style="margin:0 0 8px;padding:8px 10px;border-radius:6px;background:{LACK["fill"]};'
                   f'color:{LACK["text"]};font:400 12px/1.5 {SANS}">{html.escape(n)}</div>' for n in notes)


def _page(now: datetime, preheader: str, headline: str, sub: str, body: str, legend: bool, check_every: int,
          notes=(), sources: str = "", dashboard: str = "") -> str:
    """The shared dark shell: header with today badge, headline, body rows, footer."""
    local = now.astimezone(DISPLAY_TZ)
    key = (f"{_legend_dot(TOP['bar'], 'Top company')}{_legend_dot(OTHER['bar'], 'Other match')}"
           f"{_legend_dot(HAVE['text'], '✓ Skill you have')}{_legend_dot(LACK['text'], '✗ Skill you don’t')}") if legend else ""
    board = (f'<a href="{html.escape(dashboard, quote=True)}" style="color:{OTHER["tag_text"]};text-decoration:none">'
             f'Open your dashboard &rarr;</a> &middot; ') if dashboard else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark"><title>JobScanner</title></head>
<body style="margin:0;padding:0;background:{BG}" bgcolor="{BG}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{BG}" style="background:{BG};table-layout:fixed">
<tr><td align="center" style="padding:24px 12px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;table-layout:fixed">
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
      {_notes(notes)}{key}
      <div style="margin-top:8px;color:{FAINT}">{board}Checked every {_interval(check_every).removeprefix("an ")} from {html.escape(sources or "intern-list.com")} &middot; edit config.yaml in your JobScanner repo to change what you get.</div>
    </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""


def _html(matches: list[Match], now: datetime, check_every: int, links=None, digest: bool = False,
          notes=(), sources: str = "", dashboard: str = "") -> str:
    noun = "internship" if len(matches) == 1 else "internships"
    return _page(
        now,
        preheader=", ".join(dict.fromkeys(m.posting.company for m in matches)),
        headline=f"{len(matches)} more {noun}" if digest else f"{len(matches)} new {noun}",
        sub=("Today's digest: matches that weren't urgent enough to send right away · best first" if digest
             else "Posted since your last check · best first"),
        body="".join(_event(m, now, links) for m in matches),
        legend=True,
        check_every=check_every,
        notes=notes,
        sources=sources,
        dashboard=dashboard,
    )


def render(matches: list[Match], now: datetime | None = None, check_every: int = 60, links=None,
           digest: bool = False, notes=(), sources: str = "", dashboard: str = "") -> tuple[str, str, str]:
    """The job email. ``links(posting)`` gives tracker links; ``digest`` words it as the daily digest.

    ``notes`` are warnings shown above the footer; ``sources`` names what was checked.
    """
    now = now or datetime.now(timezone.utc)
    subject = _subject(matches)
    if digest:
        subject = "☀️ Daily digest: " + subject.removeprefix("🎯 ").replace(" new ", " more ", 1)
    text = _text(matches, now, links) + "".join(f"\nNote: {n}" for n in notes)
    return subject, text, _html(matches, now, check_every, links, digest, notes, sources, dashboard)


EMPTY_HEADLINE = "Hi, nothing has been posted!"


def render_empty(quiet_hours: float, scanned: int, lists: int, now: datetime | None = None,
                 check_every: int = 60, notes=(), sources: str = "", dashboard: str = "") -> tuple[str, str, str]:
    """The 'nothing has been posted' email, in the same template."""
    now = now or datetime.now(timezone.utc)
    back = f"Check back in {_interval(check_every)}."
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
    text = f"{EMPTY_HEADLINE}\n{back}\n\n{detail}\n" + "".join(f"Note: {n}\n" for n in notes)
    return subject, text, _page(now, f"{EMPTY_HEADLINE} {back}", EMPTY_HEADLINE, back, slot,
                                legend=False, check_every=check_every, notes=notes, sources=sources, dashboard=dashboard)


def _tile(value, label: str) -> str:
    return (f'<td width="25%" style="padding:0 6px 0 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'bgcolor="{PANEL}" style="background:{PANEL};border-radius:8px"><tr><td style="padding:12px 10px">'
            f'<div style="font:700 24px/1.1 {SANS};color:{TEXT}">{html.escape(str(value))}</div>'
            f'<div style="font:400 12px/1.4 {SANS};color:{MUTED};margin-top:3px">{html.escape(label)}</div></td></tr></table></td>')


def _job_list(heading: str, jobs: list[dict], note) -> str:
    """A titled list of linked jobs; ``note(job)`` is the small text after each."""
    if not jobs:
        return ""
    rows = "".join(
        f'<div style="padding:8px 0;border-top:1px solid {LINE};font:400 14px/1.45 {SANS};color:{TEXT};word-break:break-word">'
        f'<a href="{html.escape(j["url"], quote=True)}" style="color:{TEXT};text-decoration:none">'
        f'<span style="font-weight:600">{html.escape(j["company"])}</span> &middot; {html.escape(j["title"])}</a>'
        f'<span style="color:{MUTED};font-size:12px"> &nbsp;{html.escape(note(j))}</span></div>' for j in jobs)
    return (f'<tr><td style="padding:14px 2px 6px"><div style="font:600 13px/1.4 {SANS};color:{MUTED};'
            f'margin-bottom:4px">{html.escape(heading)}</div>{rows}</td></tr>')


MAX_LISTED = 10


def render_weekly(found: int, waiting: list[dict], follow_up: list[dict], counts: dict[str, int] | None,
                  now: datetime | None = None, check_every: int = 60, notes=(), sources: str = "",
                  dashboard: str = "", follow_up_days: int = 10) -> tuple[str, str, str]:
    """The weekly summary: what matched, what you did about it, and what's still waiting.

    ``waiting`` and ``follow_up`` are jobs as {company, title, url} (plus days for follow-ups); ``counts`` has your
    applied / interview / offer / rejected numbers for the week, or is None without the tracker.
    """
    now = now or datetime.now(timezone.utc)
    counts = counts or {}
    stat = lambda n, one, many: f"{n} {one if n == 1 else many}"
    headline = f"{stat(found, 'match', 'matches')} this week"
    done = f", {counts['applied']} applied" if "applied" in counts else ""
    subject = f"📊 Your week: {stat(found, 'match', 'matches')}{done}"
    tiles = [_tile(found, "Matches found")]
    tiles += [_tile(counts[k], label) for k, label in (("applied", "Applied"), ("interview", "Interviews"), ("offer", "Offers"))
              if k in counts]
    fit = lambda j: ""
    late = lambda j: f"applied {j['days']} days ago"
    more = f" (showing the top {MAX_LISTED})" if len(waiting) > MAX_LISTED else ""
    body = (f'<tr><td style="padding:0 0 6px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'{"".join(tiles)}</tr></table></td></tr>'
            + _job_list(f"Still waiting on you: {len(waiting)}{more}", waiting[:MAX_LISTED], fit)
            + _job_list(f"Worth a follow-up: applied {follow_up_days}+ days ago, no news", follow_up[:MAX_LISTED], late))
    sub = "The last 7 days" + ("" if counts else " · set up the tracker to see what you've applied to")
    lines = [headline, sub, ""]
    lines += [f"{label}: {counts[k]}" for k, label in (("applied", "Applied"), ("interview", "Interviews"), ("offer", "Offers"),
                                                       ("rejected", "Rejected")) if k in counts]
    for heading, jobs, note in (("Still waiting on you", waiting[:MAX_LISTED], fit), ("Worth a follow-up", follow_up[:MAX_LISTED], late)):
        if jobs:
            lines += ["", f"{heading}:"] + [f"- {j['company']}: {j['title']} ({note(j)}) {j['url']}".replace(" ()", "") for j in jobs]
    lines += [f"\nDashboard: {dashboard}"] if dashboard else []
    text = "\n".join(lines) + "\n" + "".join(f"Note: {n}\n" for n in notes)
    return subject, text, _page(now, headline, headline, sub, body, legend=False, check_every=check_every, notes=notes,
                                sources=sources, dashboard=dashboard)


# The project email has its own look: black, large type, one idea per panel.
P_BG, P_PANEL, P_TEXT, P_MUTED, P_BLUE, P_BUTTON, P_ORANGE = "#000000", "#1d1d1f", "#f5f5f7", "#86868b", "#2997ff", "#0071e3", "#ff9f0a"
P_FONT = "-apple-system,BlinkMacSystemFont,'SF Pro Display','Helvetica Neue',Helvetica,Arial,sans-serif"
MAIN_PICTURE = "project-main"
KIND = {"certification": "Certification", "topic": "Topic", "concept": "Concept"}


def _panel(title: str, inner: str) -> str:
    return (f'<tr><td style="padding:0 0 14px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'<tr><td style="background:{P_PANEL};border-radius:18px;padding:22px 24px">'
            f'<div style="font-size:19px;font-weight:700;color:{P_TEXT};margin:0 0 8px">{html.escape(title)}</div>'
            f'<div style="font-size:15px;line-height:1.6;color:{P_TEXT}">{inner}</div></td></tr></table></td></tr>')


def render_project(project, link: str = "", has_picture: bool = True, files=(), sample: bool = False) -> tuple[str, str, str]:
    """The weekly project email: (subject, plain text, HTML). ``files`` are the attachment names.

    Every count shown is worked out from your applied-to jobs, never taken from the model.
    """
    g, e = project.guide, html.escape
    title, tagline = str(g.get("title") or ""), str(g.get("tagline") or "")
    subject = ("[Sample] " if sample else "") + f"🛠 Project to build: {title}"
    share = f"{len(project.roles)} of {project.total}"
    facts = [(f"{g.get('hours')} hrs", "to finish")] if g.get("hours") else []
    facts += [(share, "of your applications")] if project.total else []
    facts += [(str(g.get("cost")), "cost")] if g.get("cost") else []
    muted = lambda t: f'<span style="color:{P_MUTED}">{e(t)}</span>'
    roles, more = project.roles[:6], len(project.roles) - 6
    reading = [f'<a href="{e(r["url"], quote=True)}" style="color:{P_BLUE};text-decoration:none">{e(r["title"])} ›</a>'
               if str(r.get("url") or "").startswith("https://") else e(r["title"]) for r in g.get("reading") or []]
    also = [f'{e(a["name"])} {muted(KIND.get(a["kind"], "Topic").lower() + " · named in " + str(a["count"]) + " of your " + str(project.total) + " applications")}'
            for a in project.also]
    button = (f'<a href="{e(link, quote=True)}" style="display:inline-block;background:{P_BUTTON};color:#ffffff;border-radius:980px;'
              f'padding:11px 22px;font-size:16px;text-decoration:none">Open the instructions</a>') if link else ""
    cells = "".join(f'<td align="center" width="{100 // len(facts)}%" style="padding:0 6px"><div style="font-size:{26 if len(v) < 12 else 17}px;'
                    f'font-weight:700;color:{P_TEXT}">{e(v)}</div><div style="font-size:13px;color:{P_MUTED}">{e(k)}</div></td>'
                    for v, k in facts)
    picture = (f'<tr><td align="center" style="padding:0 0 22px"><img src="cid:{MAIN_PICTURE}" width="520" alt="How the finished project fits together" '
               f'style="display:block;width:100%;max-width:520px;height:auto;border:0"></td></tr>') if has_picture else ""
    body = "".join([
        _panel("What you build.", e(str(g.get("description") or ""))),
        _panel("Roles this helps with.", "<br>".join(f"{e(r['company'])} {muted(r['title'])}" for r in roles)
               + (f"<br>{muted(f'and {more} more')}" if more > 0 else "")) if roles else "",
        _panel("What it proves.", e(project.proves)) if project.proves else "",
        _panel("Read first if rusty.", "<br>".join(reading)) if reading else "",
        _panel("Also worth your time.", "<br>".join(also)) if also else "",
    ])
    attached = f'Attached: {" · ".join(e(f) for f in files)}' if files else ""
    page = f"""<!doctype html><html><head><meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark"></head>
<body style="margin:0;padding:0;background:{P_BG}">
<div style="display:none;max-height:0;overflow:hidden">{e(tagline)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{P_BG};font-family:{P_FONT}"><tr><td align="center" style="padding:0 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px">
<tr><td align="center" style="padding:52px 8px 34px">
  <div style="font-size:13px;font-weight:600;color:{P_ORANGE}">{'Sample project' if sample else 'New project for you'}</div>
  <div style="font-size:38px;line-height:1.08;font-weight:700;letter-spacing:-.02em;color:{P_TEXT};margin-top:8px">{e(title)}</div>
  <div style="font-size:20px;line-height:1.3;color:{P_MUTED};margin-top:12px">{e(tagline)}</div>
  {f'<div style="margin-top:26px">{button}</div>' if button else ''}
</td></tr>
{picture}
<tr><td style="padding:0 0 34px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{cells}</tr></table></td></tr>
{body}
<tr><td style="padding:10px 8px 44px;font-size:13px;line-height:1.7;color:{P_MUTED}">{attached}{'<br>' if attached else ''}
Chosen from the jobs you marked as applied. One project a week.</td></tr>
</table></td></tr></table></body></html>"""
    lines = [f"Project to build: {title}", tagline, "", str(g.get("description") or ""), ""]
    lines += [f"{v} {k}" for v, k in facts] + [""]
    lines += [f"Open the instructions: {link}", ""] if link else []
    lines += ["Roles this helps with:"] + [f"- {r['company']}: {r['title']}" for r in roles] + [""] if roles else []
    lines += ["What it proves:", project.proves, ""] if project.proves else []
    lines += ["Read first if rusty:"] + [f"- {r['title']} {r.get('url') or ''}".rstrip() for r in g.get("reading") or []] + [""] if reading else []
    lines += ["Also worth your time:"] + [f"- {KIND.get(a['kind'], 'Topic')}: {a['name']} (named in {a['count']} of your {project.total} applications)"
                                          for a in project.also] if also else []
    return subject, "\n".join(lines).rstrip() + "\n", page


_FILE_TYPES = {"pdf": ("application", "pdf"), "md": ("text", "markdown"),
               "docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")}


def send(subject: str, text: str, html_body: str, attachments=(), inline=(), env=os.environ) -> None:
    sender, password = env.get("GMAIL_ADDRESS"), env.get("GMAIL_APP_PASSWORD")
    if not sender or not password:
        raise EmailConfigError("set GMAIL_ADDRESS and GMAIL_APP_PASSWORD (see README) to send email")
    recipients = [r.strip() for r in (env.get("EMAIL_TO") or sender).split(",") if r.strip()]

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(recipients)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    for cid, png in inline:   # pictures shown in the HTML body
        msg.get_payload()[-1].add_related(png, maintype="image", subtype="png", cid=f"<{cid}>")
    for filename, body in attachments:
        maintype, subtype = _FILE_TYPES.get(filename.rsplit(".", 1)[-1].lower(), ("text", "plain"))
        msg.add_attachment(body.encode("utf-8") if isinstance(body, str) else body, maintype=maintype, subtype=subtype,
                           filename=filename)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.send_message(msg)
