"""Test suite — no credentials, no network.

A fake Anthropic client replays a scripted tool-use conversation, and fixture
payloads stand in for the France Travail API. Everything else is the real code:
the real loop, the real normalisation, the real store, the real guards.

Run: python -m tests.test_agent
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from src.digest import render_digest  # noqa: E402
from src.guards import check_letter  # noqa: E402
from src.sources.france_travail import FranceTravailClient, Job  # noqa: E402
from src.store import JobStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROFILE = yaml.safe_load((ROOT / "profile.yaml").read_text(encoding="utf-8"))

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    mark = "ok  " if condition else "FAIL"
    print(f"  {mark}  {name}" + (f"  — {detail}" if detail and not condition else ""))


# --------------------------------------------------------------------------
# Fixtures: real France Travail response shape
# --------------------------------------------------------------------------

RAW_OFFERS = [
    {
        "id": "195FKTL",
        "intitule": "Technicien support informatique N2 (H/F)",
        "description": "Au sein de notre centre de services, vous assurez le "
        "support N2 des environnements Windows et Linux, la gestion des "
        "incidents et des escalades, et participez a la migration cloud AWS "
        "de nos clients. Experience en gestion d'incidents appreciee.",
        "dateCreation": "2026-10-16T07:12:33.000Z",
        "lieuTravail": {"libelle": "93 - SAINT-DENIS"},
        "entreprise": {"nom": "Claranet France"},
        "typeContrat": "CDI",
        "typeContratLibelle": "Contrat a duree indeterminee",
        "salaire": {"libelle": "Annuel de 32000.0 Euros a 36000.0 Euros sur 12 mois"},
        "origineOffre": {
            "urlOrigine": "https://candidat.francetravail.fr/offres/recherche/detail/195FKTL"
        },
    },
    {
        "id": "188QRZM",
        "intitule": "Administrateur systemes Linux",
        "description": "Vous administrez un parc de serveurs Linux, "
        "automatisez via Ansible et Terraform, et participez a l'astreinte.",
        "dateCreation": "2026-10-16T05:40:00.000Z",
        "lieuTravail": {"libelle": "91 - MASSY"},
        "entreprise": {},  # company withheld — common, must not crash
        "typeContrat": "CDI",
        "origineOffre": {},  # missing URL — also common
    },
]


# --------------------------------------------------------------------------
# Fake Anthropic client
# --------------------------------------------------------------------------


@dataclass
class _Block:
    type: str
    id: str = ""
    name: str = ""
    input: dict = None
    text: str = ""


@dataclass
class _Response:
    content: list
    stop_reason: str


class FakeMessages:
    """Replays a scripted set of turns, recording what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return _Response([_Block(type="text", text="done")], "end_turn")
        return self.script.pop(0)


class FakeAnthropic:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def tool_turn(*calls) -> _Response:
    blocks = [
        _Block(type="tool_use", id=f"tu_{i}", name=n, input=a)
        for i, (n, a) in enumerate(calls)
    ]
    return _Response(blocks, "tool_use")


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_normalisation():
    print("\nNormalisation")
    jobs = [FranceTravailClient._normalise(o) for o in RAW_OFFERS]

    check("id is namespaced", jobs[0].id == "ft-195FKTL", jobs[0].id)
    check("title parsed", "Technicien support" in jobs[0].title)
    check("company parsed", jobs[0].company == "Claranet France")
    check("contract prefers the readable label",
          jobs[0].contract == "Contrat a duree indeterminee", jobs[0].contract)
    check("salary parsed", "32000" in jobs[0].salary)
    check("missing company does not crash", jobs[1].company == "")
    check("missing URL does not crash", jobs[1].url == "")
    check("falls back to typeContrat", jobs[1].contract == "CDI", jobs[1].contract)

    block = jobs[0].to_prompt_block()
    check("prompt block carries the id", "[ft-195FKTL]" in block)
    check("prompt block truncates long text",
          len(jobs[0].to_prompt_block(max_chars=50)) < 400)


def test_store(tmp: Path):
    print("\nStore")
    store = JobStore(tmp / "t.db")
    jobs = [FranceTravailClient._normalise(o) for o in RAW_OFFERS]

    check("unseen ids come back as new",
          store.filter_new([j.id for j in jobs]) == {"ft-195FKTL", "ft-188QRZM"})

    store.record(jobs[0], 84, "apply", "Strong match.")
    store.record(jobs[1], 30, "skip", "Too senior.")

    check("recorded ids are no longer new", store.filter_new(["ft-195FKTL"]) == set())
    check("stats split by status",
          store.stats() == {"shortlisted": 1, "rejected": 1}, str(store.stats()))
    check("shortlist excludes rejects", len(store.shortlist()) == 1)

    store.record(jobs[0], 84, "apply", "Strong match.", "Madame, Monsieur...")
    check("re-record attaches letter without duplicating",
          len(store.shortlist()) == 1
          and store.shortlist()[0]["cover_letter"].startswith("Madame"))

    store.set_status("ft-195FKTL", "applied")
    check("status transition sticks", store.stats().get("applied") == 1)
    check("applied drops off the shortlist", store.shortlist() == [])


def test_guards():
    print("\nGuards")
    bad_cert = (
        "Madame, Monsieur,\n\nJe suis certifie AWS Cloud Practitioner et je "
        "souhaite rejoindre vos equipes."
    )
    check("catches a falsely claimed certification",
          len(check_letter(bad_cert, PROFILE)) > 0)

    bad_word = "J'ai suivi un bootcamp cloud de 400 heures chez Ironhack."
    check("catches the forbidden training word",
          any("bootcamp" in v for v in check_letter(bad_word, PROFILE)))

    good = (
        "Madame, Monsieur,\n\nJ'ai suivi le Cloud Engineering Course chez "
        "Ironhack (400 heures). Je prepare actuellement la certification AWS "
        "Cloud Practitioner, prevue pour novembre 2026.\n\nCordialement,"
    )
    check("passes a correct letter", check_letter(good, PROFILE) == [],
          str(check_letter(good, PROFILE)))

    hedged = "La certification AWS Cloud Practitioner est en preparation."
    check("allows a mention marked en preparation",
          check_letter(hedged, PROFILE) == [], str(check_letter(hedged, PROFILE)))


def test_agent_loop(tmp: Path):
    print("\nAgent loop")
    from src import agent as agent_mod

    script = [
        tool_turn(("get_profile", {})),
        tool_turn(("search_jobs", {"keywords": "technicien support informatique"})),
        tool_turn(
            ("record_judgement", {
                "job_id": "ft-195FKTL", "score": 84, "verdict": "apply",
                "reasoning": "Priority title; escalation background maps directly."}),
            ("record_judgement", {
                "job_id": "ft-188QRZM", "score": 72, "verdict": "apply",
                "reasoning": "Linux admin is a priority title."}),
        ),
        # Deliberately bad letter — the guard must reject it.
        tool_turn(("draft_cover_letter", {
            "job_id": "ft-195FKTL",
            "letter": "Je suis certifie AWS Cloud Practitioner."})),
        # The retry, corrected.
        tool_turn(("draft_cover_letter", {
            "job_id": "ft-195FKTL",
            "letter": "Madame, Monsieur,\n\nVotre poste de support N2 avec "
                      "migration AWS correspond a mon parcours. Je prepare la "
                      "certification AWS Cloud Practitioner.\n\nCordialement,"})),
        tool_turn(("finish", {"summary": "Scanned 2 vacancies, shortlisted 2."})),
    ]

    a = agent_mod.JobAgent.__new__(agent_mod.JobAgent)
    a.client = FakeAnthropic(script)
    a.ft = type("FT", (), {
        "search": lambda self, **kw: [
            FranceTravailClient._normalise(o) for o in RAW_OFFERS]
    })()
    a.store = JobStore(tmp / "loop.db")
    a.profile = PROFILE
    a.dry_run = False
    a.jobs, a.judged = {}, {}
    a.done, a.summary, a.rejected_letters = False, "", 0

    result = a.run()

    check("loop terminates on finish", a.done)
    check("summary captured", "Scanned 2" in result["summary"])
    check("both vacancies ingested", result["seen_this_run"] == 2)
    check("both judged", result["judged"] == 2)
    check("guard rejected the bad letter", a.rejected_letters == 1)

    sl = {r["id"]: r for r in a.store.shortlist()}
    check("corrected letter was saved",
          sl["ft-195FKTL"]["cover_letter"].startswith("Madame"))
    check("no letter saved where none drafted",
          sl["ft-188QRZM"]["cover_letter"] is None)
    check("shortlist ordered by score",
          [r["score"] for r in a.store.shortlist()] == [84, 72])

    # The loop must feed tool results back, not just fire and forget.
    last = a.client.messages.calls[-1]["messages"]
    check("conversation accumulates", len(last) > 6, f"{len(last)} messages")
    check("tools were advertised every turn",
          all("tools" in c for c in a.client.messages.calls))


def test_dedupe_across_runs(tmp: Path):
    print("\nDedupe across runs")
    store = JobStore(tmp / "dedupe.db")
    jobs = [FranceTravailClient._normalise(o) for o in RAW_OFFERS]
    for j in jobs:
        store.record(j, 80, "apply", "x")

    a = type("A", (), {})()
    a.store, a.jobs, a.profile = store, {}, PROFILE
    a.ft = type("FT", (), {"search": lambda self, **kw: jobs})()

    from src.agent import JobAgent
    out = JobAgent._search_jobs(a, "technicien support")
    check("second run reports nothing new", "No new vacancies" in out, out[:70])
    check("nothing re-ingested", a.jobs == {})


def test_digest(tmp: Path):
    print("\nDigest")
    store = JobStore(tmp / "d.db")
    j = FranceTravailClient._normalise(RAW_OFFERS[0])
    store.record(j, 84, "apply", "Strong match on a priority title.",
                 "Madame, Monsieur,\n\n<script>alert(1)</script>\n\nCordialement,")

    out = render_digest(store.shortlist(), tmp / "digest.html")
    html_text = out.read_text(encoding="utf-8")

    check("file written", out.exists())
    check("self-contained (no external assets)",
          "http://" not in html_text.replace("http://www.w3.org", ""))
    check("job title rendered", "Technicien support" in html_text)
    check("script tag escaped", "<script>alert" not in html_text)
    check("dark mode handled", "prefers-color-scheme" in html_text)
    check("viewport set for phone", "width=device-width" in html_text)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    print("=" * 62)
    print("job-agent v1 — test suite (fixtures + fake model, no network)")
    print("=" * 62)
    try:
        test_normalisation()
        test_store(tmp)
        test_guards()
        test_agent_loop(tmp)
        test_dedupe_across_runs(tmp)
        test_digest(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
