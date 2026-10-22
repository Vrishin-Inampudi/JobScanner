# JobScanner

An hourly email of new internships from [intern-list.com](https://www.intern-list.com/)
that are worth your time. It runs free on GitHub Actions, so nothing runs on your computer.

Every hour it checks the lists you choose. It emails **every** newly posted internship
that matches your rules, ranked best-first. If nothing new matches, it sends no email.
It never sends the same posting twice.

The default [`config.yaml`](config.yaml) watches **Cybersecurity** plus **network
engineering** roles. A posting qualifies if it meets any one of these:

- pays **≥ $35/hr**
- is in **CA, MA, NY or CT**
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

### Make the hourly runs reliable (recommended)

GitHub's built-in scheduler often delays or skips runs. When this repo was new, it
started only 2 of about 12 hourly runs. Runs you trigger yourself start right away, so
a free outside timer gives you a dependable hourly check:

1. **Create a GitHub token that can only start this workflow.** Go to
   <https://github.com/settings/personal-access-tokens/new>, then:
   - Set *Repository access* to **Only select repositories**, and pick your copy of
     JobScanner.
   - Set *Permissions → Repository permissions → Actions* to **Read and write**.
     Leave everything else at No access.
   - Choose a 1-year expiration, generate the token, and copy it.
2. **Create a free account at <https://cron-job.org>,** then click *Create cronjob*:
   - **URL:** `https://api.github.com/repos/<you>/JobScanner/actions/workflows/alerts.yml/dispatches`
   - **Schedule:** every hour, at minute 5.
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
- **`max_age_hours`**: only jobs posted within this many hours are considered. Jobs are
  never emailed twice, so with hourly runs each email holds what was posted since the
  last run. The default of `2` leaves slack for runs that start late.
- **`match`**: `any` means one criterion is enough. `all` means every criterion must
  hold. Under `all`, postings without a listed salary fail the pay check.
- **`min_hourly_pay`**: compared against the top of the posted range. Yearly pay is
  divided by 2,080 hours.
- **`locations`**: US state codes. "Remote, United States" doesn't count as a match.
- **`priority_companies`**: ranked first, and they always count as large companies.
  Matching tolerates suffixes like "Inc." or "Corporation", but "Apple Bank" won't
  match "Apple".

Jobs already emailed are never sent again, even if you change the config.

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
needed.

The jobs it has already seen and emailed are stored in `state/seen.json`, which the
workflow commits back after each run. If an email fails to send, state isn't saved, so
the next hour retries.

Not affiliated with intern-list.com. Be kind to their servers: hourly is plenty.

## License

MIT
