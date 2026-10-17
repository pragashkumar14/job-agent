"""Safe .env inspector — prints structure, never values.

Run from the project folder:  python -m src.check_env
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = [
    "ANTHROPIC_API_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "FT_CLIENT_ID",
    "FT_CLIENT_SECRET",
]
PLACEHOLDERS = {"sk-ant-...", "", "...", "xxx", "your-key-here"}


def main() -> int:
    print(f"Looking in: {ROOT}\n")

    # Catch the classic Notepad trap.
    strays = [p.name for p in ROOT.glob(".env*") if p.name not in (".env", ".env.example")]
    if strays:
        print(f"!! Found {strays} — the agent only reads '.env'. Rename it.\n")

    env = ROOT / ".env"
    if not env.exists():
        print("!! No .env file. Run:  copy .env.example .env")
        return 1

    lines = env.read_text(encoding="utf-8-sig").splitlines()
    seen: dict[str, int] = {}
    print(f"{'line':>4}  {'key':<20} {'chars':>5}  status")
    print("-" * 52)

    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")

        if not value:
            status = "EMPTY"
        elif value in PLACEHOLDERS or value.endswith("..."):
            status = "PLACEHOLDER — not replaced"
        elif key in seen:
            status = f"DUPLICATE of line {seen[key]}"
        else:
            status = "ok"

        if key not in seen:
            seen[key] = i
        print(f"{i:>4}  {key:<20} {len(value):>5}  {status}")

    print()
    for key in EXPECTED:
        if key not in seen:
            print(f"   missing line: {key}")

    print("\nExpected sizes: ANTHROPIC_API_KEY ~100 chars, Adzuna ID ~8, key ~32.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
