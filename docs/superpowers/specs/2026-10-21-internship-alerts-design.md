# Internship Alerts — Design

Date: 2026-10-21
Owner: Vrishin Inampudi

## Intent

Vrishin is a CS student interning at CACI for $30/hr. They want to hear quickly
when a clearly better internship appears on [intern-list.com](https://www.intern-list.com/?k=cs):
one at a well-known cybersecurity or big-tech company, at a large company, in a
strong tech region, or paying meaningfully more. They want an hourly email that
lists *every* new qualifying posting (if NVIDIA posts 10 security internships, all
10 show up), ranked so the most attractive ones come first.

It should live in a public GitHub repo and be easy for anyone to fork and point
at a different role (SWE, EE, …) by editing one config file, with no code changes.

## Goals

- Hourly check of selected intern-list.com lists; email only postings not seen before.
- Every qualifying new posting is included; no cap.
- No email when nothing new qualifies.
- Ranking that surfaces "traction" (the site has no popularity data; see Ranking).
- Fully driven by `config.yaml` + GitHub secrets; a fork never emails the original owner.
- Runs free on GitHub Actions; nothing runs on the user's machine.

## Non-goals

- No web UI, database, or hosted server.
- No applying to jobs, no scraping the jobright.ai detail pages behind "Apply".
- No support for job boards other than intern-list.com.

## Anti-goals (failure even if it "works")

- Re-emailing postings already sent.
- Flooding the inbox on first run with the ~600 existing matches.
- Silently stopping when the site/Airtable format changes. Failures must fail the
  workflow run (GitHub then emails the repo owner).
- Any personal email address or password in the repo.

## Data source — update 2026-10-22

On 2026-10-22 intern-list.com replaced its Airtable embeds with an iframe from
jobright.ai (`https://jobright.ai/minisites-jobs/intern/us/<slug>?embed=true`). The app
now reads that feed directly: `POST https://jobright.ai/swan/mini-sites/list?position=N&count=50`
with body `{"category": "intern:us:<slug>"}` (slug from the site's `data-job-path`).
Jobs come newest first with a stable `jobId` and a `postedAt` timestamp, which is US
Pacific wall-clock time encoded as if it were UTC (the app converts it, and falls back to
UTC if converted times land in the future). Vrishin asked for each hourly email to hold
only jobs posted since the last run, so `max_age_hours` (2, for slack on late runs)
replaced the date-based filter. The Airtable notes below are historical.

## Data source (verified 2026-10-21)

- `https://www.intern-list.com/` contains `<h2 data-job-path="/us/<slug>"
  airtable-link="https://airtable.com/embed/<appId>/<shrId>?viewControls=on"
  short-link="<key>">`. The `?k=` URL param maps to `short-link`.
  `k=cs` = **Cybersecurity** (`/us/cyber_security`), not Computer Science.
  There are also `/ca/...` (Canada) entries that reuse some short-link keys; only
  `/us/` entries are used. (Builder's call whether to make the region configurable.)
- Relevant keys: `cs` (Cybersecurity, ~596 rows), `swe` (Software Engineering,
  ~2,450 rows), `eng` (Engineering and Development, ~18,470 rows).
- Each shared view is readable without auth:
  1. GET `https://airtable.com/embed/<appId>/<shrId>` with a browser User-Agent,
     keeping cookies.
  2. Extract `urlWithParams: "..."` from the HTML (JS-escaped, `/` etc.).
  3. GET `https://airtable.com` + that path with headers
     `x-airtable-application-id: <appId>`, `x-requested-with: XMLHttpRequest`,
     `x-time-zone`, `x-user-locale`, same cookie jar.
  4. JSON at `data.table`: `columns` (id, name, type, typeOptions.choices for
     select/multiSelect) and `rows` (`id`, `cellValuesByColumnId`).
  A throwaway probe doing exactly this succeeded for `cs`, `swe`, `eng`. The `eng`
  response is large (18k rows); this is acceptable.
- Columns: Position Title, Date (`YYYY-MM-DD`), Apply (`{label,url}`), Work Model
  (select), Location (text, e.g. `San Jose, CA, United States`), Company, Salary
  (text), Hire Time, Graduate Time, Company Size (select: `1-10` … `10000+`),
  Company Industry (multiSelect), Qualifications.
- Salary formats seen: `N/A` (~half), `Unpaid`, `$45-$60 /hr`, `$22.60-$47.95 /hr`,
  `$52900-$108000 /yr`. Parse to hourly; `/yr` ÷ 2080. Compare using the **top of
  the range** (builder may change if a better rule appears). Unparseable → unknown.

## Configuration (`config.yaml`)

The default config is Vrishin's. Shape (field names are the builder's call):

```yaml
lists:
  - key: cs                 # whole Cybersecurity list
  - key: swe
    title_include: [network engineer, networking, network security, network software, noc]
  - key: eng
    title_include: [network engineer, networking, network security, noc]
    title_exclude: [electrical, transmission, distribution, power]
match: any                  # any | all, over the criteria below
criteria:
  min_hourly_pay: 35
  locations: [CA, MA, NY, CT]   # state codes; full names also matched
  large_company_min_size: 10000
  priority_companies:           # also count as "large company"
    - Palo Alto Networks
    - CrowdStrike
    - Fortinet
    - Zscaler
    - Cisco
    - Okta
    - Cloudflare
    - SentinelOne
    - Apple
    - NVIDIA
    - Meta
    - Google
    - Microsoft
    - Amazon
```

Title matching is case-insensitive substring. Company matching is case-insensitive
and tolerant of suffixes (e.g. "NVIDIA Corporation" matches "NVIDIA").

## Matching

A posting qualifies (with `match: any`) if at least one is true:
- pay ≥ `min_hourly_pay` (unknown pay never satisfies this, but doesn't disqualify),
- Location contains one of the configured states (by `, XX,` code or full state name),
- company size ≥ `large_company_min_size`, or company is a priority company.

With `match: all`, unknown pay fails the pay criterion (builder's call).

Remote-only postings do not count as a location match on their own (Vrishin can
change this later; builder may add a config flag).

## Ranking (stands in for "traction")

Sort the hour's qualifying postings by, in order:
1. priority company first,
2. higher hourly pay (unknown pay last),
3. in a configured location,
4. larger company size,
5. newer Date.

## Dedup and state

- Identity = the Apply URL without its query string **and** company|title;
  a posting is seen if either was seen. (Location was dropped from the fingerprint
  after the site re-listed jobs with merged "Multi Location ..." locations and new links.) The site sometimes gives an existing job a new
  apply link on rebuild (10 of 12 "new" postings in the first live email were such
  repeats), so the link alone isn't enough. **Not** the Airtable row id: the site rebuilds its tables
  several times a day and every row gets a new id (found in production 2026-10-21,
  when a rebuild re-sent every match). Stripping the query (`utm_campaign` differs per
  list) also dedupes the same job across lists. The state file is versioned; an older
  version is re-baselined silently.
- State file `state/seen.json` committed back to the repo by the workflow after each
  successful run.
- **Every fetched row is recorded as seen**, whether or not it qualified. Config
  changes (lower pay bar, new state, new keyword) therefore only affect postings
  that appear afterwards — no catch-up digest. (Decided by Vrishin.)
- **First run / new list** (no state file, or a list key with no state yet): record
  that list's rows as seen silently and send nothing for it (a log line says how
  many were recorded). Builder may add a flag to send a one-off digest instead.
- Also remember emailed Apply URLs, so the same job appearing later in a second
  list is not emailed again.
- State updates only after the email sends successfully, so a failed send retries
  next hour.
- Builder decides pruning (e.g. drop ids absent from the source for 30+ days).

## Email

- Sent via Gmail SMTP (smtp.gmail.com:587, STARTTLS) using an app password.
- GitHub secrets: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_TO` (may equal sender).
- Subject like `🛡️ 7 new internships — NVIDIA, CrowdStrike, …`.
- HTML body plus a plain-text fallback. One entry per posting: title, company,
  location, pay, size, date posted, a short "why it matched" line
  (e.g. `NVIDIA · $52/hr · Santa Clara, CA`), and the Apply link.

## Runtime

- Python 3.12, minimal dependencies (e.g. `requests`, `PyYAML`).
- GitHub Actions workflow on `cron: "0 * * * *"` plus `workflow_dispatch`. It needs
  `contents: write` to commit state. Use a `concurrency` group so runs don't overlap.
- A `--dry-run` flag prints the email instead of sending it and does not save state.
- Fetch or parse errors exit non-zero (fail the run). An empty list or missing
  expected columns counts as an error.

## Deliverables

- Python package/script, `config.yaml`, workflow file, `.gitignore`, MIT license.
- README: what it does, how to fork, create a Gmail app password, add secrets,
  edit config for another role (example EE config snippet), enable Actions,
  run once manually.
- Unit tests for salary parsing, location/company matching, ranking, dedup,
  and the site-mapping parser (using small saved fixtures).

## Left to the builder

Module layout, exact config key names, HTML email styling, state pruning, retry and
backoff details, logging format.
