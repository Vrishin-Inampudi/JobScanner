import copy
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobscanner import emailer, papers, tracker
from jobscanner.__main__ import paper_preview, run
from jobscanner.ai import Assistant
from jobscanner.papers import (LABS, READING_LIST, SAMPLE_PAPER, SAMPLE_QUIZ, Lab, Paper, fresh, lab_items, make_quiz,
                               parse_arxiv, parse_feed, parse_links, pick, problems, read, worthwhile)

from .test_main import CONFIG, paths, site  # noqa: F401 - fixtures

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)        # Wednesday 10 AM Eastern
ENV = {"TRACKER_URL": "https://script.google.com/macros/s/abc/exec", "TRACKER_TOKEN": "secret"}
P0 = Lab("Google Project Zero", "https://projectzero.google/feed.xml", ("projectzero.google",))
RED = Lab("Anthropic Frontier Red Team", "https://www.anthropic.com/research/team/frontier-red-team", ("www.anthropic.com",), "/research/")

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title type="html">Fuzzers vs. AI &amp;amp; more</title><link href="https://projectzero.google/2026/11/fuzz.html" rel="alternate"/>
  <published>2026-11-16T00:00:00-08:00</published><summary type="html">&lt;p&gt;We compared two tools.&lt;/p&gt;</summary></entry>
<entry><title>Old post</title><link href="https://projectzero.google/2026/10/old.html"/><published>2026-10-01T00:00:00Z</published></entry>
<entry><title>Elsewhere</title><link href="https://evil.example/x"/><published>2026-11-17T00:00:00Z</published></entry>
<entry><title>No date</title><link href="https://projectzero.google/2026/11/nodate.html"/></entry>
</feed>"""
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title><![CDATA[Hardening agents against injection]]></title><link>http://openai.com/index/hardening</link>
  <pubDate>Tue, 17 Nov 2026 15:00:00 GMT</pubDate><description>How we limit harm.</description></item>
<item><title>Introducing Wallet</title><link>https://openai.com/index/wallet</link><pubDate>Mon, 16 Nov 2026 15:00:00 GMT</pubDate></item>
</channel></rss>"""
PAGE = b'<a href="/research/zero-days">x</a><a href="/research/team/frontier-red-team">t</a><a href="/research/exploit-evals">y</a>'
ARXIV = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2611.01111v1</id><published>2026-11-02T10:00:00Z</published><title>Breaking  BGP
 Security</title><summary>We attack routing.</summary><author><name>A One</name></author><author><name>B Two</name></author>
 <author><name>C Three</name></author><author><name>D Four</name></author>
 <arxiv:comment>Accepted at USENIX Security 2027</arxiv:comment><category term="cs.CR"/><category term="cs.NI"/></entry>
<entry><id>http://arxiv.org/abs/2610.02222v2</id><published>2026-10-20T10:00:00Z</published><title>Latent Agents</title>
 <summary>Not security.</summary><author><name>E Five</name></author><category term="cs.LG"/></entry>
<entry><id>http://arxiv.org/abs/2611.03333v1</id><published>2026-11-05T10:00:00Z</published><title>A Quiet Networking Paper</title>
 <summary>Measurements.</summary><author><name>F Six</name></author><arxiv:comment>12 pages</arxiv:comment><category term="cs.NI"/></entry>
</feed>"""


class Reply:
    def __init__(self, body=b"", kind="text/html", status=200, data=None):
        self.content, self.headers, self.status_code, self._data = body, {"Content-Type": kind}, status, data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")

    def iter_content(self, n):
        yield self.content

    def json(self):
        return self._data

    def close(self):
        pass


class Web:
    """A fake internet: ``pages`` maps a URL prefix to a Reply (or an exception to raise)."""

    def __init__(self, pages=None):
        self.pages, self.asked = pages or {}, []

    def get(self, url, params=None, **kw):
        self.asked.append(url)
        for prefix, reply in self.pages.items():
            if url.startswith(prefix):
                if isinstance(reply, Exception):
                    raise reply
                return reply(params) if callable(reply) else reply
        return Reply(status=404)

    def post(self, url, json=None, timeout=None):
        return Reply(data={"ok": True})


class FakeReader(Assistant):
    """A real Assistant (the run checks its type) answering ``ask`` from canned replies, keyed by what's asked."""

    def __init__(self, judge=None, quiz=None):
        super().__init__(client=None, resume="")
        self.judge, self.quiz, self.calls = judge, list(quiz) if isinstance(quiz, list) else [quiz] * 9, []

    def ask(self, what, instructions, content, schema, small=False, max_tokens=16000):
        self.calls.append((what, instructions, content, small))
        if what == "paper screening":
            n = content.count("\n") - 1
            return self.judge(content) if callable(self.judge) else {"items": [{"n": i, "research": True, "relevant": True} for i in range(1, n + 1)]} if self.judge else {"items": []}
        return copy.deepcopy(self.quiz.pop(0)) if self.quiz else None


def test_reading_list_is_well_formed_and_takes_turns():
    assert len({p.id for p in READING_LIST}) == len(READING_LIST) >= 18
    assert all(p.url.startswith("https://") and p.lane == "core" and p.title and p.authors for p in READING_LIST)
    assert [p.source.split(" reading")[0] for p in READING_LIST[:6]] == ["Security engineering", "Trading networks", "Satellite networks"] * 2
    assert READING_LIST[0].ids == ["core:camel", "arxiv:2503.18813"]
    assert READING_LIST[1].sheet_id("2026-11-18") == "2026-11-18-network-design-considerations-for-trading-systems"


def test_feeds_keep_only_dated_items_on_the_labs_own_site():
    items = parse_feed(ATOM, P0)
    assert [p.title for p in items] == ["Fuzzers vs. AI & more", "Old post"]
    assert items[0].id == "lab:projectzero.google/2026/11/fuzz.html" and items[0].blurb == "We compared two tools." and items[0].lane == "lab"
    rss = parse_feed(RSS, Lab("OpenAI", "https://openai.com/news/rss.xml", ("openai.com",)))
    assert rss[0].url == "https://openai.com/index/hardening" and rss[0].published == datetime(2026, 11, 17, 15, tzinfo=timezone.utc)
    with pytest.raises(Exception):
        parse_feed(b'<!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>', P0)


def test_a_page_without_a_feed_gives_its_article_links():
    assert [p.url for p in parse_links(PAGE, RED)] == ["https://www.anthropic.com/research/zero-days", "https://www.anthropic.com/research/exploit-evals"]
    assert parse_links(PAGE, RED)[0].title == "Zero days"


def test_lab_items_are_new_since_last_time_and_survive_one_lab_being_down(caplog):
    web = Web({P0.url: Reply(ATOM), RED.url: Reply(PAGE)})
    since = NOW - timedelta(days=7)
    items, links = lab_items(web, since, None, labs=(RED, P0))                    # first look: the page's links are only recorded
    assert [p.title for p in items] == ["Fuzzers vs. AI & more"] and links == {"lab:www.anthropic.com/research/zero-days", "lab:www.anthropic.com/research/exploit-evals"}
    items, links = lab_items(web, since, {"lab:www.anthropic.com/research/zero-days"}, labs=(RED, P0))
    assert [p.title for p in items] == ["Exploit evals", "Fuzzers vs. AI & more"] and len(links) == 2
    down = Web({P0.url: Reply(ATOM), RED.url: OSError("boom")})
    items, links = lab_items(down, since, {"lab:www.anthropic.com/research/zero-days"}, labs=(RED, P0))
    assert [p.title for p in items] == ["Fuzzers vs. AI & more"] and links == {"lab:www.anthropic.com/research/zero-days"}
    assert "couldn't read Anthropic Frontier Red Team (OSError)" in caplog.text
    assert lab_items(Web(), since, None, labs=(RED,)) == ([], None)                # never seen and unreachable: still "first look" next time


def test_arxiv_reply_is_parsed():
    got = parse_arxiv(ARXIV)
    assert got["2611.01111"]["title"] == "Breaking BGP Security" and got["2611.01111"]["authors"] == "A One, B Two, C Three et al."
    assert got["2611.01111"]["comment"] == "Accepted at USENIX Security 2027" and got["2610.02222"]["categories"] == ["cs.LG"]


def hits(*rows):
    return Reply(data={"hits": [{"url": u, "points": p, "title": t} for u, p, t in rows]})


def test_fresh_ranks_by_attention_and_venue_and_rejects_old_or_off_topic_papers():
    hn = hits(("https://arxiv.org/abs/2611.03333", 90, "A Quiet Networking Paper"), ("https://arxiv.org/pdf/2610.02222v2", 520, "Latent Agents"),
              ("https://arxiv.org/abs/2409.02292", 222, "An old paper, discussed again"), ("https://arxiv.org/abs/2611.03333", 40, "dup"),
              ("https://example.com/x", 900, "not arxiv"), ("https://arxiv.org/abs/2611.09999", 30, "Sent already"))
    web, naps = Web({"https://hn.algolia.com": hn, "https://export.arxiv.org": Reply(ARXIV)}), []
    got = fresh(web, NOW, {"arxiv:2611.09999"}, pause=naps.append)
    assert [p.id for p in got] == ["arxiv:2611.01111", "arxiv:2611.03333"]        # venue acceptance (150) outranks 90 points
    assert naps == [papers.ARXIV_PAUSE] and got[0].text_url == "https://arxiv.org/pdf/2611.01111"
    assert "USENIX Security 2027" in got[0].source and "90 points on Hacker News" in got[1].source and got[1].authors == "F Six"


def test_fresh_still_works_when_arxiv_or_hacker_news_is_down(caplog):
    hn = hits(("https://arxiv.org/abs/2611.03333", 90, "A Quiet Networking Paper"))
    got = fresh(Web({"https://hn.algolia.com": hn, "https://export.arxiv.org": Reply(status=429)}), NOW, set(), pause=lambda s: None)
    assert [(p.id, p.title, p.authors) for p in got] == [("arxiv:2611.03333", "A Quiet Networking Paper", "")]
    got = fresh(Web({"https://hn.algolia.com": OSError("x"), "https://export.arxiv.org": Reply(ARXIV)}), NOW, set(), pause=lambda s: None)
    assert [p.id for p in got] == ["arxiv:2611.01111"] and "couldn't read Hacker News" in caplog.text


def test_worthwhile_keeps_what_the_small_model_calls_relevant_research():
    items = [Paper(id=f"lab:{i}", title=t, url="https://x", source="Lab") for i, t in enumerate(["Wallet launch", "Fuzzing study", "Robot demo"])]
    reader = FakeReader(judge=lambda c: {"items": [{"n": 1, "research": False, "relevant": True}, {"n": 2, "research": True, "relevant": True},
                                                    {"n": 3, "research": True, "relevant": False}, {"n": 9, "research": True, "relevant": True}]})
    assert [p.title for p in worthwhile(reader, items, papers.FOCUS)] == ["Fuzzing study"]
    what, system, content, small = reader.calls[0]
    assert small and "<items>\n1. Wallet launch" in content and "quantitative trading" in system
    assert worthwhile(FakeReader(), [], papers.FOCUS) == [] and worthwhile(FakeReader(judge=lambda c: None), items, papers.FOCUS) == []


def lab_web():
    return Web({P0.url: Reply(ATOM), "https://hn.algolia.com": hits(("https://arxiv.org/abs/2611.03333", 90, "A Quiet Networking Paper")),
                "https://export.arxiv.org": Reply(ARXIV)})


def test_pick_prefers_a_lab_release_then_the_reading_list_then_fresh(monkeypatch):
    monkeypatch.setattr(papers, "LABS", (P0,))
    monkeypatch.setattr(papers.lab_items, "__defaults__", ((P0,),))
    memory = {}
    first = pick(lab_web(), FakeReader(judge=True), memory, NOW, pause=lambda s: None)
    assert first.id == "lab:projectzero.google/2026/11/fuzz.html"
    memory["sent"] = first.ids
    assert pick(lab_web(), FakeReader(judge=True), memory, NOW, pause=lambda s: None).id == "core:camel"       # no repeat
    assert pick(lab_web(), FakeReader(judge=False), {}, NOW, pause=lambda s: None).id == "core:camel"          # a product story doesn't count
    memory["sent"] += [i for p in READING_LIST for i in p.ids]
    assert pick(lab_web(), FakeReader(judge=True), memory, NOW, pause=lambda s: None).id == "arxiv:2611.01111"
    memory["sent"] += ["arxiv:2611.01111", "arxiv:2611.03333"]
    assert pick(lab_web(), FakeReader(judge=True), memory, NOW, pause=lambda s: None) is None
    old = {"last_at": (NOW - timedelta(hours=1)).isoformat()}
    assert pick(lab_web(), FakeReader(judge=True), old, NOW, pause=lambda s: None).id == "core:camel"          # released before the last email


def test_read_gives_a_pdf_or_page_text_and_refuses_what_it_should(caplog):
    pdf = read(Web({"https://arxiv.org/pdf/": Reply(b"%PDF-1.7 data", "application/pdf")}), READING_LIST[0])
    assert pdf[0]["type"] == "document" and pdf[0]["source"]["media_type"] == "application/pdf" and pdf[0]["source"]["data"] == "JVBERi0xLjcgZGF0YQ=="
    html = b"<html><head><title>Zero days | Anthropic</title><script>var x = 'SECRET'</script></head><body><p>" + b"Finding bugs. " * 200 + b"</p></body></html>"
    lab = Paper(id="lab:x", title="Zero days", url="https://www.anthropic.com/research/zero-days", source="Anthropic", lane="lab", hosts=("www.anthropic.com",))
    text = read(Web({"https://www.anthropic.com": Reply(html)}), lab)
    assert text[0]["text"].startswith("<paper>\n") and "Finding bugs." in text[0]["text"] and "SECRET" not in text[0]["text"]
    assert read(Web({"https://www.anthropic.com": Reply(b"<p>Sign in</p>")}), lab) is None                    # a stub
    web = Web()
    off_site = Paper(id="lab:y", title="t", url="https://evil.example/a", source="Lab", lane="lab", hosts=("www.anthropic.com",))
    assert read(web, off_site) is None and web.asked == []                                                     # never requested
    assert read(Web({"https://arxiv.org/pdf/": Reply(b"%PDF-" + b"x" * (papers.MAX_FETCH_BYTES + 1), "application/pdf")}), READING_LIST[0]) is None
    assert read(Web(), READING_LIST[0]) is None and "couldn't fetch the paper" in caplog.text


def test_redirects_are_followed_only_within_the_papers_own_site(caplog):
    pdf = Reply(b"%PDF-1.7", "application/pdf")

    def moved(to, status=302):
        r = Reply(status=status)
        r.headers["Location"] = to
        return r

    ok = Web({"https://arxiv.org/pdf/2503.18813v2": pdf, "https://arxiv.org/pdf/2503.18813": moved("/pdf/2503.18813v2")})   # longest first: the fake matches by prefix
    assert read(ok, READING_LIST[0])[0]["type"] == "document"
    lab = Paper(id="lab:x", title="t", url="https://www.anthropic.com/research/a", source="Anthropic", lane="lab", hosts=("www.anthropic.com",))
    page = Reply(b"<p>" + b"Finding bugs. " * 200 + b"</p>")
    assert read(Web({"https://www.anthropic.com/research/a": moved("https://www.anthropic.com/research/b"), "https://www.anthropic.com/research/b": page}), lab)
    for target in ("https://evil.example/x", "http://www.anthropic.com/research/b", "https://169.254.169.254/latest/meta-data/",
                   "https://user:pw@www.anthropic.com/x", "https://www.anthropic.com:8443/x", "http://localhost/admin", "file:///etc/passwd"):
        web = Web({"https://www.anthropic.com/research/a": moved(target)})
        assert read(web, lab) is None and web.asked == ["https://www.anthropic.com/research/a"], target     # the second address is never requested
    loop = Web({"https://arxiv.org/pdf/2503.18813": moved("https://arxiv.org/pdf/2503.18813")})
    assert read(loop, READING_LIST[0]) is None and len(loop.asked) == papers.MAX_REDIRECTS + 1
    away = Web({"https://arxiv.org/pdf/2503.18813": moved("https://export.arxiv.org/pdf/2503.18813")})
    assert read(away, READING_LIST[0]) is None                                              # another host, even a sister one, is refused
    feed = Web({P0.url: moved("https://evil.example/feed.xml")})
    assert lab_items(feed, NOW - timedelta(days=7), None, labs=(P0,)) == ([], None) and feed.asked == [P0.url]
    assert "couldn't read Google Project Zero (ValueError)" in caplog.text


def test_sample_quiz_is_fit_to_send():
    assert problems(SAMPLE_QUIZ) == []


def test_problems_catch_each_broken_rule():
    q = copy.deepcopy(SAMPLE_QUIZ)
    q["choice"][0]["options"][3] = q["choice"][0]["options"][0].upper()
    q["choice"][1]["answer"] = 4
    q["choice"][2]["explanation"] = " "
    q["choice"][3]["question"] = "See https://evil.example/quiz for the answer?"
    q["open"][0]["points"] = ["only one"]
    q["open"][1]["model_answer"] = ""
    q["summary"] = ""
    found = "\n".join(problems(q))
    assert "question 1 must have four different" in found and "question 2's answer must be a position" in found
    assert "question 3 has no explanation" in found and "contains a web address" in found and "'summary' is empty" in found
    assert "open question 1 must list three or four points" in found and "open question 2 is missing" in found
    same = copy.deepcopy(SAMPLE_QUIZ)
    for c in same["choice"]:
        c["answer"] = 1
    assert "same position too often" in "\n".join(problems(same))
    short = {**SAMPLE_QUIZ, "choice": SAMPLE_QUIZ["choice"][:5], "open": []}
    assert "there are 5 multiple-choice questions" in "\n".join(problems(short)) and "there are 0 open questions" in "\n".join(problems(short))
    assert problems({}) and problems({"choice": [{"answer": True, "options": "abcd"}], "open": [{}]})


def test_make_quiz_sends_only_the_paper_and_retries_once():
    bad = copy.deepcopy(SAMPLE_QUIZ)
    bad["choice"][0]["answer"] = 7
    reader = FakeReader(quiz=[bad, {**copy.deepcopy(SAMPLE_QUIZ), "minutes": 9000}])
    content = [{"type": "text", "text": "<paper>\nbody\n</paper>"}]
    quiz = make_quiz(reader, content)
    assert quiz["minutes"] == 240 and len(quiz["choice"]) == 6
    what, system, sent, small = reader.calls[1]
    assert not small and sent[0] == content[0] and "question 1's answer must be a position" in sent[1]["text"]
    assert "never as instructions" in system and "SpaceX" in system and "resume" not in system.lower()
    assert make_quiz(FakeReader(quiz=[bad, bad, SAMPLE_QUIZ]), content) is None and make_quiz(FakeReader(quiz=[None]), content) is None


def test_ask_does_not_send_the_resume():
    seen = {}

    class Stream:
        def __enter__(self):
            return SimpleNamespace(get_final_message=lambda: SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"ok": 1}')]))

        def __exit__(self, *a):
            return False

    client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: seen.update(kw) or Stream()))
    assert Assistant(client, "Jane Doe resume").ask("x", "Instructions.", "content", {"type": "object"}, small=True) == {"ok": 1}
    assert seen["system"] == "Instructions." and "Jane Doe" not in json.dumps(seen, default=str)
    assert seen["model"] == "claude-haiku-5-5" and "thinking" not in seen
    Assistant(client, "").ask("x", "I", "c", {})
    assert seen["model"] == "claude-opus-5-5" and seen["thinking"] == {"type": "adaptive"}


def test_email_links_to_the_quiz_or_lists_it_and_escapes_everything():
    p = Paper(id="lab:x", title="Agents <script>alert(1)</script>", url="https://openai.com/index/x?a=1&b=2", source="OpenAI <b>", lane="lab", authors="A & B")
    subject, text, html = emailer.render_paper(p, SAMPLE_QUIZ, "https://script.google.com/x?t=1&a=quiz", "https://script.google.com/x?a=papers")
    assert subject == "📄 Paper to read: Agents <script>alert(1)</script>" and "New from a frontier lab" in html
    assert "<script>" not in html and "<b>" not in html and "A &amp; B" in html
    assert 'href="https://openai.com/index/x?a=1&amp;b=2"' in html and 'href="https://script.google.com/x?t=1&amp;a=quiz"' in html
    assert "Practice questions" not in html and "Why is exchange market data" not in html and "Take the quiz: https://script" in text
    assert "35 min" in html and ">8<" in html and "All your papers" in html
    _, text, html = emailer.render_paper(SAMPLE_PAPER, SAMPLE_QUIZ)
    assert "Take the quiz" not in html and "Practice questions." in html and "Answers." in html
    assert html.index("Practice questions.") < html.index("8. You are asked to choose") < html.index("Answers.") < html.index("<b>1. B.</b>")
    assert text.index("Practice questions:") < text.index("Answers:") and "3. D. A byte is 8 bits" in text
    _, _, html = emailer.render_paper(Paper(id="x", title="T", url="javascript:alert(1)", source="S"), SAMPLE_QUIZ)
    assert "javascript:" not in html and "Read the paper" not in html


def test_paper_is_saved_to_the_sheet_and_linked(caplog):
    posted = []
    sheet = SimpleNamespace(post=lambda url, json=None, timeout=None: posted.append(json) or Reply(data={"ok": True}))
    assert tracker.push_paper(sheet, "2026-11-18-x", "T", "https://a/b", "HotNets", {"choice": []}, ENV) is True
    assert posted[0] == {"t": "secret", "a": "paper", "id": "2026-11-18-x", "title": "T", "link": "https://a/b", "source": "HotNets", "json": '{"choice":[]}'}
    old = SimpleNamespace(post=lambda *a, **k: Reply(data={"ok": False, "error": "unknown action"}))
    assert tracker.push_paper(old, "x", "T", "l", "s", {}, ENV) is False and tracker.push_paper(sheet, "x", "T", "l", "s", {}, {}) is False
    assert tracker.quiz_url("2026-11-18-x", ENV).endswith("?t=secret&a=quiz&id=2026-11-18-x") and tracker.quiz_url("x", {}) is None
    assert tracker.papers_url(ENV).endswith("?t=secret&a=papers") and tracker.papers_url({}) is None


def test_papers_config(tmp_path):
    from jobscanner.config import load_config
    path, base = tmp_path / "c.yaml", "lists:\n  - key: cs\n"
    path.write_text(base)
    assert load_config(path).papers is None
    path.write_text(base + "papers: {days: [Sunday, wednesday, saturday, sunday], hour: 9}\n")
    assert load_config(path).papers == papers.PapersConfig(days=(2, 5, 6), hour=9, focus=papers.FOCUS)
    path.write_text(base + "papers:\n  days: [monday]\n  focus: ['  Storage   systems ']\n")
    assert load_config(path).papers == papers.PapersConfig(days=(0,), hour=9, focus=("Storage systems",))
    for bad in ("{days: sunday}", "{days: []}", "{days: [someday]}", "{days: [sunday], hour: 24}", "{days: [sunday], often: 3}",
                "{days: [sunday], focus: []}", "{days: [sunday], focus: [3]}", "weekly", "{hour: 9}"):
        path.write_text(base + f"papers: {bad}\n")
        with pytest.raises(ValueError, match="'papers' must look like"):
            load_config(path)


class Outbox(list):
    def __call__(self, subject, text, html, attachments=(), inline=()):
        self.append({"subject": subject, "text": text, "html": html})


def setup(monkeypatch, paths, saved=True):
    Path(paths[0]).write_text(CONFIG + "papers: {days: [wednesday, saturday, sunday], hour: 9}\n")
    saves = []
    monkeypatch.setattr(papers.lab_items, "__defaults__", ((),))                       # no labs in these runs
    monkeypatch.setattr(tracker, "push_paper", lambda session, pid, title, link, source, quiz: saves.append((pid, title)) or saved)
    monkeypatch.setattr(tracker, "quiz_url", lambda pid: f"https://script.google.com/x?a=quiz&id={pid}")
    monkeypatch.setattr(tracker, "papers_url", lambda: "https://script.google.com/x?a=papers")
    return saves


def pdf_web():
    return Web({"https://arxiv.org/pdf/": Reply(b"%PDF-1.7", "application/pdf"), "https://cs.cornell.edu": Reply(b"%PDF-1.7", "application/pdf")})


def test_paper_goes_out_once_on_its_day_and_the_next_one_differs(site, paths, monkeypatch, caplog):
    saves = setup(monkeypatch, paths)
    outbox = Outbox()
    with caplog.at_level(logging.INFO):
        assert run(*paths, session=pdf_web(), sender=outbox, now=NOW, reader=FakeReader(quiz=SAMPLE_QUIZ), assistant=None) == 0
    assert outbox[-1]["subject"] == "📄 Paper to read: Defeating Prompt Injections by Design"
    assert "id=2026-11-18-defeating-prompt-injections-by-design" in outbox[-1]["html"] and saves[0][1] == "Defeating Prompt Injections by Design"
    memory = json.loads(Path(paths[1]).read_text())["papers"]
    assert memory["sent"] == ["core:camel", "arxiv:2503.18813"] and memory["done"] is True and memory["tries"] == 1
    assert memory["last_at"] == NOW.isoformat() and "sent the paper 'core:camel'" in caplog.text
    run(*paths, session=pdf_web(), sender=outbox, now=NOW + timedelta(minutes=30), reader=FakeReader(quiz=SAMPLE_QUIZ), assistant=None)
    assert sum(m["subject"].startswith("📄") for m in outbox) == 1                                             # once that day
    run(*paths, session=pdf_web(), sender=outbox, now=NOW + timedelta(days=3), reader=FakeReader(quiz=SAMPLE_QUIZ), assistant=None)
    assert outbox[-1]["subject"] == "📄 Paper to read: Network Design Considerations for Trading Systems"       # Saturday: the next one


def test_paper_waits_for_its_days_and_hour_and_is_off_without_config(site, paths, monkeypatch):
    setup(monkeypatch, paths)
    outbox, reader = Outbox(), FakeReader(quiz=SAMPLE_QUIZ)
    for when in (NOW - timedelta(days=1), NOW - timedelta(hours=3), NOW + timedelta(days=1)):                  # Tuesday, Wednesday 7 AM, Thursday
        run(*paths, session=pdf_web(), sender=outbox, now=when, reader=reader, assistant=None)
    assert outbox == [] and reader.calls == []
    Path(paths[0]).write_text(CONFIG)
    run(*paths, session=pdf_web(), sender=outbox, now=NOW, reader=reader, assistant=None)
    assert outbox == [] and json.loads(Path(paths[1]).read_text())["papers"] == {}


def test_without_a_key_or_a_sheet_it_degrades_quietly(site, paths, monkeypatch, caplog):
    setup(monkeypatch, paths, saved=False)
    outbox = Outbox()
    with caplog.at_level(logging.INFO):
        run(*paths, session=pdf_web(), sender=outbox, now=NOW, reader=None, assistant=None)
        run(*paths, session=pdf_web(), sender=outbox, now=NOW + timedelta(minutes=30), reader=None, assistant=None)
    assert outbox == [] and caplog.text.count("needs the ANTHROPIC_API_KEY secret") == 1
    Path(paths[1]).unlink()
    run(*paths, session=pdf_web(), sender=outbox, now=NOW, reader=FakeReader(quiz=SAMPLE_QUIZ), assistant=None)
    assert "Take the quiz" not in outbox[-1]["html"] and "Practice questions." in outbox[-1]["html"]            # no Sheet: quiz in the email


def test_unreadable_papers_are_passed_over_and_failing_quizzes_give_up_after_three(site, paths, monkeypatch, caplog):
    setup(monkeypatch, paths)
    outbox = Outbox()
    run(*paths, session=Web(), sender=outbox, now=NOW, reader=FakeReader(quiz=SAMPLE_QUIZ), assistant=None)     # the paper's site is down
    memory = json.loads(Path(paths[1]).read_text())["papers"]
    assert outbox == [] and memory["sent"] == ["core:camel", "arxiv:2503.18813"] and memory["done"] is False
    run(*paths, session=pdf_web(), sender=outbox, now=NOW + timedelta(minutes=30), reader=FakeReader(quiz=SAMPLE_QUIZ), assistant=None)
    assert outbox[-1]["subject"].endswith("Network Design Considerations for Trading Systems")                  # second try that day: the next paper
    Path(paths[1]).unlink()
    outbox, reader = Outbox(), FakeReader(quiz=[None] * 9)
    for i in range(5):
        assert run(*paths, session=pdf_web(), sender=outbox, now=NOW + timedelta(minutes=30 * i), reader=reader, assistant=None) == 0
    memory = json.loads(Path(paths[1]).read_text())["papers"]
    assert outbox == [] and memory["tries"] == 3 and memory["sent"] == ["core:camel", "arxiv:2503.18813"] and memory["failed"] == {}


def test_paper_trouble_never_stops_the_alerts(site, paths, monkeypatch, caplog):
    from .test_main import posting
    setup(monkeypatch, paths)
    site["cs"] = [posting("nvidia", company="NVIDIA", posted_at=NOW - timedelta(minutes=5))]
    monkeypatch.setattr(papers, "pick", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    outbox = Outbox()
    assert run(*paths, session=pdf_web(), sender=outbox, now=NOW, reader=FakeReader(quiz=SAMPLE_QUIZ), assistant=None) == 0
    assert len(outbox) == 1 and "NVIDIA" in outbox[0]["text"] and "paper: failed (RuntimeError)" in caplog.text


def test_preview_writes_the_sample_without_a_key(tmp_path, paths):
    out = tmp_path / "preview"
    assert paper_preview(paths[0], str(out), session=Web(), reader=None) == 0
    assert sorted(f.name for f in out.iterdir()) == ["email.html", "email.txt", "quiz.json"]
    assert "Sample paper" in (out / "email.html").read_text() and json.loads((out / "quiz.json").read_text())["minutes"] == 35
    assert (out / "email.txt").read_text().startswith("Subject: [Sample] 📄 Paper to read: Network Design Considerations")


def test_preview_builds_a_real_one_with_a_reader(tmp_path, paths, monkeypatch):
    monkeypatch.setattr(papers.lab_items, "__defaults__", ((),))
    out = tmp_path / "preview"
    paper_preview(paths[0], str(out), session=pdf_web(), reader=FakeReader(quiz=SAMPLE_QUIZ), now=NOW)
    assert "From your reading list" in (out / "email.html").read_text() and "Defeating Prompt Injections" in (out / "email.txt").read_text()
