"""Adzuna parameter bisect.

Zero results across every query usually means one filter is wrong, not that
the market is empty. This runs the same query with filters added one at a
time and prints the count at each step, so the culprit is obvious.

Run:  python -m src.check_source
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

from .config import load_env

ROOT = Path(__file__).resolve().parent.parent
load_env(ROOT / ".env")

BASE = "https://api.adzuna.com/v1/api/jobs/fr/search/1"


def probe(label: str, **extra) -> int:
    params = {
        "app_id": os.environ.get("ADZUNA_APP_ID", ""),
        "app_key": os.environ.get("ADZUNA_APP_KEY", ""),
        "results_per_page": 10,
        "content-type": "application/json",
        **extra,
    }
    try:
        r = requests.get(BASE, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:<46} network error: {exc}")
        return -1

    if r.status_code != 200:
        body = r.text[:120].replace("\n", " ")
        print(f"  {label:<46} HTTP {r.status_code}  {body}")
        return -1

    data = r.json()
    count = len(data.get("results", []))
    total = data.get("count", "?")
    print(f"  {label:<46} {count:>3} returned (total matching: {total})")
    return count


def main() -> int:
    if not os.environ.get("ADZUNA_APP_ID"):
        print("ADZUNA_APP_ID not set — check .env")
        return 1

    print("\nAdzuna probe — each line adds one more filter\n")

    print("Baseline:")
    probe("no filters at all")
    probe("what=linux", what="linux")

    print("\nLocation forms:")
    probe("where=Paris", what="linux", where="Paris")
    probe("where=Ile-de-France (no accents)", what="linux", where="Ile-de-France")
    probe("where=Île-de-France (accents)", what="linux", where="Île-de-France")
    probe("where=Paris + distance=50km", what="linux", where="Paris", distance=50)

    print("\nRecency:")
    for days in (2, 7, 30):
        probe(f"max_days_old={days}", what="linux", where="Paris", max_days_old=days)

    print("\nSorting:")
    probe("sort_by=date", what="linux", where="Paris", sort_by="date")
    probe("sort_by=relevance", what="linux", where="Paris", sort_by="relevance")

    print("\nThe first line that drops to 0 is the problem.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
