import copy
import io
import re
import zipfile

from docx import Document
from PIL import Image

from jobscanner import guide
from jobscanner.projects import SAMPLE

PNG = b"\x89PNG\r\n\x1a\n"


def test_picture_is_a_png_and_survives_bad_diagrams():
    d = SAMPLE.guide["diagram"]
    dark, light = guide.picture(d), guide.picture(d, dark=False)
    assert dark.startswith(PNG) and light.startswith(PNG) and dark != light
    assert Image.open(io.BytesIO(dark)).size[0] == 1280
    assert guide.picture({"nodes": [], "links": [], "highlight": []}) is None
    assert guide.picture(None) is None and guide.picture("<svg onload=x>") is None
    messy = {"nodes": [{"id": f"n{i}", "label": "x" * 80, "row": i % 3} for i in range(30)] + [{"label": "no id"}, "junk",
                                                                                               {"id": "r", "row": "top"}],
             "links": [{"a": "n0", "b": "ghost"}, {"a": "n0"}, "junk", {"a": "n0", "b": "n1"}], "highlight": ["ghost", 7]}
    assert guide.picture(messy).startswith(PNG)
    assert len(guide._nodes(messy)) == 12 and len(guide._nodes(messy)[0]["label"]) == 14


def test_pdf_has_every_part_and_handles_text_outside_latin_1():
    p = copy.deepcopy(SAMPLE)
    p.guide["parts"][0]["steps"][0]["text"] = "Install the tool → “quoted” ✓ 日本語 **bold** --flag [x](http://e) \\ done."
    p.guide["title"] = "Zero‑trust 网络 lab"
    data = guide.pdf(p)
    assert data.startswith(b"%PDF") and len(data) > 5000
    assert len(re.findall(rb"/Type /Page(?!s)", guide.pdf(SAMPLE))) == 6     # cover, four parts, the close


def test_pdf_copes_with_a_guide_missing_most_things():
    bare = copy.deepcopy(SAMPLE)
    bare.guide = {"title": "T", "parts": [{"title": "P", "steps": [{"text": "Run"}]}]}
    bare.roles, bare.also, bare.proves, bare.total = [], [], "", 0
    assert guide.pdf(bare).startswith(b"%PDF")
    assert guide.docx(bare)[:2] == b"PK" and "1. Run" in guide.helper_md(bare)


def test_word_file_has_numbered_steps_and_pictures():
    data = guide.docx(SAMPLE)
    text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    assert "Zero-trust campus network" in text and "Part 2: Build the fabric (about 3 hours)" in text
    assert "4. Connect each leaf switch to both spine switches." in text and "12. Check that the sensor raised an alert." in text
    assert "You should see: Two rows say Established." in text and "sudo containerlab deploy -t fabric.yml" in text
    assert "Example Networks: Network Security Intern" in text and "written by AI. Read each one" in text
    assert sum(n.startswith("word/media/") for n in zipfile.ZipFile(io.BytesIO(data)).namelist()) == 5


def test_ai_helper_lists_every_step_and_the_rules():
    md = guide.helper_md(SAMPLE)
    assert md.startswith("# AI helper: Zero-trust campus network")
    assert all(f"\n{n}. " in md for n in range(1, 13))
    assert "written by AI" in md and "Ask which step they are on" in md and "systems they don't own" in md
    assert "spine 1 connects to leaf 1" in md and "- Expected: A version number prints." in md
    assert "**A BGP neighbor stays Idle.**" in md
    assert "Example Networks" not in md            # which jobs you applied to isn't the assistant's business


def test_files_are_named_for_the_project():
    p = copy.deepcopy(SAMPLE)
    p.guide["title"] = "Zero/trust: <b>lab</b>\n" + "x" * 90
    names = [n for n, _ in guide.files(p)]
    assert names[0].startswith("Guide - Zero trust b lab b x") and names[0].endswith(".pdf") and len(names[0]) < 80
    assert [n.rsplit(".", 1)[1] for n in names] == ["pdf", "docx", "md"]
    assert all(isinstance(data, bytes) for _, data in guide.files(SAMPLE))
