"""DynamoDB-backed job store.

Same interface as the SQLite JobStore, so the agent is unchanged — this is
the seam that made the move to Lambda cheap. Lambda has no persistent disk,
so SQLite cannot survive between invocations; DynamoDB can.

Table design (single table, no secondary indexes):
    pk  = "JOB#<job_id>"     partition key
    sk  = "META"             sort key
Plus a GSI on status for the shortlist query, declared in Terraform.

At this volume (tens of items a day) everything sits inside the perpetual
free allowance: 25 GB storage and 25 read/write capacity units.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

log = logging.getLogger(__name__)

TABLE = os.environ.get("JOBS_TABLE", "job-agent-jobs")


def _clean(value):
    """DynamoDB rejects floats and empty strings; normalise both."""
    if isinstance(value, float):
        return Decimal(str(value))
    if value == "":
        return None
    return value


class DynamoJobStore:
    def __init__(self, table_name: str | None = None):
        self.table_name = table_name or TABLE
        self._table = boto3.resource("dynamodb").Table(self.table_name)

    # -- same surface as JobStore -------------------------------------------

    def filter_new(self, job_ids: list[str]) -> set[str]:
        """Return the ids never seen before.

        batch_get_item takes up to 100 keys per call, so chunk.
        """
        if not job_ids:
            return set()

        seen: set[str] = set()
        client = boto3.resource("dynamodb").meta.client
        for i in range(0, len(job_ids), 100):
            chunk = job_ids[i : i + 100]
            resp = client.batch_get_item(
                RequestItems={
                    self.table_name: {
                        "Keys": [{"pk": f"JOB#{j}", "sk": "META"} for j in chunk],
                        "ProjectionExpression": "pk",
                    }
                }
            )
            for item in resp.get("Responses", {}).get(self.table_name, []):
                seen.add(item["pk"].removeprefix("JOB#"))

        return set(job_ids) - seen

    def record(
        self,
        job,
        score: int,
        verdict: str,
        reasoning: str,
        cover_letter: str | None = None,
    ) -> None:
        status = "shortlisted" if verdict == "apply" else "rejected"
        item = {
            "pk": f"JOB#{job.id}",
            "sk": "META",
            "job_id": job.id,
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "source": job.source,
            "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "score": int(score),
            "verdict": verdict,
            "reasoning": reasoning,
            "cover_letter": cover_letter,
            "status": status,
        }
        self._table.put_item(Item={k: _clean(v) for k, v in item.items() if _clean(v) is not None})

    def shortlist(self, limit: int = 20) -> list[dict]:
        resp = self._table.query(
            IndexName="status-score-index",
            KeyConditionExpression=Key("status").eq("shortlisted"),
            ScanIndexForward=False,  # highest score first
            Limit=limit,
        )
        items = resp.get("Items", [])
        # Decimal -> int for the digest template.
        for it in items:
            if isinstance(it.get("score"), Decimal):
                it["score"] = int(it["score"])
        return items

    def set_status(self, job_id: str, status: str) -> None:
        self._table.update_item(
            Key={"pk": f"JOB#{job_id}", "sk": "META"},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status},
        )

    def stats(self) -> dict:
        """Full scan. Fine at this size; revisit if the table grows past a
        few thousand items."""
        counts: dict[str, int] = {}
        kwargs = {"ProjectionExpression": "#s", "ExpressionAttributeNames": {"#s": "status"}}
        while True:
            resp = self._table.scan(**kwargs)
            for item in resp.get("Items", []):
                s = item.get("status", "unknown")
                counts[s] = counts.get(s, 0) + 1
            if "LastEvaluatedKey" not in resp:
                return counts
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
