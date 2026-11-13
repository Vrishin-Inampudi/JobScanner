"""Turn a project guide into its files: pictures, a PDF, a Word file and an AI-helper Markdown file.

All four come from the same guide, so they always agree. Pictures are drawn here from the
guide's box-and-line descriptions; nothing the model wrote is ever used as markup. The PDF
uses a built-in font and the pictures use Pillow's, so no font files are needed where this runs.
"""

from __future__ import annotations

import io
import re
import unicodedata

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from fpdf import FPDF
from fpdf.enums import MethodReturnValue
from PIL import Image, ImageDraw, ImageFont

from .projects import WARNING, Project, has_diagram

MAX_NODES, MAX_LABEL = 12, 14
DARK = {"bg": (0, 0, 0), "box": (29, 29, 31), "line": (110, 110, 115), "edge": (245, 245, 247), "text": (245, 245, 247), "hot": (48, 209, 88)}
LIGHT = {"bg": (255, 255, 255), "box": (255, 255, 255), "line": (134, 134, 139), "edge": (29, 29, 31), "text": (29, 29, 31), "hot": (29, 138, 60)}


def _nodes(d) -> list[dict]:
    """The drawable boxes of a diagram: those with an id, at most MAX_NODES, rows as whole numbers."""
    out = []
    for n in (d.get("nodes") or [] if isinstance(d, dict) else []):
        if isinstance(n, dict) and str(n.get("id", "")).strip():
            try:
                row = int(n.get("row") or 0)
            except (TypeError, ValueError):
                row = 0
            out.append({"id": str(n["id"]), "label": " ".join(str(n.get("label") or n["id"]).split())[:MAX_LABEL], "row": row})
    return out[:MAX_NODES]


def _rows(nodes: list[dict], most: int) -> list[list[dict]]:
    """Boxes grouped into rows top to bottom; a row with more than ``most`` boxes wraps onto the next."""
    out = []
    for r in sorted({n["row"] for n in nodes}):
        in_row = [n for n in nodes if n["row"] == r]
        out += [in_row[i:i + most] for i in range(0, len(in_row), most)]
    return out


def picture(diagram, dark: bool = True, width: int = 640, bg: tuple | None = None) -> bytes | None:
    """A PNG of a diagram, or None when there's nothing to draw. Bad links and extra boxes are left out.

    ``bg`` is the colour it will sit on, when that isn't the page's.
    """
    nodes = _nodes(diagram)
    if not nodes:
        return None
    c, s = dict(DARK if dark else LIGHT), 2             # drawn at twice the size, so it stays sharp
    if bg:
        c["bg"], c["box"] = bg, (0, 0, 0) if dark else c["box"]
    rows = _rows(nodes, max(3, width // 110))
    per_row = max(len(r) for r in rows)
    bw, bh, gap, pad = min(150, width // (per_row + 1) - 10) * s, 40 * s, 70 * s, 12 * s   # boxes never touch
    height = len(rows) * bh + (len(rows) - 1) * gap + 2 * pad
    img = Image.new("RGB", (width * s, height), c["bg"])
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=max(9, min(15, bw // s // 7)) * s)
    at = {}
    for ri, in_row in enumerate(rows):
        for i, n in enumerate(in_row):
            at[n["id"]] = (width * s * (i + 1) // (len(in_row) + 1), pad + bh // 2 + ri * (bh + gap))
    hot = {str(h) for h in (diagram.get("highlight") or [])}
    for link in diagram.get("links") or []:
        a, b = (str(link.get(k, "")) for k in ("a", "b")) if isinstance(link, dict) else ("", "")
        if a in at and b in at:
            draw.line([at[a], at[b]], fill=c["hot"] if a in hot and b in hot else c["line"], width=2 * s)
    for n in nodes:
        x, y = at[n["id"]]
        draw.rounded_rectangle([x - bw // 2, y - bh // 2, x + bw // 2, y + bh // 2], radius=11 * s, fill=c["box"],
                               outline=c["hot"] if n["id"] in hot else c["edge"], width=2 * s)
        label = n["label"]
        while len(label) > 1 and font.getlength(label) > bw - 10 * s:   # a label never spills out of its box
            label = label[:-2].rstrip() + "…"
        draw.text((x, y), label, font=font, fill=c["text"], anchor="mm")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def steps(guide: dict):
    """(number, part index, step) for every step, numbered straight through the guide."""
    n = 0
    for pi, part in enumerate(guide.get("parts") or []):
        for s in part.get("steps") or []:
            n += 1
            yield n, pi, s


def _split(text) -> tuple[str, str]:
    """A step's leading verb and the rest of its sentence."""
    words = " ".join(str(text or "").split()).split(" ", 1)
    return words[0], (words[1] if len(words) > 1 else "")


def _hours(h) -> str:
    try:
        return f"{float(h):g}"
    except (TypeError, ValueError):
        return ""


def clean_name(text: str) -> str:
    return " ".join(re.sub(r"[^A-Za-z0-9 &+.-]+", " ", str(text)).split())[:60].strip() or "project"


# ---------- PDF ----------

_PUNCT = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "→": "->", "←": "<-", "•": "-",
          "✓": "OK:", "✕": "X:", "≥": ">=", "≤": "<=", "×": "x", " ": " "}
BOLD = "\x01\x01"


def _latin(text) -> str:
    """Text the PDF's built-in font can show: common punctuation swapped, anything else unusual dropped."""
    text = "".join(_PUNCT.get(ch, ch) for ch in str(text or ""))
    text = unicodedata.normalize("NFKC", text).replace("\t", "    ")
    return "".join(ch for ch in text if ch == "\n" or (ch.isprintable() and ord(ch) < 256))


class _Pdf(FPDF):
    # Only our own marker means bold; nothing in a guide's text can switch on other styling.
    MARKDOWN_BOLD_MARKER = BOLD
    MARKDOWN_ITALICS_MARKER = "\x02\x02"
    MARKDOWN_UNDERLINE_MARKER = "\x03\x03"
    MARKDOWN_STRIKETHROUGH_MARKER = "\x04\x04"
    MARKDOWN_MARKERS = (MARKDOWN_BOLD_MARKER, MARKDOWN_ITALICS_MARKER, MARKDOWN_STRIKETHROUGH_MARKER, MARKDOWN_UNDERLINE_MARKER)
    MARKDOWN_ESCAPE_CHARACTER = "\x05"
    MARKDOWN_LINK_REGEX = MARKDOWN_BULLET_REGEX = re.compile(r"(?!x)x")   # never matches

    def header(self):
        self.set_fill_color(0, 0, 0)
        self.rect(0, 0, self.w, self.h, style="F")


TEXT, MUTED, PANEL, BLUE, GREEN, ORANGE = (245, 245, 247), (134, 134, 139), (29, 29, 31), (41, 151, 255), (48, 209, 88), (255, 159, 10)
MARGIN, PIC_W = 18, 60


def pdf(project: Project) -> bytes:
    g = project.guide
    doc = _Pdf(format="A4")
    doc.set_auto_page_break(False)
    doc.set_margins(MARGIN, MARGIN, MARGIN)
    doc.set_title(_latin(project.title))
    width = doc.w - 2 * MARGIN

    def say(text, size, color=TEXT, style="", align="L", h=None, w=None, x=None, verb=False, font="Helvetica", measure=False):
        """Write (or just measure) wrapped text. ``verb`` sets the first word in bold."""
        text, markdown = _latin(text), verb
        if verb:
            first, rest = _split(text)
            text = f"{BOLD}{first}{BOLD} {rest}"
        doc.set_font(font, style, size)
        doc.set_text_color(*color)
        if x is not None:
            doc.set_x(x)
        line = h or size * 0.45
        if measure:
            return doc.multi_cell(w or width, line, text, align=align, markdown=markdown, dry_run=True,
                                  output=MethodReturnValue.HEIGHT)
        doc.multi_cell(w or width, line, text, align=align, markdown=markdown, new_x="LMARGIN", new_y="NEXT")

    def room(need):
        if doc.get_y() + need > doc.h - MARGIN:
            doc.add_page()
            doc.set_y(MARGIN)

    def panel(height):
        doc.set_fill_color(*PANEL)
        doc.rect(MARGIN, doc.get_y(), width, height, style="F", round_corners=True, corner_radius=4)

    def bullets(title, items):
        if not items:
            return
        inner = width - 16
        heights = [say(f"-  {t}", 11, w=inner, measure=True) + 2 for t, _ in items]
        room(sum(heights) + 22)
        top = doc.get_y()
        panel(sum(heights) + 20)
        doc.set_y(top + 7)
        say(title, 13, style="B", x=MARGIN + 8, w=inner)
        doc.ln(2)
        for (text, url), _h in zip(items, heights):
            doc.set_font("Helvetica", "", 11)
            doc.set_text_color(*(BLUE if url else TEXT))
            doc.set_x(MARGIN + 8)
            doc.multi_cell(inner, 11 * 0.45, _latin(f"-  {text}"), link=url or "", align="L", new_x="LMARGIN", new_y="NEXT")
            doc.ln(2)
        doc.set_y(top + sum(heights) + 24)

    # Cover
    doc.add_page()
    doc.set_y(24)
    say("Project guide", 11, ORANGE, "B", "C")
    doc.ln(3)
    say(project.title, 30, style="B", align="C", h=12)
    doc.ln(3)
    say(g.get("tagline"), 14, MUTED, align="C", h=7)
    doc.ln(7)
    facts = [(f"{g.get('hours')} hrs", "to finish")] if g.get("hours") else []
    facts += [(f"{len(project.roles)} of {project.total}", "of your applications")] if project.total else []
    facts += [(str(g.get("cost")), "cost")] if g.get("cost") else []
    top = doc.get_y()
    for i, (value, label) in enumerate(facts):
        x, w = MARGIN + i * width / len(facts), width / len(facts)
        doc.set_xy(x, top)
        say(value, 17 if len(value) < 12 else 12, style="B", align="C", w=w, x=x, h=8)
        doc.set_xy(x, top + 9)
        say(label, 9, MUTED, align="C", w=w, x=x)
    doc.set_y(top + 22)
    main = picture(g.get("diagram"), dark=True, width=640)
    if main:
        size = Image.open(io.BytesIO(main)).size
        h = (width - 30) * size[1] / size[0]
        doc.image(io.BytesIO(main), x=MARGIN + 15, y=doc.get_y(), w=width - 30)
        doc.set_y(doc.get_y() + h + 8)
    say(g.get("description"), 12, MUTED, h=6)
    doc.ln(3)
    say(WARNING, 10, ORANGE)
    doc.ln(6)
    if project.proves:
        bullets("What it proves", [(project.proves, "")])
    bullets("Roles this helps with", [(f"{r['company']}: {r['title']}", "") for r in project.roles])

    # Parts and steps
    parts = g.get("parts") or []
    current = -1
    for n, pi, s in steps(g):
        if pi != current:
            current = pi
            doc.add_page()
            doc.set_y(MARGIN + 4)
            hours = _hours(parts[pi].get("hours"))
            say(f"Part {pi + 1} of {len(parts)}" + (f"  -  about {hours} hours" if hours else ""), 10, MUTED, "B")
            doc.ln(1)
            say(parts[pi].get("title"), 24, style="B", h=10)
            doc.ln(5)
        pic = picture(s.get("diagram"), dark=True, width=320, bg=PANEL) if has_diagram(s.get("diagram")) else None
        text_w = width - 36 - (PIC_W + 6 if pic else 0)
        sentence = " ".join(str(s.get("text") or "").split())
        command = _latin(s.get("command")).strip()
        notes = [(f"OK: {s['expect']}", GREEN)] if str(s.get("expect") or "").strip() else []
        notes += [(f"If not: {s['if_not']}", ORANGE)] if str(s.get("if_not") or "").strip() else []
        h_text = say(sentence, 13, w=text_w, h=6.2, verb=True, measure=True)
        h_cmd = (say(command, 9, w=text_w - 6, h=4.4, font="Courier", measure=True) + 6) if command else 0
        h_notes = sum(say(t, 10, w=text_w, measure=True) + 1.5 for t, _ in notes)
        pic_h = 0
        if pic:
            size = Image.open(io.BytesIO(pic)).size
            pic_h = PIC_W * size[1] / size[0]
        body = h_text + (h_cmd + 3 if command else 0) + (h_notes + 2 if notes else 0)
        height = max(body, pic_h, 14) + 14
        room(height + 4)
        top = doc.get_y()
        panel(height)
        doc.set_xy(MARGIN + 6, top + 7)
        say(str(n), 24, MUTED, "B", w=20, x=MARGIN + 6, h=10)
        doc.set_xy(MARGIN + 28, top + 7)
        say(sentence, 13, w=text_w, x=MARGIN + 28, h=6.2, verb=True)
        if command:
            y = doc.get_y() + 3
            doc.set_fill_color(0, 0, 0)
            doc.rect(MARGIN + 28, y, text_w, h_cmd, style="F", round_corners=True, corner_radius=2)
            doc.set_xy(MARGIN + 31, y + 3)
            say(command, 9, w=text_w - 6, x=MARGIN + 31, h=4.4, font="Courier")
            doc.set_y(y + h_cmd)
        if notes:
            doc.ln(2)
            for t, color in notes:
                say(t, 10, color, w=text_w, x=MARGIN + 28)
                doc.ln(1.5)
        if pic:
            doc.image(io.BytesIO(pic), x=MARGIN + width - PIC_W - 6, y=top + (height - pic_h) / 2, w=PIC_W)
        doc.set_y(top + height + 4)

    # Close
    doc.add_page()
    doc.set_y(MARGIN + 4)
    say("When you finish.", 24, style="B", h=10)
    doc.ln(5)
    bullets("Resume lines you can now write", [(str(t), "") for t in g.get("resume_lines") or []])
    bullets("Read first if rusty", [(r["title"], r.get("url") or "") for r in g.get("reading") or []])
    bullets("If something goes wrong", [(f"{p.get('problem')} Cause: {p.get('cause')} Fix: {p.get('fix')}", "") for p in g.get("pitfalls") or []])
    return bytes(doc.output())


# ---------- Word ----------

def docx(project: Project) -> bytes:
    """The same guide as an editable, printable Word file (light, plain styles)."""
    g = project.guide
    doc = Document()
    doc.core_properties.title = project.title
    doc.add_heading(project.title, level=0)
    if g.get("tagline"):
        doc.add_paragraph(str(g["tagline"])).runs[0].italic = True
    facts = [f"About {g['hours']} hours" if g.get("hours") else "", str(g.get("difficulty") or ""), str(g.get("cost") or ""),
             f"Helps with {len(project.roles)} of {project.total} applications" if project.total else ""]
    doc.add_paragraph(" · ".join(f for f in facts if f))
    main = picture(g.get("diagram"), dark=False, width=640)
    if main:
        doc.add_picture(io.BytesIO(main), width=Inches(5.5))
    if g.get("description"):
        doc.add_paragraph(str(g["description"]))
    doc.add_paragraph().add_run(WARNING).bold = True
    if project.proves:
        doc.add_heading("What it proves", level=2)
        doc.add_paragraph(project.proves)
    if project.roles:
        doc.add_heading("Roles this helps with", level=2)
        for r in project.roles:
            doc.add_paragraph(f"{r['company']}: {r['title']}", style="List Bullet")

    parts = g.get("parts") or []
    current = -1
    for n, pi, s in steps(g):
        if pi != current:
            current = pi
            hours = _hours(parts[pi].get("hours"))
            doc.add_heading(f"Part {pi + 1}: {parts[pi].get('title')}" + (f" (about {hours} hours)" if hours else ""), level=1)
        verb, rest = _split(s.get("text"))
        para = doc.add_paragraph()
        para.add_run(f"{n}. {verb}").bold = True
        para.add_run(f" {rest}")
        if str(s.get("command") or "").strip():
            run = doc.add_paragraph().add_run(str(s["command"]).strip())
            run.font.name, run.font.size = "Courier New", Pt(9)
        for label, key, color in (("You should see: ", "expect", RGBColor(0x1D, 0x8A, 0x3C)), ("If not: ", "if_not", RGBColor(0xBF, 0x48, 0x00))):
            if str(s.get(key) or "").strip():
                run = doc.add_paragraph().add_run(label + str(s[key]).strip())
                run.font.color.rgb, run.font.size = color, Pt(10)
        if has_diagram(s.get("diagram")):
            doc.add_picture(io.BytesIO(picture(s["diagram"], dark=False, width=320)), width=Inches(2.8))

    doc.add_heading("When you finish", level=1)
    for title, items in (("Resume lines you can now write", [str(t) for t in g.get("resume_lines") or []]),
                         ("Read first if rusty", [f"{r['title']} {r.get('url') or ''}".strip() for r in g.get("reading") or []]),
                         ("If something goes wrong", [f"{p.get('problem')} Cause: {p.get('cause')} Fix: {p.get('fix')}" for p in g.get("pitfalls") or []])):
        if items:
            doc.add_heading(title, level=2)
            for item in items:
                doc.add_paragraph(item, style="List Bullet")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# ---------- AI helper ----------

def helper_md(project: Project) -> str:
    """A briefing to give an AI assistant, so it can help with this project without guessing."""
    g = project.guide
    parts = g.get("parts") or []
    lines = [f"# AI helper: {project.title}", "",
             "Give this file to an AI assistant when you want help with the project.", "",
             "## Instructions for the assistant", "",
             "You are helping someone build the project below by following its numbered steps.", "",
             "- Ask which step they are on and what they see, before suggesting anything.",
             "- Diagnose first: compare what they see with that step's expected result and the pitfalls below.",
             "- Keep to the guide's design. Don't skip ahead, swap tools or redesign the project unless they ask.",
             "- Explain why a fix works, briefly. They know the fundamentals; skip the basics.",
             "- Everything stays inside the lab this guide builds. Never suggest running any of it against systems they don't own.",
             "- Don't write their resume lines for them until the project works.",
             "- The guide's commands were written by AI. If one looks wrong or unsafe, say so before they run it.", "",
             f"Note for the builder: {WARNING}", "",
             "## The project", "", str(g.get("description") or ""), "",
             f"- Time: about {g.get('hours')} hours", f"- Level: {g.get('difficulty')}", f"- Cost: {g.get('cost')}", ""]
    nodes = _nodes(g.get("diagram"))
    if nodes:
        names = {n["id"]: n["label"] for n in nodes}
        lines += ["## Finished setup", "", "Parts: " + ", ".join(names.values()), ""]
        lines += [f"- {names[str(l.get('a'))]} connects to {names[str(l.get('b'))]}" for l in g["diagram"].get("links") or []
                  if isinstance(l, dict) and str(l.get("a")) in names and str(l.get("b")) in names] + [""]
    current = -1
    lines += ["## Steps", ""]
    for n, pi, s in steps(g):
        if pi != current:
            current = pi
            lines += [f"### Part {pi + 1}: {parts[pi].get('title')}", ""]
        lines.append(f"{n}. {' '.join(str(s.get('text') or '').split())}")
        if str(s.get("command") or "").strip():
            lines += ["", "   ```", *[f"   {c}" for c in str(s["command"]).strip().replace("```", "'''").splitlines()], "   ```", ""]
        if str(s.get("expect") or "").strip():
            lines.append(f"   - Expected: {s['expect']}")
        if str(s.get("if_not") or "").strip():
            lines.append(f"   - If not: {s['if_not']}")
    if g.get("pitfalls"):
        lines += ["", "## Known pitfalls", ""]
        lines += [f"- **{p.get('problem')}** Cause: {p.get('cause')} Fix: {p.get('fix')}" for p in g["pitfalls"]]
    return "\n".join(lines) + "\n"


def files(project: Project) -> list[tuple[str, bytes]]:
    """The three attachments: (filename, contents)."""
    name = clean_name(project.title)
    return [(f"Guide - {name}.pdf", pdf(project)), (f"Guide - {name}.docx", docx(project)),
            (f"AI helper - {name}.md", helper_md(project).encode("utf-8"))]
