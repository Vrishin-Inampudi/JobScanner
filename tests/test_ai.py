import json
from types import SimpleNamespace

import anthropic
import httpx2 as httpx

from jobscanner.ai import AIConfig, Assistant, draft_filename, from_env

from .test_filters import posting

RESUME = "Jane Doe\nSecurity+ certified. Built a SIEM pipeline in Python."


def reply(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking", thinking=""),
                                                      SimpleNamespace(type="text", text=text)])


class FakeClient:
    """Stands in for anthropic.Anthropic: records each request and returns canned replies."""

    def __init__(self, score=None, draft=None, error=None):
        self.calls, self._score, self._draft, self._error = [], score, draft, error
        self.messages = SimpleNamespace(create=lambda **kw: self._answer("score", self._score, kw))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: self._answer("draft", self._draft, kw)))

    def _answer(self, kind, value, kw):
        self.calls.append((kind, kw))
        if self._error:
            raise self._error
        return value


def test_score_sends_resume_as_cached_prefix_and_reads_json():
    client = FakeClient(score=reply(json.dumps({"score": 82, "why": "Strong SIEM  match;\nno Java."})))
    fit = Assistant(client, RESUME).score(posting(title="SOC Intern", description="Splunk, Python"))
    assert (fit.score, fit.why) == (82, "Strong SIEM match; no Java.")
    kind, kw = client.calls[0]
    assert kind == "score" and kw["model"] == "claude-haiku-5-5"
    assert RESUME in kw["system"][-1]["text"] and kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert RESUME not in kw["messages"][0]["content"] and "SOC Intern" in kw["messages"][0]["content"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in kw and "temperature" not in kw


def test_score_is_clamped_and_bad_replies_are_skipped():
    assert Assistant(FakeClient(score=reply('{"score": 140, "why": "x"}')), RESUME).score(posting()).score == 100
    assert Assistant(FakeClient(score=reply("not json")), RESUME).score(posting()) is None
    assert Assistant(FakeClient(score=reply('{"score": 80, "why": "x"}', stop="refusal")), RESUME).score(posting()) is None
    assert Assistant(FakeClient(score=reply('{"score": 80, "wh', stop="max_tokens")), RESUME).score(posting()) is None


def test_draft_uses_the_stronger_model_with_fallbacks():
    client = FakeClient(draft=reply("COVER LETTER\nDear team..."))
    cfg = AIConfig(draft_model="claude-opus-5-5")
    assert Assistant(client, RESUME, cfg).draft(posting()).startswith("COVER LETTER")
    kind, kw = client.calls[0]
    assert kind == "draft" and kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]


def test_a_bad_key_turns_ai_off_for_the_run_instead_of_failing_it():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=request), body=None)
    client = FakeClient(error=error)
    helper = Assistant(client, RESUME)
    assert helper.score(posting()) is None and helper.score(posting()) is None and helper.draft(posting()) is None
    assert len(client.calls) == 1   # it stopped asking after the first failure


def test_from_env_needs_both_secrets():
    assert from_env(env={}) is None
    assert from_env(env={"ANTHROPIC_API_KEY": "k"}) is None
    assert from_env(env={"RESUME_TEXT": RESUME}) is None
    helper = from_env(AIConfig(draft_min_score=60), env={"ANTHROPIC_API_KEY": "k", "RESUME_TEXT": RESUME})
    assert helper.resume == RESUME and helper.cfg.draft_min_score == 60


def test_draft_filename_is_safe():
    assert draft_filename(posting(company="A/B: Corp", title="Intern <Security>\n")) == "Draft - A B Corp - Intern Security.txt"
