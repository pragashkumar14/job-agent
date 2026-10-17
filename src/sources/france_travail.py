"""France Travail 'Offres d'emploi' API v2 client.

Free to use: register an application at https://francetravail.io, subscribe it
to the "Offres d'emploi v2" API, and you get a client id/secret for the OAuth2
client-credentials flow. No contract, no partner review.

Attribution note: if you ever redistribute these offers publicly, the licence
requires naming France Travail as the source with the last-update date and a
link back. For personal use (this agent emails you a shortlist) that does not
apply, but keep it in mind if you turn this into a public portfolio demo.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"

# INSEE department codes the agent searches.
DEPARTMENTS = {
    "75": "Paris",
    "77": "Seine-et-Marne",
    "78": "Yvelines",
    "91": "Essonne",
    "92": "Hauts-de-Seine",
    "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne",
    "95": "Val-d'Oise",
    "60": "Oise",
}


@dataclass
class Job:
    """One vacancy, normalised across sources."""

    id: str
    source: str
    title: str
    company: str
    location: str
    contract: str
    description: str
    url: str
    posted_at: str
    salary: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    def to_prompt_block(self, max_chars: int = 1800) -> str:
        """Compact rendering for the model — full JSON wastes tokens badly."""
        body = self.description[:max_chars]
        if len(self.description) > max_chars:
            body += " […truncated]"
        return (
            f"[{self.id}] {self.title}\n"
            f"Company: {self.company or 'not stated'}\n"
            f"Location: {self.location}\n"
            f"Contract: {self.contract}\n"
            f"Salary: {self.salary or 'not stated'}\n"
            f"Posted: {self.posted_at}\n"
            f"Description: {body}"
        )


class FranceTravailClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or os.environ["FT_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["FT_CLIENT_SECRET"]
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._session = requests.Session()

    # -- auth ---------------------------------------------------------------

    def _access_token(self) -> str:
        # Refresh 60s early so a long run never dies mid-request.
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        resp = self._session.post(
            TOKEN_URL,
            params={"realm": "/partenaire"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": SCOPE,
            },
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + payload.get("expires_in", 1500)
        log.debug("France Travail token refreshed")
        return self._token

    # -- search -------------------------------------------------------------

    def search(
        self,
        keywords: str,
        departments: list[str] | None = None,
        since_days: int = 2,
        max_results: int = 40,
        min_salary_eur: int | None = None,
    ) -> list[Job]:
        """Search live vacancies.

        The API pages via a Range-style `range` param (0-149 max per call) and
        caps a single query's reachable window, so narrow queries beat broad
        ones. We run several keyword queries rather than one giant one.
        """
        departments = departments or list(DEPARTMENTS)
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        params = {
            "motsCles": keywords,
            "departement": ",".join(departments),
            "minCreationDate": since,
            "maxCreationDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "range": f"0-{min(max_results, 149) - 1}",
            "sort": "1",  # most recent first
        }
        # NOTE: deliberately NOT passing salaireMin by default.
        # A large share of French postings state no salary at all, and the
        # server-side filter drops those rather than keeping them as unknown.
        # Filtering here would silently hide most of the market. Salary is
        # judged by the model from the description instead. Pass
        # min_salary_eur explicitly only if you want the strict behaviour.
        if min_salary_eur:
            params["salaireMin"] = min_salary_eur
            params["periodeSalaire"] = "A"  # annual

        resp = self._session.get(
            SEARCH_URL,
            params=params,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            timeout=30,
        )

        # 204 = no results for this query. Perfectly normal, not an error.
        if resp.status_code == 204:
            return []
        resp.raise_for_status()

        return [self._normalise(o) for o in resp.json().get("resultats", [])]

    @staticmethod
    def _normalise(o: dict) -> Job:
        lieu = o.get("lieuTravail") or {}
        entreprise = o.get("entreprise") or {}
        salaire = o.get("salaire") or {}
        return Job(
            id=f"ft-{o['id']}",
            source="France Travail",
            title=o.get("intitule", ""),
            company=entreprise.get("nom", ""),
            location=lieu.get("libelle", ""),
            contract=o.get("typeContratLibelle") or o.get("typeContrat", ""),
            description=o.get("description", ""),
            url=o.get("origineOffre", {}).get("urlOrigine", ""),
            posted_at=o.get("dateCreation", ""),
            salary=salaire.get("libelle", ""),
            raw=o,
        )
