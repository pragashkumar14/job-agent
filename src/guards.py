"""Hard guards on generated text.

The system prompt tells the model the rules. This enforces them. Prompts are
persuasion; code is a guarantee. A cover letter that claims a certification
Pragash does not hold would be actively damaging in an interview, so it must
not be possible for one bad generation to reach the digest.
"""

from __future__ import annotations

import re

FORBIDDEN_TRAINING_WORDS = ["bootcamp", "boot camp", "boot-camp"]

# Phrases that assert possession of a credential. French and English.
_HELD_PATTERNS = [
    r"\b(certifi[ée]?|certified)\b",
    r"\bj'?ai\s+obtenu\b",
    r"\btitulaire\s+(?:de|du|d')\b",
    r"\bj'?ai\s+pass[ée]\s+avec\s+succ[èe]s\b",
    r"\bi\s+hold\b",
    r"\bi\s+(?:have\s+)?(?:earned|obtained|achieved)\b",
]

# "en préparation" and friends make a credential mention legitimate.
_IN_PREP_PATTERNS = [
    r"en\s+pr[ée]paration",
    r"en\s+cours\s+(?:de\s+pr[ée]paration|d'obtention|de\s+passage)",
    r"pr[ée]vue?\b",
    r"je\s+pr[ée]pare",
    r"in\s+preparation",
    r"currently\s+preparing",
    r"scheduled\s+for",
]


class GuardViolation(Exception):
    """Raised when generated text breaks a hard constraint."""


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", text) if s.strip()]


def check_letter(letter: str, profile: dict) -> list[str]:
    """Return a list of violations. Empty list means the letter is clean."""
    violations: list[str] = []
    lowered = letter.lower()

    banned = profile.get("constraints", {}).get("never_call_training")
    if banned:
        for word in FORBIDDEN_TRAINING_WORDS:
            if word in lowered:
                violations.append(f"uses forbidden word for the training: {word!r}")

    in_prep = profile.get("certifications", {}).get("in_preparation", [])
    for cert in in_prep:
        # Match on the distinctive part of the name, not the whole string.
        key = cert.lower().replace("certified ", "").replace("aws ", "aws ")
        tokens = [t for t in re.split(r"\W+", key) if len(t) > 3]
        if not tokens:
            continue

        for sentence in _sentences(letter):
            s_low = sentence.lower()
            hits = sum(1 for t in tokens if t in s_low)
            if hits < max(1, len(tokens) - 1):
                continue
            asserts_held = any(re.search(p, s_low) for p in _HELD_PATTERNS)
            says_in_prep = any(re.search(p, s_low) for p in _IN_PREP_PATTERNS)
            if asserts_held and not says_in_prep:
                violations.append(
                    f"claims {cert!r} as held: {sentence[:90]!r}"
                )

    return violations


def assert_letter_ok(letter: str, profile: dict) -> None:
    violations = check_letter(letter, profile)
    if violations:
        raise GuardViolation("; ".join(violations))
