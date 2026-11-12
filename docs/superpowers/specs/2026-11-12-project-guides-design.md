# Project guides: design

Date: 2026-11-12. Status: awaiting approval.

Lines marked **(my call)** were decided by Claude, not by the repo owner, and are open to change.

## Why

The owner can do the work many postings ask for but has nothing on the resume that proves it.
The scanner already knows which jobs they applied to and what those postings ask for. This
feature turns that into one impressive, advanced project at a time, with instructions good
enough to follow without guesswork, so that finishing it produces real resume lines.

## What the owner asked for

- An email suggesting projects, plus certifications, topics and concepts, based on the jobs
  they applied to.
- Projects should be impressive and use the most advanced concepts those postings mention.
  The owner already knows the basics.
- Each email has: a general description, the roles it helps with, how long it takes, suggested
  reading for more basic knowledge, a link to the instructions, the instructions as Word and
  PDF files, and a Markdown file that lets an AI assistant help with the project.
- Instructions are a numbered step-by-step list in the imperative mood. Each step starts with
  a strong action verb and covers one task. Short plain text is paired with clear visuals.
- The look follows the approved mockup (large type, generous space, rounded panels, one idea
  per section), in dark mode.

## Goals

1. Once a week, send one project email built from the owner's applied-to jobs and resume.
2. Make every guide followable start to finish: one task per step, a picture wherever one helps,
   and a check after each stage that says what success looks like and what to do otherwise.
3. Tie each project to evidence: which applied-to jobs ask for it, and what it proves.
4. End each guide with resume lines the owner can honestly write when finished.

## Non-goals

- Changing which jobs are matched or sent. This feature only reads.
- Restyling the existing alert, digest or weekly emails. **(my call)**
- Tracking progress through a project.
- Building the project for the owner (no generated starter repo).

## Anti-goals

- Anything derived from the resume reaching the public repo: not the state file, not the logs.
- Beginner projects, or advanced-sounding titles over basic content.
- Invented facts: fake job counts, dead reading links, resume lines claiming things the
  project doesn't do.
- Guides that touch systems the owner doesn't own. Security projects stay inside a lab the
  guide has the owner build, and say so.

## How it works

### 1. Know what applied-to jobs asked for

Today the private Sheet stores each match's company, title and link, but not the posting text.
The scanner will also send a trimmed copy of each match's qualifications and description
(first 6,000 characters) to the Sheet's Matches tab, in a new `Posting` column. This is public
text going to the owner's private Sheet. **(my call)**

A job counts as applied-to when its status is Applied, Interview, Offer or Rejected, marked in
the last 120 days. **(my call)** Jobs applied to before this feature have no stored text and are
judged by title alone.

The feature needs the tracker (`TRACKER_URL`), the Claude key and the resume secret. With any
missing, or fewer than 3 applied-to jobs, it does nothing and logs one line. **(my call)**

### 2. Find the gaps (first Claude request)

Input: the resume (cached system block, as today), the applied-to postings each tagged with its
ID, and the titles of projects already sent. Output, as JSON checked against a schema:

- `themes`: what the postings ask for that the resume doesn't prove, each with the IDs of the
  postings that ask for it and the posting's own wording.
- `also`: up to 5 certifications, topics or concepts, each with a kind and posting IDs.
- `project`: one project concept covering the most valuable advanced themes, different from
  projects already sent, with the IDs of the postings it helps.

The scanner drops any ID it didn't send, then computes every count itself ("4 of 9"). The model
never supplies a number that appears in the email.

### 3. Write the guide (second Claude request)

Input: the resume, the chosen project and its themes. Output, as JSON:

- title, one-line tagline, description, hours, cost, difficulty
- `reading`: up to 5 items, each a title and URL
- `parts`: 4 to 8 parts, each with a title, hours and steps
- each step: `verb` (one word), `text` (one sentence, one task), optional `command`, optional
  `expect` and `if_not` for check steps, optional `diagram`
- `resume_lines`: 3 to 5 bullets
- `pitfalls`: common failures and their causes, for the AI helper file

A diagram is a small description, not a drawing: named boxes, lines between them, and which to
highlight. The scanner draws it. This keeps pictures consistent and means no model output is
ever rendered as markup. **(my call)**

Checks made in code after the reply: every step has a verb and at most 30 words of text; every
part ends in a check step; at least one step in three has a diagram or command. One retry
naming the failures, then give up for this week. Each reading URL is requested once; a link that
doesn't answer is kept as a title with no link.

Uses the existing draft model setting (`claude-opus-5-5`), streamed, since the reply is long.
Roughly two requests a week. Posting text is wrapped and labelled as data, as it is for scoring.

### 4. Produce the files

From the one JSON guide, the scanner makes:

- **PDF**: dark, in the mockup's style, a panel per step with its number, sentence and picture.
- **Word file**: same content, light, plain styles so it can be edited and printed. **(my call)**
- **Markdown AI helper**: what the project is, the target setup, every step, the pitfalls, and
  instructions to the assistant (find which step the user is on, diagnose before answering, don't
  skip ahead, keep everything inside the lab). It contains no name or contact details.
- **Pictures**: PNG files, used in the PDF, the Word file and the email.

New libraries, pinned: Pillow (pictures), fpdf2 (PDF), python-docx (Word). **(my call)**

### 5. The link

The existing Apps Script web app gains a guide page: the same dark design, served from the
owner's own Sheet behind the existing token. The scanner posts the guide JSON to a new `Guides`
tab (date, title, themes, JSON split across cells). **(my call: a private page in the
existing web app, not a Google Doc; nothing new to deploy or authorise beyond updating the
script.)** The page builds its pictures from the same diagram descriptions and escapes all text.

The `Guides` tab is also the memory of what was sent, so projects don't repeat. The state file
stores only the date of the last project email.

### 6. The email

One email, separate from the weekly summary, subject `Project to build: <title>`. **(my call)**
Layout as approved, in dark mode: headline and tagline, "Open the instructions" button, a band
with the main picture and three numbers (hours, share of applications, cost), roles this helps
with, what it proves, read first if rusty, also worth your time, then the three attachments.
Built with tables and inline styles like the existing emails; pictures are attached inline.

### 7. When

New config section:

```yaml
projects:
  day: sunday      # one project email a week
  hour: 18
  min_applied: 3
  lookback_days: 120
```

Leaving the section out turns the feature off. It runs in the same scheduled job, after alerts
are sent, and any failure is logged and skipped, never stopping alerts. If it fails, it tries
again on the next run that day.

A local command, `python -m jobscanner --project-preview`, writes the email and files to an
ignored folder without sending anything, so the owner can check a real guide first. **(my call)**

## Where the code goes

- `jobscanner/projects.py`: gather applied-to jobs, the two requests, checks, counts.
- `jobscanner/guide.py`: pictures, PDF, Word, Markdown from the guide JSON.
- `jobscanner/emailer.py`: `render_project(...)`; `send` gains inline pictures.
- `jobscanner/tracker.py` and `tracker/Code.gs`: `Posting` column, `Guides` tab, guide page.
- `jobscanner/config.py`, `state.py`, `__main__.py`, `config.yaml`, workflow, README.

## Testing

- Unit tests with a fake Claude client: ID filtering, counts, step checks and the retry, dead
  reading links, nothing written to state except the date, nothing resume-derived in logs.
- File tests: PDF, Word and PNG open and contain the steps; Markdown has no contact details.
- Apps Script: the Node stub used today, plus a browser render of the guide page.
- Not testable here: real Claude output quality, Gmail rendering, the deployed script. The
  preview command exists so the owner can check these.

## Left to the builder

Exact prompts, picture layout rules, PDF typography, and how guide JSON is split across cells.
