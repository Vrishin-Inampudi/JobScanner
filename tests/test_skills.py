from jobscanner.skills import Skills, plain_text, skill

import pytest

SKILLS = Skills(have=[skill(x) for x in ["Python", "C++", "C", "JavaScript", "React", ["Security+", "Sec+"], "AWS"]],
                lack=[skill(x) for x in ["Java", ["Go", "Golang"], ["C#", ".NET"]]])


@pytest.mark.parametrize("text, have, lack", [
    ("Experience with C/C++ and Python", ["Python", "C++", "C"], []),
    ("Knowledge of C# and .NET; Java a plus", [], ["Java", "C#"]),
    ("Proficiency in JavaScript", ["JavaScript"], []),
    ("React or Golang; Sec+ preferred; aws-cli", ["React", "Security+"], ["Go"]),
    ("You'll react fast, go above and beyond, and talk to the C-suite", [], []),
    ("Objective-C experience", [], []),
    ("", [], []),
])
def test_find(text, have, lack):
    assert SKILLS.find(text) == (have, lack)


def test_bad_entry():
    with pytest.raises(ValueError):
        skill(["", "x"])


def test_plain_text_unescapes_and_strips_tags():
    assert plain_text("&lt;p&gt;Use &lt;b&gt;C++&lt;/b&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Linux&lt;/li&gt;") == "Use C++ \n Linux"
