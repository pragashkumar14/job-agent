"""Seen-job store.

SQLite locally; the same interface swaps to DynamoDB in phase 2 without the
agent code changing. That seam is deliberate — it is the bit worth talking
about in an interview.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    company       TEXT,
    url           TEXT,
    first_seen    TEXT NOT NULL,
    score         INTEGER,
    verdict       TEXT,
    reasoning     TEXT,
    cover_letter  TEXT,
    status        TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_status ON seen_jobs(status);
CREATE INDEX IF NOT EXISTS idx_first_seen ON seen_jobs(first_seen);
"""

# status values: new | shortlisted | rejected | applied | interviewing | closed


class JobStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def filter_new(self, job_ids: list[str]) -> set[str]:
        """Return the subset we have never scored. Saves the expensive step."""
        if not job_ids:
            return set()
        with closing(self._connect()) as conn:
            placeholders = ",".join("?" * len(job_ids))
            rows = conn.execute(
                f"SELECT id FROM seen_jobs WHERE id IN ({placeholders})", job_ids
            ).fetchall()
        return set(job_ids) - {r["id"] for r in rows}

    def record(
        self,
        job,
        score: int,
        verdict: str,
        reasoning: str,
        cover_letter: str | None = None,
    ) -> None:
        status = "shortlisted" if verdict == "apply" else "rejected"
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO seen_jobs
                   (id, title, company, url, first_seen, score, verdict,
                    reasoning, cover_letter, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.id,
                    job.title,
                    job.company,
                    job.url,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    score,
                    verdict,
                    reasoning,
                    cover_letter,
                    status,
                ),
            )
            conn.commit()

    def shortlist(self, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT * FROM seen_jobs
                   WHERE status = 'shortlisted'
                   ORDER BY score DESC, first_seen DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, job_id: str, status: str) -> None:
        """Call this as you actually apply — it keeps the pipeline honest."""
        with closing(self._connect()) as conn:
            conn.execute("UPDATE seen_jobs SET status = ? WHERE id = ?", (status, job_id))
            conn.commit()

    def stats(self) -> dict:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM seen_jobs GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def export_json(self, path: Path | str) -> None:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM seen_jobs").fetchall()
        Path(path).write_text(
            json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
