"""Source selection.

The agent does not care which job board it is talking to — it gets Job
objects either way. This picks whichever sources are configured, so you can
run France Travail, Adzuna, or both, by changing .env alone.
"""

from __future__ import annotations

import logging
import os

from .adzuna import AdzunaClient
from .france_travail import DEPARTMENTS, FranceTravailClient, Job

log = logging.getLogger(__name__)

__all__ = ["Job", "DEPARTMENTS", "FranceTravailClient", "AdzunaClient", "MultiSource"]


class MultiSource:
    """Fans one query out across every configured source."""

    def __init__(self, clients: list | None = None):
        self.clients = clients if clients is not None else self._autodetect()
        if not self.clients:
            raise RuntimeError(
                "No job source configured. Set ADZUNA_APP_ID/ADZUNA_APP_KEY "
                "or FT_CLIENT_ID/FT_CLIENT_SECRET in your .env"
            )
        log.info("Sources: %s", ", ".join(type(c).__name__ for c in self.clients))

    @staticmethod
    def _autodetect() -> list:
        clients = []
        if os.environ.get("FT_CLIENT_ID") and os.environ.get("FT_CLIENT_SECRET"):
            clients.append(FranceTravailClient())
        if os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
            clients.append(AdzunaClient())
        return clients

    def search(self, **kwargs) -> list[Job]:
        results: list[Job] = []
        for client in self.clients:
            try:
                results.extend(client.search(**kwargs))
            except Exception as exc:  # noqa: BLE001
                # One source being down must not kill the run.
                log.warning("%s failed: %s", type(client).__name__, exc)
        return results
