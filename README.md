# JobScanner

Email alerts, every 30 minutes, of new internships from [intern-list.com](https://www.intern-list.com/)
that are worth your time. It runs free on GitHub Actions, so nothing runs on your computer.

Every 30 minutes (or however often you schedule it) it checks the lists you choose. It emails **every** newly posted internship
that matches your rules, ranked best-first. If nothing new matches, it sends no email.
It never sends the same posting twice.

The default [`config.yaml`](config.yaml) watches **Cybersecurity** plus **network
engineering** roles. A posting qualifies if it meets any one of these:

- pays **≥ $35/hr**
- is in the **Bay Area, LA, San Diego, Seattle, NYC, Boston, Chicago, Austin, DC** or
  anywhere in **CT**
- is at a **large company** (10,000+ employees) or a priority company, such as
  Palo Alto Networks, CrowdStrike, NVIDIA, Apple or Meta

The ranking puts priority companies first, then higher pay, then a preferred location,
then bigger companies, then the newest postings.

## Set it up for yourself (about 10 minutes)

1. **Fork this repo.** You can also use it as a template.
2. **Create a Gmail app password** for the Gmail account that will send the email.
   Turn on 2-Step Verification, then go to
   <https://myaccount.google.com/apppasswords> and create one called "JobScanner".
   Copy the 16-character password.
3. **Add three secrets** in your fork under *Settings → Secrets and variables → Actions →
   New repository secret*:

   | Secret | Value |
   |---|---|
   | `GMAIL_ADDRESS` | the Gmail address that sends the email |
   | `GMAIL_APP_PASSWORD` | the app password from step 2 |
   | `EMAIL_TO` | where to send alerts (comma-separate several; defaults to `GMAIL_ADDRESS`) |

4. **Enable Actions.** Open the *Actions* tab and click "I understand… enable them".
   Forks start with Actions turned off.
5. **Run it once.** Go to *Actions → Internship alerts → Run workflow*. It emails any
   matching jobs posted in the last `max_age_hours`, then only newer ones after that.

GitHub emails you when a run fails, for example if intern-list.com changes its
layout. So if it breaks, you'll hear about it instead of it going quiet.

### Make the scheduled runs reliable (recommended)

GitHub's built-in scheduler often delays or skips runs. When this repo was new, it
started only 2 of about 12 hourly runs. Runs you trigger yourself start right away, so
a free outside timer gives you a dependable check:

1. **Create a GitHub token that can only start this workflow.** Go to
   <https://github.com/settings/personal-access-tokens/new>, then:
   - Set *Repository access* to **Only select repositories**, and pick your copy of
     JobScanner.
   - Set *Permissions → Repository permissions → Actions* to **Read and write**.
     Leave everything else at No access.
   - Choose a 1-year expiration, generate the token, and copy it.
2. **Create a free account at <https://cron-job.org>,** then click *Create cronjob*:
   - **URL:** `https://api.github.com/repos/<you>/JobScanner/actions/workflows/alerts.yml/dispatches`
   - **Schedule:** every 30 minutes (or every hour). Set `check_every_minutes` in
     `config.yaml` to match, so the emails say when the next check is.
   - Under *Advanced*, set **Request method** to `POST` and **Request body** to
     `{"ref":"main"}`.
   - Add these **headers**:

     | Key | Value |
     |---|---|
     | `Authorization` | `Bearer <your token>` |
     | `Accept` | `application/vnd.github+json` |
     | `X-GitHub-Api-Version` | `2022-11-28` |
     | `Content-Type` | `application/json` |
3. **Save it and click *Test run*.** A response of **204** means it worked, and a new
   run will appear in your *Actions* tab within seconds.

The built-in schedule stays on as a backup. If both fire close together, the second
run just finds nothing new.

### Track your applications (optional)

Each job in the email can have **✓ Applied** and **Not interested** links. A click is
logged as a row in a private Google Sheet, which becomes your application tracker, with
a Notes column you can fill in. Jobs you've marked never come back. "Not interested" also
mutes the same role at that company in other cities.

1. Create a Google Sheet, e.g. "Internship applications".
2. Open *Extensions → Apps Script*, replace the code with
   [`tracker/Code.gs`](tracker/Code.gs), and save.
3. Make a secret token and save it as a GitHub secret. On a Mac, this command does both
   and copies the token to your clipboard:
   `t=$(openssl rand -hex 16); gh secret set TRACKER_TOKEN -b "$t"; printf %s "$t" | pbcopy`
4. In Apps Script, open *Project Settings → Script properties*, then add a property
   named `TOKEN` and paste the token as its value.
5. Click *Deploy → New deployment → Web app*. Set *Execute as* to **Me** and
   *Who has access* to **Anyone**, then click *Deploy*. Approve the access request,
   then copy the web app URL.
6. Add that URL as the GitHub secret `TRACKER_URL`.

Each link carries the token, so only your emails can write to the Sheet. If the tracker
is down, alerts keep working.

## Change what it looks for

Everything lives in [`config.yaml`](config.yaml), and no code changes are needed.
Here's an example for electrical engineering:

```yaml
lists:
  - key: eng
    title_include: [electrical, hardware, embedded, power systems]
match: any
criteria:
  min_hourly_pay: 30
  locations: [TX, CA, AZ]
  large_company_min_size: 5000
  priority_companies: [Texas Instruments, Qualcomm, Tesla, Intel, Analog Devices]
```

- **`lists`**: the site's short names, which are the `?k=` value in its URLs. `cs` is
  Cybersecurity, `swe` is Software Engineering, `eng` is Engineering and Development,
  and `aiml` is ML/AI. Run `python -m jobscanner --show-lists` to see all of them.
  `title_include` and `title_exclude` keep or drop titles that contain any of the
  given words. They're case-insensitive.
- **GitHub lists**: a list entry with `repo:` reads a community internship repo instead,
  such as `SimplifyJobs/Summer2027-Internships`. Any repo with
  `.github/scripts/listings.json` in that format works. Those lists have no pay or
  company size, so their jobs qualify by location or priority company. A job that's on
  both lists is sent once, using intern-list's copy. Update the repo name each season.
- **`company_sites`**: check companies' own career sites, where jobs appear before
  aggregators copy them. Each site needs a `company` and either a `workday` careers URL
  (e.g. `https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite`), a
  `greenhouse`, `lever` or `ashby` board name (the part after `boards.greenhouse.io/`,
  `jobs.lever.co/` or `jobs.ashbyhq.com/`), an `oracle`
  Recruiting careers URL (`https://….oraclecloud.com/hcmUI/CandidateExperience/en/sites/…`),
  or `careers: amazon`, `google` or `apple` for those companies' own sites. Meta and
  Microsoft aren't supported, because their job search only works in a browser. The section's
  `title_require`, `title_include` and `title_exclude` apply to every site. The first
  check of a new site only records what's already posted, and after that you get each
  new job. Every company listed here counts as a priority company, and jobs located only
  outside the US are skipped.
- **`eligibility`**: hard filters, applied before `criteria`. `graduation: 2028-05` skips
  roles aimed at other graduating classes, and `skip_phd_only: true` skips PhD-only
  roles. Each email card also shows the start term and target grad class when the
  posting gives them, plus tags like "Needs clearance" or "US citizens".
- **`digest_hour`**: send less-urgent matches as one morning email at this hour
  (Eastern). Matches at top companies, or paying at least `min_hourly_pay`, still arrive
  right away. Leave it out to get everything right away.
- **`company_needs_location`**: when `true`, top-company and large-company matches must
  also be in your `locations`, remote, or posted without a specific city. Matches on pay
  alone can still be anywhere.
- **`check_every_minutes`**: how often your timer runs the check. It only changes the
  email wording, such as "Check back in 30 minutes".
- **`max_age_hours`**: how far back the first run looks. After that, each run looks back
  past the previous run, and jobs are never emailed twice, so each email holds what was
  posted since the last check.
- **`empty_email_every_hours`**: send a short "Hi, nothing has been posted!" email after
  this many hours without any email, so you know it's still checking. It's skipped if a
  list failed to load. Leave it out to turn it off.
- **`quiet_hours`**: `[start, end]` hours (Eastern time) when it doesn't check, e.g.
  `[0, 4]` for midnight to 4 AM. The first run afterwards catches up on anything posted
  in the meantime.
- **`match`**: `any` means one criterion is enough. `all` means every criterion must
  hold. Under `all`, postings without a listed salary fail the pay check.
- **`min_hourly_pay`**: compared against the top of the posted range. Yearly pay is
  divided by 2,080 hours.
- **`locations`**: any mix of state codes (`CT`), metro areas (`Bay Area`, `Los Angeles`,
  `San Diego`, `Seattle`, `NYC`, `Boston`, `Chicago`, `Austin`, `DC`) and cities
  (`"Denver, CO"`). Metros include their suburbs, e.g. Sunnyvale counts as Bay Area and
  McLean, VA counts as DC. The suburb lists are `METROS` in
  [`jobscanner/filters.py`](jobscanner/filters.py). "Remote, United States" doesn't count.
- **`priority_companies`**: ranked first, and they always count as large companies.
  Matching tolerates suffixes like "Inc." or "Corporation", but "Apple Bank" won't
  match "Apple".

Jobs already emailed are never sent again, even if you change the config.

### Skills

Each job in the email lists the skills its posting mentions: green **✓** for ones you
have, amber **✗** for ones you don't. This only highlights; it never changes which jobs
are sent. List them under `skills`:

```yaml
skills:
  have: [Python, C++, C, Linux, [Security+, Sec+]]   # [name, other spellings...]
  lack: [Java, [Go, Golang], [C#, .NET]]
```

`C` doesn't match C++, C# or C-suite, and short names (C, Go, AWS) only match with that
exact capitalization, so "go above and beyond" isn't Go. Postings are read from
intern-list's qualifications and from Workday, Greenhouse, Lever, Ashby and Amazon job
descriptions. The GitHub list has no descriptions, so its jobs show no skills.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m jobscanner --preview     # print what qualifies right now; sends nothing
.venv/bin/python -m pytest -q               # tests
```

To get a sample email, go to *Actions → Internship alerts → Run workflow* and tick
**test**. It emails matches from the last 24 hours with "[Test]" in the subject and
doesn't change what counts as already sent.

`--dry-run` behaves like a real run, but it prints the email instead of sending it and
doesn't save state.

## How it works

intern-list.com names its lists and shows each one in a table embedded from
jobright.ai. [`jobscanner/source.py`](jobscanner/source.py) reads the list names from
the site, then reads each list's newest jobs from the same public jobright.ai feed the
table uses, page by page until it passes `max_age_hours`. No account or API key is
needed. GitHub lists are read from the repo's public `listings.json`, looking back
three days because those listings are often dated by day only. Company career sites
are read from their public Workday and Greenhouse job feeds
([`jobscanner/sites.py`](jobscanner/sites.py)).

The jobs it has already seen and emailed are stored in `seen.json` on the repo's `state`
branch. Each run replaces that branch with a single fresh commit, so it never builds up
history, and `main` only changes when you change the code or config. Only jobs that pass
a list's title filter are remembered. If an email fails to send, state isn't saved, so
the next run retries.

Not affiliated with intern-list.com. Be kind to their servers: every 30 minutes is plenty.

## License

MIT
