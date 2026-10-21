"""Fetch internship lists from intern-list.com and its public Airtable views."""

from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass

from urllib.parse import urljoin, urlsplit

import requests

SITE_URL = "https://www.intern-list.com/"
AIRTABLE = "https://airtable.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
REQUIRED_COLUMNS = ("Position Title", "Company", "Location", "Salary", "Apply")
TIMEOUT = 60


class SourceError(RuntimeError):
    """The site or Airtable returned something we don't understand."""


@dataclass(frozen=True)
class Posting:
    list_key: str
    row_id: str
    title: str
    company: str
    location: str
    salary: str
    size: str | None
    date: str
    work_model: str | None
    apply_url: str

    @property
    def uid(self) -> str:
        return f"{self.list_key}:{self.row_id}"


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


_H2 = re.compile(r"<h2\b([^>]*\bairtable-link=[^>]*)>", re.I)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def parse_list_map(page_html: str) -> dict[str, str]:
    """Map each US list's short key (the ``?k=`` value) to its Airtable embed URL."""
    lists: dict[str, str] = {}
    for m in _H2.finditer(page_html):
        attrs = {k.lower(): htmllib.unescape(v) for k, v in _ATTR.findall(m.group(1))}
        path, link, key = attrs.get("data-job-path", ""), attrs.get("airtable-link"), attrs.get("short-link")
        if not (link and key and path.startswith("/us/")):
            continue
        lists.setdefault(key, link.split("?", 1)[0])
    if not lists:
        raise SourceError("no Airtable lists found on intern-list.com; page layout may have changed")
    return lists


def fetch_list_map(session: requests.Session) -> dict[str, str]:
    resp = session.get(SITE_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_list_map(resp.text)


def _cell(column: dict, value):
    choices = (column.get("typeOptions") or {}).get("choices") or {}
    if column["type"] == "select":
        return choices.get(value, {}).get("name", value)
    if column["type"] == "multiSelect":
        return [choices.get(v, {}).get("name", v) for v in value or []]
    return value


def parse_view(payload: dict, list_key: str) -> list[Posting]:
    try:
        table = payload["data"]["table"]
        columns = {c["id"]: c for c in table["columns"]}
        rows = table["rows"]
    except (KeyError, TypeError) as e:
        raise SourceError(f"unexpected Airtable response for list '{list_key}': missing {e}") from e
    names = {c["name"] for c in columns.values()}
    missing = [c for c in REQUIRED_COLUMNS if c not in names]
    if missing:
        raise SourceError(f"list '{list_key}' is missing columns {missing}")
    if not rows:
        raise SourceError(f"list '{list_key}' returned no rows")

    postings = []
    for row in rows:
        cells = {
            columns[cid]["name"]: _cell(columns[cid], v)
            for cid, v in row.get("cellValuesByColumnId", {}).items()
            if cid in columns
        }
        apply = cells.get("Apply") or {}
        postings.append(
            Posting(
                list_key=list_key,
                row_id=row["id"],
                title=(cells.get("Position Title") or "").strip(),
                company=(cells.get("Company") or "").strip(),
                location=(cells.get("Location") or "").strip(),
                salary=(cells.get("Salary") or "").strip(),
                size=cells.get("Company Size"),
                date=(cells.get("Date") or "").strip(),
                work_model=cells.get("Work Model"),
                apply_url=apply.get("url", "") if isinstance(apply, dict) else "",
            )
        )
    return postings


_EMBED_PATH = re.compile(r"^/embed/(app\w+)/(shr\w+)/?$")


def _airtable_embed(embed_url: str) -> tuple[str, str]:
    """(app id, rebuilt embed URL). Links come from scraped HTML, so only airtable.com embeds are followed."""
    u = urlsplit(embed_url)
    m = _EMBED_PATH.match(u.path)
    if u.scheme != "https" or u.netloc != "airtable.com" or not m:
        raise SourceError(f"not an Airtable embed URL: {embed_url}")
    app_id, share_id = m.groups()
    return app_id, f"{AIRTABLE}/embed/{app_id}/{share_id}"


_URL_WITH_PARAMS = re.compile(r'urlWithParams:\s*"((?:[^"\\]|\\.)*)"')


def fetch_view(session: requests.Session, embed_url: str, list_key: str) -> list[Posting]:
    """Read every row of a public Airtable shared view, the same way its embed page does."""
    app_id, embed_url = _airtable_embed(embed_url)

    page = session.get(embed_url, timeout=TIMEOUT)
    page.raise_for_status()
    found = _URL_WITH_PARAMS.search(page.text)
    if not found:
        raise SourceError(f"could not find data URL in Airtable page for list '{list_key}'")
    data_path = json.loads(f'"{found.group(1)}"')  # undo JS string escaping
    data_url = urljoin(AIRTABLE, data_path)
    if not data_path.startswith("/") or data_path.startswith("//") or urlsplit(data_url).hostname != "airtable.com":
        raise SourceError(f"unexpected Airtable data URL for list '{list_key}': {data_path[:100]}")

    resp = session.get(
        data_url,
        headers={
            "x-airtable-application-id": app_id,
            "x-requested-with": "XMLHttpRequest",
            "x-time-zone": "America/Los_Angeles",
            "x-user-locale": "en",
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return parse_view(resp.json(), list_key)
