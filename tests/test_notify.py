from jobscanner import notify
from jobscanner.filters import evaluate

from .test_filters import CRIT, posting

ENV = {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/abc"}


class Session:
    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        if self.fail:
            raise ConnectionError(f"could not reach {url}")
        return type("R", (), {"raise_for_status": lambda self: None})()


def match(i=0, **kw):
    m = evaluate(posting(row_id=str(i), company="NVIDIA", salary="$40 /hr", apply_url=f"https://x/{i}", **kw), CRIT)
    m.fit, m.fit_why = 82, "Strong match."
    return m


def test_posts_embeds_without_mentions():
    s = Session()
    assert notify.send(s, [match(title="Security Intern @everyone")], ENV) is True
    url, body = s.posts[0]
    assert url == ENV["DISCORD_WEBHOOK_URL"] and body["allowed_mentions"] == {"parse": []}
    embed = body["embeds"][0]
    assert embed["title"] == "NVIDIA: Security Intern @everyone" and embed["url"] == "https://x/0"
    assert "$40/hr" in embed["description"] and "Fit 82" in embed["description"] and "Strong match." in embed["description"]
    assert body["content"] == "🎯 1 new internship"


def test_many_matches_are_split_and_capped():
    s = Session()
    notify.send(s, [match(i) for i in range(35)], ENV)
    assert [len(b["embeds"]) for _, b in s.posts] == [10, 10, 10]
    assert s.posts[0][1]["content"] == "🎯 35 new internships (5 more in your email)"


def test_off_without_a_valid_webhook_and_never_raises(caplog):
    s = Session()
    assert notify.send(s, [match()], {}) is False
    assert notify.send(s, [match()], {"DISCORD_WEBHOOK_URL": "https://evil.example/api/webhooks/1/abc"}) is False
    assert s.posts == []
    assert notify.send(Session(fail=True), [match()], ENV) is False
    assert "abc" not in caplog.text   # the webhook URL is a secret: it must not reach the logs
