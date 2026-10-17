"""The agent loop.

Claude is the orchestrator: it decides which searches to run, judges each
vacancy against the profile, and writes the cover letters. The Python side
only provides tools and executes them. That is what makes this an agent
rather than a script with an LLM call bolted on.

Run:  python -m src.agent            (normal daily run)
      python -m src.agent --dry-run  (no cover letters, cheaper)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml
from anthropic import Anthropic

from .config import describe_config, load_env
from .digest import render_digest
from .guards import check_letter
from .sources import DEPARTMENTS, Job, MultiSource
from .store import JobStore

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("job-agent")

ROOT = Path(__file__).resolve().parent.parent

# Must happen before anything reads os.environ.
_ENV = load_env(ROOT / ".env")

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
MAX_TURNS = 40
MAX_OUTPUT_TOKENS = 8192
# Caps that keep one run inside a sane context and cost.
RESULTS_PER_SEARCH = 15
MAX_JOBS_PER_RUN = 60

SYSTEM_PROMPT = """\
You are a job-search agent working for one candidate. Your job each run:

1. Call get_profile first. Everything downstream depends on it.
2. Run several search_jobs calls with DIFFERENT keyword angles — French job
   titles, English ones, and adjacent operational titles.

   KEYWORD RULES — these matter, the search engine ANDs every word together:
     - Use ONE or TWO words per search. Never a full job-title phrase.
     - NEVER put a location in the keywords. Location is applied separately.
     - NEVER add "junior", "senior", "CDI", "H/F" — they shrink results to
       nothing. Judge seniority from the description instead.
     Good: "administrateur systeme" / "support cloud" / "devops" / "linux"
     Run MANY of these narrow searches rather than a few wide ones.
     Bad:  "cloud support engineer Ile-de-France" / "junior DevOps CDI Paris"

   French postings often use: "administrateur systeme", "technicien support",
   "ingenieur cloud", "exploitation informatique", "infogerance", "infra".

   TWO TRACKS. The profile has `targets` (cloud/DevOps) and, when
   `secondary_track.active` is true, an operations/ADV track. Search BOTH
   every run — roughly two thirds of searches on the cloud track, one third
   on the operations track. Operations keywords: "ADV", "administration
   ventes", "gestionnaire commandes", "service client", "coordinateur
   logistique", "support client".

   Score each posting against the track it belongs to, never the other one.
   An ADV role is judged on ten years of operations, escalation and team
   leadership — not on cloud skills, and it is NOT a lesser option. A cloud
   role is judged on the training and transferable ops experience.
   Begin every `reasoning` with "[cloud]" or "[ops]" so the track is visible
   in the digest.
3. Judge each batch IMMEDIATELY after its search, before searching again.
   Do not collect many searches and judge at the end — you will run out of
   room. Search, judge those results, then search again.

   For every vacancy returned, judge it against the profile and call
   record_judgement. Be honest and be strict — a shortlist of 3 real matches
   beats 15 maybes. Score 0-100:
     - 80+  strong fit, apply now
     - 60-79 worth applying, some stretch
     - below 60 do not shortlist
   Weight the profile's priority_titles higher. Do NOT reject on commute
   distance. Do NOT reject because the candidate lacks a certification that
   is listed as in preparation unless the posting makes it a hard requirement.
4. For each job you score 70+, call draft_cover_letter and write a real
   letter in the profile's default_draft_language.

HARD GATES — check these BEFORE scoring anything else. The profile's
`hard_limits` block lists what the candidate cannot provide. If a posting
states any of them as a REQUIREMENT, score it below 40 and verdict "skip",
no matter how well the rest matches. Begin the reasoning with
"[gate: <what blocks it>]" so the reason is visible.

  - A language at courant/bilingue level that is NOT in languages_spoken.
    German, Spanish, Italian, Dutch, Mandarin and the rest are not spoken.
  - A named professional certification stated as required.
  - A named ERP/CRM demanded as "maîtrise éprouvée" when it is listed in
    no_deep_expertise_in.
  - A stated minimum degree above no_formal_degree_above (Bac+5,
    ingénieur, master).

French postings mark a hard gate with: "exigé", "impératif",
"indispensable", "obligatoire", "critère éliminatoire", "requis".
They mark a PREFERENCE with: "souhaité", "apprécié", "un plus",
"idéalement", "serait un atout". A preference is NOT a gate — do not
skip for those, and do not deduct heavily either.

Judge the wording, not the topic. "Anglais courant exigé" is fine — he
speaks it. "Allemand courant exigé" is a gate. "AWS apprécié" is fine;
"certification AWS exigée" is a gate.

Seniority mismatch is NOT a gate, but flag it: a posting written for a
junior (first experience, stage/apprentissage, BTS fresh graduate) that
the candidate is far beyond should score no higher than 65, with the
reasoning saying so.

Hard rules, no exceptions:
- Never state a certification as held if the profile lists it as in
  preparation. Write "en preparation" or omit it.
- Never call the Ironhack training a "bootcamp". It is a "Cloud Engineering
  Course".
- Never claim a skill the candidate could not defend in an interview.
- Cover letters must reference something specific from the actual posting.
  A letter that could be sent to any employer is a failed letter.

For an [ops] role, the letter leads with the operations record (CNH team
leadership, Veepee order and stock management, SLA-bound escalation work) and
mentions the cloud training only if the posting touches systems or tooling.
For a [cloud] role, it bridges that same operations record toward the
technical requirement.

Cover letter shape (French, ~250 words, no filler):
  - Opening: the role, and why this employer specifically.
  - Middle: bridge the operations/escalation background to the technical
    requirement in the posting. Concrete, not adjectival.
  - Close: the training, certifications in preparation, availability.

When you have judged every vacancy, call finish with a two-sentence summary.
"""

TOOLS = [
    {
        "name": "get_profile",
        "description": "Load the candidate's profile: targets, experience, constraints.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_jobs",
        "description": (
            "Search live French vacancies. Returns only postings not already "
            "seen in previous runs. Call several times with different keywords."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "description": "Search terms, French or English, e.g. 'ingenieur cloud'",
                },
                "since_days": {
                    "type": "integer",
                    "description": "How far back to look. Default 2 for a daily run.",
                },
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "record_judgement",
        "description": "Record your verdict on one vacancy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "score": {"type": "integer", "description": "0-100 fit score"},
                "verdict": {"type": "string", "enum": ["apply", "skip"]},
                "reasoning": {
                    "type": "string",
                    "description": "Two sentences max. Why this score. Name the deciding factor.",
                },
            },
            "required": ["job_id", "score", "verdict", "reasoning"],
        },
    },
    {
        "name": "draft_cover_letter",
        "description": "Attach a cover letter to a shortlisted vacancy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "letter": {"type": "string", "description": "Full letter text."},
            },
            "required": ["job_id", "letter"],
        },
    },
    {
        "name": "finish",
        "description": "End the run.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]


class JobAgent:
    def __init__(self, dry_run: bool = False):
        self.client = Anthropic()
        self.ft = MultiSource()
        self.store = JobStore()
        self.profile = yaml.safe_load((ROOT / "profile.yaml").read_text(encoding="utf-8"))
        self.dry_run = dry_run
        self.jobs: dict[str, Job] = {}      # job_id -> Job, this run
        self.judged: dict[str, dict] = {}   # job_id -> verdict payload
        self.done = False
        self.summary = ""
        self.rejected_letters = 0

    # -- tool implementations ----------------------------------------------

    def _get_profile(self) -> str:
        return yaml.safe_dump(self.profile, allow_unicode=True, sort_keys=False)

    def _search_jobs(self, keywords: str, since_days: int = 2) -> str:
        try:
            found = self.ft.search(
                keywords=keywords,
                departments=list(DEPARTMENTS),
                since_days=since_days,
                # No server-side salary filter — it discards postings that
                # state no salary, which is most of them. The model weighs
                # the profile's min_salary_eur from the description instead.
                min_salary_eur=None,
            )
        except Exception as exc:  # noqa: BLE001 - surface to the model, keep going
            log.warning("search failed for %r: %s", keywords, exc)
            return f"Search failed: {exc}. Try different keywords."

        fresh_ids = self.store.filter_new([j.id for j in found])
        fresh = [j for j in found if j.id in fresh_ids and j.id not in self.jobs]

        # Keep the newest few per search; the model cannot usefully weigh 40
        # postings in one turn and the context cost is real.
        fresh = fresh[:RESULTS_PER_SEARCH]

        room = MAX_JOBS_PER_RUN - len(self.jobs)
        truncated = False
        if room <= 0:
            return (
                f"Budget reached ({MAX_JOBS_PER_RUN} vacancies this run). "
                "Stop searching. Judge anything outstanding, then call finish."
            )
        if len(fresh) > room:
            fresh, truncated = fresh[:room], True

        for j in fresh:
            self.jobs[j.id] = j

        log.info("  %-34s %2d new / %2d found", keywords, len(fresh), len(found))
        if not fresh:
            return f"No new vacancies for '{keywords}' ({len(found)} already seen)."
        body = "\n\n---\n\n".join(j.to_prompt_block(max_chars=900) for j in fresh)
        if truncated:
            body += "\n\n[Run budget reached — judge these, then finish.]"
        return body

    def _record_judgement(self, job_id: str, score: int, verdict: str, reasoning: str) -> str:
        job = self.jobs.get(job_id)
        if not job:
            return f"Unknown job_id {job_id}. Use an id from a search result."
        self.judged[job_id] = {"score": score, "verdict": verdict, "reasoning": reasoning}
        self.store.record(job, score, verdict, reasoning)
        return f"Recorded {job_id}: {verdict} ({score})."

    def _draft_cover_letter(self, job_id: str, letter: str) -> str:
        job = self.jobs.get(job_id)
        if not job:
            return f"Unknown job_id {job_id}."

        # Hard guard. The prompt states the rules; this enforces them.
        violations = check_letter(letter, self.profile)
        if violations:
            self.rejected_letters += 1
            log.warning("  letter REJECTED (%s): %s", job_id, "; ".join(violations))
            return (
                "Letter rejected, not saved. Violations: "
                + "; ".join(violations)
                + ". Rewrite it respecting the constraints and call "
                "draft_cover_letter again."
            )

        if self.dry_run:
            return "Dry run — letter not saved."
        j = self.judged.get(job_id, {})
        self.store.record(
            job, j.get("score", 0), j.get("verdict", "apply"), j.get("reasoning", ""), letter
        )
        log.info("  letter drafted: %s", job.title[:50])
        return f"Letter saved for {job_id}."

    def _finish(self, summary: str) -> str:
        self.done = True
        self.summary = summary
        return "Run closed."

    def _dispatch(self, name: str, args: dict) -> str:
        handlers = {
            "get_profile": self._get_profile,
            "search_jobs": self._search_jobs,
            "record_judgement": self._record_judgement,
            "draft_cover_letter": self._draft_cover_letter,
            "finish": self._finish,
        }
        handler = handlers.get(name)
        if not handler:
            return f"Unknown tool {name}."
        try:
            return handler(**args)
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return f"Tool error: {exc}"

    # -- the loop -----------------------------------------------------------

    def run(self) -> dict:
        messages = [{"role": "user", "content": "Start today's run."}]

        for turn in range(MAX_TURNS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "max_tokens":
                # It ran out of room mid-answer. Ask it to continue in
                # smaller pieces rather than treating this as the end.
                log.warning("Output limit hit on turn %d — continuing.", turn)
                messages.append({
                    "role": "user",
                    "content": (
                        "You hit the output limit. Continue from where you "
                        "stopped, a few tool calls at a time."
                    ),
                })
                continue

            if response.stop_reason != "tool_use":
                log.info("Model stopped without finishing (turn %d).", turn)
                break

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                output = self._dispatch(block.name, block.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output}
                )

            messages.append({"role": "user", "content": results})

            if self.done:
                break
        else:
            log.warning("Hit the %d-turn ceiling.", MAX_TURNS)

        return {
            "summary": self.summary,
            "seen_this_run": len(self.jobs),
            "judged": len(self.judged),
            "shortlist": self.store.shortlist(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily job-search agent")
    parser.add_argument("--dry-run", action="store_true", help="skip cover letters")
    parser.add_argument("--no-digest", action="store_true", help="skip HTML output")
    args = parser.parse_args()

    log.info("Config: %s", describe_config(_ENV))

    try:
        agent = JobAgent(dry_run=args.dry_run)
    except RuntimeError as exc:
        log.error("%s", exc)
        log.error("Check that .env sits next to README.md and has no .txt extension.")
        return 2
    result = agent.run()

    log.info("")
    log.info("%s", result["summary"] or "(no summary)")
    log.info(
        "Scanned %d new vacancies, judged %d, shortlist now %d.",
        result["seen_this_run"],
        result["judged"],
        len(result["shortlist"]),
    )

    if not args.no_digest and result["shortlist"]:
        out = ROOT / "data" / "digest.html"
        render_digest(result["shortlist"], out)
        log.info("Digest: %s", out)

    print(json.dumps(agent.store.stats(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
