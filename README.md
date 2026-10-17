# Job Agent

An AI agent that scans French job postings daily, scores each one against my
profile, drafts a tailored cover letter for the good ones, and emails me a
shortlist to review.

**It does not auto-submit applications.** That is a deliberate design choice,
not a missing feature — see [Why apply-assist](#why-apply-assist-and-not-auto-apply).

---

## What it does

```
France Travail API  →  dedupe against store  →  Claude judges each posting
                                                        ↓
                        HTML digest  ←  cover letter drafts  ←  shortlist
```

The agent is a **Claude tool-use loop**. Claude is the orchestrator: it chooses
which keyword searches to run, reads each vacancy, scores the fit, and writes
the letters. Python only supplies the tools and executes them. That is the
difference between an agent and a script that happens to call an LLM.

| Tool | What Claude uses it for |
|---|---|
| `get_profile` | Load targets, experience, hard constraints |
| `search_jobs` | Query live vacancies (returns only unseen ones) |
| `record_judgement` | Score 0–100 + apply/skip + reasoning |
| `draft_cover_letter` | Attach a French letter to a shortlisted role |
| `finish` | Close the run with a summary |

---

## Setup

```bash
git clone <your-repo> && cd job-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # then fill it in
```

Two credentials are needed:

1. **Anthropic API key** — <https://console.anthropic.com>
2. **France Travail** — <https://francetravail.io>. Create a free account,
   declare an application, subscribe it to *Offres d'emploi v2*. You get a
   client id and secret. No contract, no partner review, no sales call.

Then:

```bash
python -m src.agent --dry-run   # scan + score, no letters (cheap first run)
python -m src.agent             # full run
open data/digest.html
```

---

## Tuning it

Everything that controls behaviour lives in `profile.yaml`. Edit that, not the
code. The constraints block is load-bearing:

```yaml
constraints:
  never_claim:
    - "AWS Certified Cloud Practitioner as held"
  never_call_training: "bootcamp"
```

These are enforced in the system prompt. If a cover letter ever claims a
certification I do not hold, that is the bug to fix first.

Scoring is strict on purpose. Three real matches beat fifteen maybes — the
whole point is to cut review time, and a padded shortlist does the opposite.

---

## Why apply-assist and not auto-apply

Fully automated submission fails in three ways at once:

- **It gets caught.** ATS platforms run bot detection. A flagged account is
  worse than no account.
- **It breaks terms of service** on essentially every job board.
- **It produces worse applications.** Untouched generated applications read as
  generated and get filtered out. Volume is not the bottleneck; relevance is.

So the agent does the 90% that is genuinely mechanical — finding, filtering,
scoring, drafting — and stops at the submit button. Each shortlisted role takes
about two minutes to review and send.

---

## Roadmap

**Phase 2 — put it on AWS.** The deployment is the portfolio piece:

- Lambda on an EventBridge cron (07:00 Europe/Paris, weekdays)
- DynamoDB replaces SQLite — `store.py` is already behind an interface, so only
  that file changes
- SES sends the digest
- Secrets Manager for the two API keys
- Terraform for all of it

**Phase 3 — more sources.** APEC and Welcome to the Jungle for tech roles;
LinkedIn stays manual (no usable API, aggressive automation detection).

**Phase 4 — outcome feedback.** Record which applications got replies, and feed
that back into scoring. That turns the score from a guess into something
calibrated against reality.

---

## Layout

```
job-agent/
├── profile.yaml              # all behaviour config
├── .env                      # credentials (gitignored)
└── src/
    ├── agent.py              # the tool loop
    ├── store.py              # SQLite → DynamoDB seam
    ├── digest.py             # self-contained HTML output
    └── sources/
        └── france_travail.py # OAuth2 + search + normalisation
```

Source: job data from the France Travail *Offres d'emploi* API.

---

## Tests

```bash
python -m tests.test_agent
```

39 checks, no credentials and no network — fixtures stand in for the API and a
scripted fake client stands in for the model. Everything else is the real code
path, including the guard rejecting a bad letter and the loop retrying.
