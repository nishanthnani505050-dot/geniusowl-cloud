# GeniusOwl Job Agent

Daily matched jobs, adjacent-role expansion, ATS resume tailoring, and applying
— all inside WhatsApp. The user touches the website once, to upload a resume.

## What it does

1. **Understands the resume** (`resume.py`) — one LLM pass extracts title,
   skills, seniority, city, achievements. Nothing is invented.
2. **Expands to adjacent roles** (`taxonomy.py`) — the core idea. An M&A analyst
   is searched for corporate strategy, management consulting, commercial due
   diligence, commercial strategy, transaction advisory and strategic finance.
   A sales consultant is searched for GTM, presales, solutions, customer
   success, partnerships and marketing. Nine families are hand-built; anything
   unknown falls through to an LLM expansion.
3. **Fetches** (`sources/`) from pluggable adapters, one query per adjacent
   title. That's why it finds jobs a normal search never surfaces.
4. **Scores and ranks** (`matcher.py`) — cheap deterministic scoring over every
   posting (keyword overlap, family fit, location, recency, seniority sanity),
   then an LLM re-rank over the top ~25 only. Top 10, max 2 per company.
5. **Sends the digest** (`daily.py` + `whatsapp.py`) as an approved template,
   then a native 10-row interactive list.
6. **Applies on confirmation** (`conversation.py` + `tailor.py` + `apply.py`) —
   rewrites the resume in the JD's vocabulary using only facts already in the
   resume, asks for any missing detail once and caches it forever, then applies.

## The compliance decision you need to make

LinkedIn and Naukri both prohibit scraping and automated applications in their
terms of service. LinkedIn fingerprints aggressively and bans accounts. Building
the product on a scraper means your users' primary job-search accounts are the
thing at risk — and a banned LinkedIn account is a worse outcome for a job
seeker than a missed interview.

So the shipped adapters are all legitimate: Greenhouse and Lever publish
documented public JSON board APIs, Adzuna has an official API with good India
coverage (and indexes much of what Naukri lists), and `FeedSource` reads any
RSS/JSON feed a publisher intends for machines.

Applying works the same way. `AssistedApplyChannel` is the default: the agent
tailors the resume, computes the ATS keyword coverage, and sends a one-tap link
on WhatsApp. The user taps once. That converts nearly as well as full
automation, carries zero account risk, and works on *every* board including
LinkedIn and Naukri. `EmailApplyChannel` is fully automatic wherever the posting
gives an email. `FormApplyChannel` is a documented stub — implement it yourself
if you decide to take on hands-off ATS submission.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # fill it in
python -m jobagent.cli initdb
```

**WhatsApp:** create a Meta Cloud API app, get a permanent system-user token,
point the webhook at `https://<your-host>/whatsapp/webhook` with your
`WHATSAPP_VERIFY_TOKEN`, and get one template approved. The daily digest MUST
be a template — Meta only allows free-form messages within 24 hours of the
user's last reply. Suggested body:

> Hi {{1}}, I found {{2}} new roles matching your profile today — top one is
> {{3}}. Reply JOBS to see all of them.

**Schedule:** run the daily job hourly; it only messages users whose
`daily_hour_ist` matches, so people get their batch when they check their phone.

```bash
curl -X POST -H "Authorization: Bearer $JOBAGENT_ADMIN_TOKEN" \
     https://<your-host>/jobagent/run
```

## Onboarding a user

```bash
curl -X POST https://<your-host>/jobagent/resume \
  -H "Authorization: Bearer $JOBAGENT_ADMIN_TOKEN" \
  -F file=@resume.pdf -F wa_number=919876543210 -F city=Bengaluru
```

The response echoes the adjacent titles it will search — show that back on the
website, it's the moment the user understands what they bought.

## Operating it

```bash
python -m jobagent.cli add --file resume.pdf --wa 919876543210 --city Bengaluru
python -m jobagent.cli preview --user <id>    # score today's jobs, send nothing
python -m jobagent.cli daily --force
python -m jobagent.cli tailor --user <id> --job <job_id>
python -m tests.test_jobagent                 # offline, no API keys
```

`preview` is the tuning loop. Run it against real profiles before you send a
single WhatsApp message — the ranking is where this product lives or dies.

## WhatsApp commands

| User sends | Agent does |
|---|---|
| `JOBS` | today's 10, as text + interactive list |
| `1 3 5` | tailors and applies to those three |
| `ALL` | applies to all 10 |
| `STATUS` | last 15 applications and their state |
| `CITY Pune` | changes the search city |
| `PAUSE` / `RESUME` | stops / restarts the daily message |

## Where to extend

- `sources/` — add a board by subclassing `JobSource` and `@register`.
- `taxonomy.ROLE_FAMILIES` — add a family; it needs `seeds`, `titles`, `keywords`.
- `apply.py` — add an `ApplyChannel` for a specific ATS.
- Ranking quality: log which digest items the user actually taps, and feed that
  back as a per-user weight on `matched_keywords`. That signal compounds and is
  the real moat.
