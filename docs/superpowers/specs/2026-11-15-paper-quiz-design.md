# Paper emails with a quiz: design

Date: 2026-11-15. Status: awaiting approval.

Lines marked **(my call)** were decided by Claude, not by the repo owner, and are open to change.
The research behind the choices is in `reports/Security and network engineering roles.md`
(kept out of git).

## Why

The owner wants to work in security engineering at top companies, or network engineering at
a quant firm or SpaceX. The research found that each of those jobs has a small body of papers
that describes the real work better than any posting, and that interviews test reasoning from
fundamentals. Reading one such paper at a time and being tested on it is a direct way to get
sharper, and a log of results shows where the gaps are.

## What the owner asked for

- An email with a link to a popular research paper on cybersecurity, network engineering, or
  both.
- When a frontier lab releases a paper, the email carries that one.
- Selection guided by what people in the target roles actually do.
- Practice questions based on the paper.
- A simple application that logs the quizzes in a dashboard.
- Frequency: "every 3 days, more on the weekend".

## Goals

1. Three times a week, email one paper worth the owner's time, with a link, a short plain
   summary and why it matters for the target roles.
2. Give each paper a quiz that tests understanding and the fundamentals behind it, graded on
   the spot, with every attempt logged.
3. Show a dashboard of papers read, scores, and the topics missed most often.
4. Put frontier-lab security and networking releases ahead of everything else.

## Non-goals

- Hosting or re-publishing papers. The email links to the paper's own page.
- A separate hosted application or login. The quiz and dashboard are pages in the owner's
  existing Sheet web app, like the dashboard and project guides. **(my call)**
- Personalising to the resume. Nothing here uses or sends the resume.
- Spaced repetition of missed questions (a natural later step; the log makes it possible).

## Anti-goals

- Wrong quiz answers presented as fact. Questions are written by a model and can be wrong, so
  every answer shows where in the paper it comes from, and any question can be flagged.
- Stale or irrelevant picks: an old paper resurfacing as "new", or a lab's product
  announcement treated as research.
- Hammering the sources. Every source is asked at most once per paper day, within its
  published limits.
- Paper text steering the model into putting links, commands or anything but questions into
  the quiz.

## How it works

### 1. When

`every 3 days, more on the weekend` is read as **Wednesday, Saturday and Sunday** at 09:00
Eastern: one mid-week, two at the weekend. **(my call)** Config:

```yaml
papers:
  days: [wednesday, saturday, sunday]
  hour: 9
```

Leaving the section out turns the feature off. It runs in the existing scheduled job, after
alerts are sent and state is saved, and any failure is logged and skipped. At most three
attempts on a paper day.

### 2. Which paper

Three lanes, checked in this order:

1. **Frontier lab.** A security or networking release from a watched lab that appeared since
   the last paper email. Watched, from the research: Anthropic's Frontier Red Team page (no
   feed, so the page's links are read), OpenAI news feed, Google Project Zero, Google Security
   Blog, Google DeepMind blog, Cloudflare research. **(my call on the list; it is a constant
   that can be edited.)** Labs publish most security work as posts, so a post counts as a
   paper. A small, fast model reads each new item's title and summary and says whether it is
   security or networking research (not a product story); only those qualify.
2. **Role-defining list.** A fixed list of about 20 works the research identified for the three
   target roles (for example CaMeL and the indirect prompt injection paper; "Network Design
   Considerations for Trading Systems" and the Chicago to New Jersey microwave study; the
   Starlink measurement papers and "Delay is Not an Option"). One per email, interleaving the
   three roles, until the list is used up. Each link is checked when the list is built and
   again before sending. **(my call: these come before fresh picks, because they are worth
   more than whatever was posted this week.)**
3. **Fresh and popular.** New arXiv papers in cs.CR and cs.NI from the last 30 days, ranked by
   Hacker News points, a top venue named in the arXiv comment (S&P, USENIX Security, CCS, NDSS,
   SIGCOMM, NSDI, IMC, HotNets, CoNEXT), and whether the small model judges it relevant to the
   target roles. A paper is dated by its arXiv submission date, never by when it was discussed,
   so old papers that resurface are not picked as new.

A paper is never sent twice. The state file keeps the ids of papers sent and the date of the
last one; these are public facts about public papers, so they are safe in a public repo.

### 3. The quiz

The paper's text is fetched (arXiv's HTML or PDF, or the post's page) and given to Claude with
the target-role topics. It returns, as JSON checked against a schema:

- a 3-sentence plain summary and a 2-sentence "why this matters for your target roles";
- **6 multiple-choice questions**: four options, exactly one correct, a one-or-two sentence
  explanation, the section of the paper it comes from, and a topic tag (for example "BGP",
  "threat modeling", "latency arithmetic");
- **2 open questions** of the kind an interviewer would ask, tying the paper to fundamentals,
  each with a model answer and the 3 to 4 points a good answer makes.

Checks in code: exactly 6 and 2; four distinct options with one correct; the correct option
is not always in the same position; no web addresses anywhere in the quiz; every field
present. One retry naming the failures, then skip. The paper's text is marked as data in the
request, and since no resume or personal detail is sent, there is nothing for a hostile
document to extract.

### 4. The email

Same dark style as the project email. Headline (the paper's title), authors and venue or lab,
a "Read the paper" button to the paper's own page, the summary, why it matters, how long it is
to read, and a "Take the quiz" button. Questions are not in the email.

Without the tracker there is nowhere to host the quiz, so the email then lists the questions
with the answers at the very bottom. **(my call)**

### 5. The quiz page and the dashboard

Two new pages in the Sheet web app, behind the existing token:

- **Quiz** (`a=quiz&id=`): one question at a time. Multiple choice is graded the moment an
  option is chosen and shows the explanation and section. Open questions take a typed answer,
  then show the model answer and its points; the owner marks "got it", "partly" or "missed".
  Each question has a "This looks wrong" button. Results are saved as they happen.
- **Papers** (`a=papers`): every paper sent with its date, source, score and link to its quiz;
  totals (papers read, average score, current streak); and the topics missed most.

Data lives in two new tabs: `Papers` (when, id, title, link, source, quiz JSON) and
`Quiz log` (when, paper id, question, topic, result, flagged). All text is placed on the page
as text, never as markup, as in the existing pages.

### 6. Requirements and cost

Needs `ANTHROPIC_API_KEY`. The tracker is needed for the quiz page and dashboard. The resume
is not needed. One large request per paper on `claude-opus-5-5` (the paper is long) plus a
few small ones on `claude-haiku-5-5`.

A local command, `python -m jobscanner --paper-preview`, writes the email and quiz to an
ignored folder without sending, using a built-in sample when no key is set.

## Where the code goes

- `jobscanner/papers.py`: the three lanes, sources, ranking, the fixed list, the quiz request
  and its checks.
- `jobscanner/emailer.py`: `render_paper(...)`.
- `jobscanner/tracker.py`, `tracker/Code.gs`: save a paper and quiz; quiz page; papers page; log.
- `jobscanner/config.py`, `state.py`, `__main__.py`, `config.yaml`, README.

## Testing

- Unit tests with fake sources and a fake Claude client: lane order, a lab item pre-empting,
  no repeats, resurfaced old papers rejected, quiz checks and the retry, the 3-try cap,
  failures never stopping alerts, nothing but ids and dates in state.
- Apps Script: the Node stand-in used today, plus a browser render of both pages.
- Live checks of each source's reply format while building.
- Not testable here: real quiz quality, Gmail rendering, the deployed script.

## Left to the builder

Exact prompts, ranking weights, page layout details, and the final fixed list (every link
verified before it goes in).
