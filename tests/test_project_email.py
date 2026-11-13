import copy
import json
import logging
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from pathlib import Path

from jobscanner import __main__ as main_mod
from jobscanner import emailer, projects, tracker
from jobscanner.__main__ import project_preview, run
from jobscanner.ai import Assistant
from jobscanner.projects import SAMPLE

from .test_main import CONFIG, paths, site  # noqa: F401 - fixtures
from .test_projects import GAPS, FakeAssistant, Web

SUNDAY = datetime(2026, 11, 15, 23, 30, tzinfo=timezone.utc)     # Sunday 6:30 PM Eastern
ENV = {"TRACKER_URL": "https://script.google.com/macros/s/abc/exec", "TRACKER_TOKEN": "secret"}


def test_email_shows_computed_counts_and_escapes_everything():
    p = copy.deepcopy(SAMPLE)
    p.guide["title"] = 'Lab <script>alert(1)</script>'
    p.guide["reading"] = [{"title": "Good <b>", "url": "https://ok.example/a?x=1&y=2"}, {"title": "Bad", "url": "javascript:alert(1)"}]
    p.roles = [{"company": f"Co{i} <i>", "title": "Intern"} for i in range(8)]
    p.total = 11
    subject, text, html = emailer.render_project(p, "https://script.google.com/x?t=1&a=guide", True, ["Guide.pdf"])
    assert subject == "🛠 Project to build: Lab <script>alert(1)</script>"
    assert "<script>" not in html and "&lt;script&gt;" in html and "<i>" not in html and "<b>" not in html
    assert "8 of 11" in html and "8 of 11 of your applications" in text and "and 2 more" in html
    assert 'href="https://ok.example/a?x=1&amp;y=2"' in html and "javascript:" not in html.replace("javascript:alert(1)", "", 0).split("Read first")[0]
    assert 'href="javascript' not in html
    assert 'href="https://script.google.com/x?t=1&amp;a=guide"' in html and "cid:project-main" in html
    assert "named in 5 of your 11 applications" in html and "Certification: CCNA" in text
    assert "Open the instructions: https://script.google.com/x?t=1&a=guide" in text
    _, _, bare = emailer.render_project(p, "", False)
    assert "Open the instructions" not in bare and "cid:" not in bare


def test_send_attaches_files_by_type_and_embeds_the_picture(monkeypatch):
    sent = []

    class Smtp:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(emailer.smtplib, "SMTP", Smtp)
    emailer.send("s", "text", '<img src="cid:project-main">', attachments=[("Guide - x.pdf", b"%PDF-1"), ("Guide - x.docx", b"PK"),
                 ("AI helper - x.md", b"# hi"), ("Draft.txt", "plain")], inline=[("project-main", b"\x89PNG")],
                 env={"GMAIL_ADDRESS": "me@example.com", "GMAIL_APP_PASSWORD": "pw"})
    msg = message_from_bytes(sent[0].as_bytes(), policy=policy.default)
    types = {part.get_filename(): part.get_content_type() for part in msg.iter_attachments()}
    assert types == {"Guide - x.pdf": "application/pdf", "AI helper - x.md": "text/markdown", "Draft.txt": "text/plain",
                     "Guide - x.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    pictures = [part for part in msg.walk() if part.get_content_type() == "image/png"]
    assert len(pictures) == 1 and pictures[0]["Content-ID"] == "<project-main>"


class Outbox(list):
    def __call__(self, subject, text, html, attachments=(), inline=()):
        self.append({"subject": subject, "text": text, "html": html, "attachments": list(attachments), "inline": list(inline)})


def helper(*replies):
    """A real Assistant (so the run accepts it) whose requests are answered by a fake."""
    fake = FakeAssistant(*replies)
    real = Assistant(client=None, resume="Jane Doe resume")
    real.structured = fake.structured
    real.score = real.draft = lambda posting: None       # matches in these runs go unscored
    real.calls = fake.calls
    return real


def applied(n=3):
    return tracker.Marks(loaded=True, rows=[
        {"status": "applied", "id": f"x/{i}", "company": f"Co{i}", "title": "Network Security Intern", "link": "",
         "posting": "Needs 802.1X and BGP.", "marked": SUNDAY - timedelta(days=i)} for i in range(n)])


def setup(monkeypatch, paths, marks, past=(), saved=True, extra="projects: {day: sunday, hour: 18}\n"):
    Path(paths[0]).write_text(CONFIG + extra)
    saves = []
    monkeypatch.setattr(tracker, "load", lambda session, env=None, full=False: marks)
    monkeypatch.setattr(tracker, "guides", lambda session: None if past is None else [{"sent": "", "title": t} for t in past])
    monkeypatch.setattr(tracker, "push_guide", lambda session, gid, guide: saves.append((gid, guide)) or saved)
    monkeypatch.setattr(tracker, "guide_url", lambda gid: f"https://script.google.com/x?a=guide&id={gid}")
    return saves


def test_project_email_goes_out_once_on_its_day(site, paths, monkeypatch, caplog):
    saves = setup(monkeypatch, paths, applied(), past=["Earlier project"])
    outbox, ai = Outbox(), helper(GAPS, SAMPLE.guide)
    with caplog.at_level(logging.INFO):
        assert run(*paths, session=Web(set()), sender=outbox, now=SUNDAY, assistant=ai) == 0
    mail = outbox[-1]
    assert mail["subject"] == "🛠 Project to build: Zero-trust campus network"
    assert [n.rsplit(".", 1)[1] for n, _ in mail["attachments"]] == ["pdf", "docx", "md"]
    assert mail["inline"][0][0] == "project-main" and mail["inline"][0][1].startswith(b"\x89PNG")
    assert "id=2026-11-15-zero-trust-campus-network" in mail["html"] and "2 of 3" in mail["html"]
    assert saves[0][0] == "2026-11-15-zero-trust-campus-network" and saves[0][1]["title"] == "Zero-trust campus network"
    assert "- Earlier project" in ai.calls[0][1]
    # Nothing about the project reaches the state file or the logs: both may be public.
    state = Path(paths[1]).read_text()
    assert json.loads(state)["project"] == {"on": "2026-11-15", "tries": 1, "sent": True}
    for secret in ("Zero-trust", "802.1X", "CCNA", "Jane Doe"):
        assert secret not in state and secret not in caplog.text
    assert "sent the weekly project guide" in caplog.text
    run(*paths, session=Web(set()), sender=outbox, now=SUNDAY + timedelta(minutes=30), assistant=helper(GAPS, SAMPLE.guide))
    assert sum(m["subject"].startswith("🛠") for m in outbox) == 1


def test_project_waits_for_its_day_and_hour(site, paths, monkeypatch):
    setup(monkeypatch, paths, applied())
    outbox, ai = Outbox(), helper(GAPS, SAMPLE.guide)
    run(*paths, session=Web(set()), sender=outbox, now=SUNDAY - timedelta(days=1), assistant=ai)      # Saturday
    run(*paths, session=Web(set()), sender=outbox, now=SUNDAY - timedelta(hours=3), assistant=ai)     # Sunday 3:30 PM
    assert outbox == [] and ai.calls == []


def test_project_is_off_without_its_config_section(site, paths, monkeypatch):
    setup(monkeypatch, paths, applied(), extra="")
    ai = helper(GAPS, SAMPLE.guide)
    run(*paths, session=Web(set()), sender=Outbox(), now=SUNDAY, assistant=ai)
    assert ai.calls == [] and json.loads(Path(paths[1]).read_text())["project"] == {}


def test_too_few_applications_or_missing_pieces_skip_quietly(site, paths, monkeypatch, caplog):
    setup(monkeypatch, paths, applied(2))
    outbox, ai = Outbox(), helper(GAPS, SAMPLE.guide)
    with caplog.at_level(logging.INFO):
        run(*paths, session=Web(set()), sender=outbox, now=SUNDAY, assistant=ai)
        run(*paths, session=Web(set()), sender=outbox, now=SUNDAY + timedelta(minutes=30), assistant=ai)
    assert outbox == [] and ai.calls == [] and caplog.text.count("2 applied-to jobs, needs 3") == 1
    Path(paths[1]).unlink()
    setup(monkeypatch, paths, applied(), past=None)                 # the Sheet can't list past guides
    with caplog.at_level(logging.INFO):
        run(*paths, session=Web(set()), sender=outbox, now=SUNDAY, assistant=ai)
        assert outbox == [] and ai.calls == [] and "needs the tracker" in caplog.text
        Path(paths[1]).unlink()
        run(*paths, session=Web(set()), sender=outbox, now=SUNDAY, assistant=None)      # no Claude secrets
    assert outbox == [] and "needs the ANTHROPIC_API_KEY and RESUME_TEXT secrets" in caplog.text


def test_a_failed_guide_is_retried_three_times_then_left_for_next_week(site, paths, monkeypatch):
    setup(monkeypatch, paths, applied())
    outbox, ai = Outbox(), helper()                                  # every request comes back empty
    for i in range(5):
        assert run(*paths, session=Web(set()), sender=outbox, now=SUNDAY + timedelta(minutes=30 * i), assistant=ai) == 0
    assert outbox == [] and len(ai.calls) == projects.MAX_TRIES_PER_DAY
    assert json.loads(Path(paths[1]).read_text())["project"] == {"on": "2026-11-15", "tries": 3, "sent": False}


def test_project_trouble_never_stops_the_alerts(site, paths, monkeypatch, caplog):
    from .test_main import posting
    setup(monkeypatch, paths, applied())
    site["cs"] = [posting("nvidia", company="NVIDIA", posted_at=SUNDAY - timedelta(minutes=5))]
    monkeypatch.setattr(main_mod.guide, "files", lambda project: (_ for _ in ()).throw(RuntimeError("Zero-trust blew up")))
    outbox = Outbox()
    with caplog.at_level(logging.INFO):
        assert run(*paths, session=Web(set()), sender=outbox, now=SUNDAY, assistant=helper(GAPS, SAMPLE.guide)) == 0
    assert len(outbox) == 1 and "NVIDIA" in outbox[0]["text"]
    assert "project guide: failed (RuntimeError)" in caplog.text and "Zero-trust" not in caplog.text


def test_email_still_goes_without_a_link_when_the_sheet_cannot_save(site, paths, monkeypatch):
    setup(monkeypatch, paths, applied(), saved=False)
    outbox = Outbox()
    run(*paths, session=Web(set()), sender=outbox, now=SUNDAY, assistant=helper(GAPS, SAMPLE.guide))
    assert "Open the instructions" not in outbox[-1]["html"] and len(outbox[-1]["attachments"]) == 3


def test_preview_writes_the_sample_without_secrets(tmp_path, paths, monkeypatch, caplog):
    out = tmp_path / "preview"
    with caplog.at_level(logging.INFO):
        assert project_preview(paths[0], str(out), session=object(), assistant=None) == 0
    names = sorted(f.name for f in out.iterdir())
    assert names == ["AI helper - Zero-trust campus network.md", "Guide - Zero-trust campus network.docx",
                     "Guide - Zero-trust campus network.pdf", "email.html", "email.txt", "main.png"]
    assert 'src="main.png"' in (out / "email.html").read_text() and "Sample project" in (out / "email.html").read_text()
    assert (out / "email.txt").read_text().startswith("Subject: [Sample] 🛠 Project to build: Zero-trust campus network")


def test_preview_builds_a_real_one_when_it_can(tmp_path, paths, monkeypatch):
    setup(monkeypatch, paths, applied())
    out = tmp_path / "preview"
    project_preview(paths[0], str(out), session=Web(set()), assistant=helper(GAPS, SAMPLE.guide), now=SUNDAY)
    assert "New project for you" in (out / "email.html").read_text() and "2 of 3" in (out / "email.html").read_text()
