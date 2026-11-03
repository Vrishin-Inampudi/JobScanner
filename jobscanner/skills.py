"""Point out skills a posting mentions: ones you have (✓) and ones you don't (✗).

Only a highlight in the email; it never changes which jobs are sent.
"""

from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field

# Ordinary English words that are also skill names only count when capitalized as the name.
_CASE_SENSITIVE_WORDS = {"react", "swift"}


@dataclass(frozen=True)
class Skill:
    name: str
    pattern: re.Pattern


def skill(entry) -> Skill:
    """'Python' or ['Security+', 'Security Plus'] (shown name first, then other spellings)."""
    names = [entry] if isinstance(entry, str) else list(entry)
    if not names or not all(isinstance(n, str) and n.strip() for n in names):
        raise ValueError(f"a skill must be a name or a list of names, got {entry!r}")
    parts = []
    for n in (n.strip() for n in names):
        # 'C' must not match C++, C# or C-suite; short names (C, Go, AWS, Java) must match their capitalization.
        body = rf"(?<![A-Za-z0-9-]){re.escape(n)}(?![A-Za-z0-9+#]|-[A-Za-z])"
        sensitive = len(n) <= 4 or n.lower() in _CASE_SENSITIVE_WORDS
        parts.append(body if sensitive else f"(?i:{body})")
    return Skill(names[0].strip(), re.compile("|".join(parts)))


@dataclass
class Skills:
    have: list[Skill] = field(default_factory=list)
    lack: list[Skill] = field(default_factory=list)

    def find(self, text: str) -> tuple[list[str], list[str]]:
        """(skills you have, skills you don't) that the text mentions, in your config's order."""
        if not text:
            return [], []
        hits = lambda skills: [s.name for s in skills if s.pattern.search(text)]
        return hits(self.have), hits(self.lack)


def plain_text(markup: str, limit: int = 6000) -> str:
    """Job description HTML -> plain text (also handles HTML that was escaped once more)."""
    text = htmllib.unescape(str(markup or ""))
    text = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t ]+", " ", text).strip()[:limit]
