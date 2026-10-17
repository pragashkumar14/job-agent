"""Adzuna job search API client.

Fallback source for when France Travail access is gated. Sign up at
developer.adzuna.com — instant, self-serve, you get an app_id and app_key
immediately with no approval step.

Free tier: 25 calls/min, 250/day, 1,000/week, 2,500/month. A daily run uses
roughly 5-8 calls, so the daily cap is not a constraint, but do not loop.

Note: Adzuna aggregates from job boards, so coverage differs from France
Travail (which carries employer-deposited offers). Neither is a superset of
the other. Running both is better than either alone.
"""

from __future__ import annotations

import logging
import os

import requests

from .france_travail import Job

log = logging.getLogger(__name__)

BASE = "https://api.adzuna.com/v1/api/jobs"
COUNTRY = "fr"

# Adzuna has no department filter; it takes a free-text location.
# These cover the same ground as the France Travail department list.
DEFAULT_AREAS = ["Île-de-France", "Oise"]


class AdzunaClient:
    def __init__(self, app_id: str | None = None, app_key: str | None = None):
        self.app_id = app_id or os.environ["ADZUNA_APP_ID"]
        self.app_key = app_key or os.environ["ADZUNA_APP_KEY"]
        self._session = requests.Session()

    def search(
        self,
        keywords: str,
        since_days: int = 2,
        max_results: int = 40,
        areas: list[str] | None = None,
        min_salary_eur: int | None = None,
        **_ignored,
    ) -> list[Job]:
        """Signature matches FranceTravailClient.search so the two are
        interchangeable from the agent's point of view."""
        keywords = self._clean_keywords(keywords)
        out: list[Job] = []
        seen: set[str] = set()

        for area in areas or DEFAULT_AREAS:
            params = {
                "app_id": self.app_id,
                "app_key": self.app_key,
                "what": keywords,
                "where": area,
                "results_per_page": min(max_results, 50),
                "max_days_old": max(since_days, 1),
                "content-type": "application/json",
                "sort_by": "date",
            }
            # Unlike France Travail, Adzuna's salary filter keeps postings
            # with no stated salary, so it is safe to pass.
            if min_salary_eur:
                params["salary_min"] = min_salary_eur

            try:
                resp = self._session.get(
                    f"{BASE}/{COUNTRY}/search/1", params=params, timeout=30
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                log.warning("Adzuna search failed for %r in %s: %s", keywords, area, exc)
                continue

            for raw in resp.json().get("results", []):
                job = self._normalise(raw)
                if job.id not in seen:
                    seen.add(job.id)
                    out.append(job)

        return out

    # Adzuna's `what` is AND across every word, so a long phrase matches
    # nothing. Location also belongs in `where`, not in the keywords.
    _LOCATION_NOISE = {
        "paris", "ile-de-france", "île-de-france", "idf", "france",
        "ile", "île", "de", "la", "le", "les", "du", "des", "en", "et",
        "oise", "region", "région", "junior", "senior", "h/f", "hf",
    }
    _MAX_TERMS = 2

    @classmethod
    def _clean_keywords(cls, keywords: str) -> str:
        """Trim a query down to something Adzuna can actually match.

        Drops location and filler words, then keeps at most two terms —
        longest first, since those carry the most signal. Two AND-ed words is
        the sweet spot: specific enough to be relevant, broad enough to
        actually return rows.
        """
        words = [w for w in keywords.replace("/", " ").split() if w]
        kept = [w for w in words if w.lower().strip(",()") not in cls._LOCATION_NOISE]
        if not kept:
            kept = words[:1]

        if len(kept) > cls._MAX_TERMS:
            ranked = sorted(kept, key=len, reverse=True)[: cls._MAX_TERMS]
            kept = [w for w in kept if w in ranked]  # keep original order

        cleaned = " ".join(kept)
        if cleaned.lower() != keywords.lower():
            log.debug("keywords %r -> %r", keywords, cleaned)
        return cleaned

    @staticmethod
    def _normalise(o: dict) -> Job:
        loc = o.get("location") or {}
        company = o.get("company") or {}
        salary = ""
        lo, hi = o.get("salary_min"), o.get("salary_max")
        if lo or hi:
            predicted = " (estimated)" if o.get("salary_is_predicted") in (1, "1", True) else ""
            salary = f"{int(lo or 0)} - {int(hi or 0)} EUR/an{predicted}"

        return Job(
            id=f"az-{o.get('id')}",
            source="Adzuna",
            title=o.get("title", "").replace("<strong>", "").replace("</strong>", ""),
            company=company.get("display_name", ""),
            location=loc.get("display_name", ""),
            contract=o.get("contract_time") or o.get("contract_type") or "",
            description=o.get("description", ""),
            url=o.get("redirect_url", ""),
            posted_at=o.get("created", ""),
            salary=salary,
            raw=o,
        )
