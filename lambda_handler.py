"""AWS Lambda entry point.

Runs the same JobAgent as the CLI, but swaps two things:
  - the store: DynamoDB instead of SQLite (Lambda has no persistent disk)
  - the output: an SES email instead of a local HTML file

Secrets come from SSM Parameter Store, read once per cold start and cached
in os.environ so the rest of the code is unchanged.
"""

from __future__ import annotations

import json
import logging
import os

import boto3

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("job-agent")

_PARAMS_LOADED = False


def _load_params() -> None:
    """Fetch secrets from Parameter Store into the environment.

    Module-level cache: Lambda reuses a warm container, so this runs on
    cold start only and keeps the SSM call count near zero.
    """
    global _PARAMS_LOADED
    if _PARAMS_LOADED:
        return

    prefix = os.environ.get("PARAM_PREFIX", "/job-agent")
    ssm = boto3.client("ssm")
    paginator = ssm.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=prefix, WithDecryption=True, Recursive=True):
        for param in page["Parameters"]:
            key = param["Name"].rsplit("/", 1)[-1]
            os.environ[key] = param["Value"]

    _PARAMS_LOADED = True
    log.info("Loaded parameters from %s", prefix)


def _send_email(html: str, subject: str) -> None:
    sender = os.environ["SES_SENDER"]
    recipient = os.environ.get("SES_RECIPIENT", sender)

    boto3.client("ses").send_email(
        Source=sender,
        Destination={"ToAddresses": [recipient]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": html, "Charset": "UTF-8"}},
        },
    )
    log.info("Digest emailed to %s", recipient)


def handler(event, context):  # noqa: ARG001 - Lambda signature
    _load_params()

    # Imported after params are loaded: agent.py reads os.environ at import.
    from src.agent import JobAgent
    from src.digest import render_digest
    from src.store_dynamo import DynamoJobStore

    agent = JobAgent.__new__(JobAgent)

    from anthropic import Anthropic

    from src.sources import MultiSource

    import yaml
    from pathlib import Path

    agent.client = Anthropic()
    agent.ft = MultiSource()
    agent.store = DynamoJobStore()
    # In Lambda the handler and profile.yaml both sit in /var/task, so this
    # is .parent — not .parent.parent. Same relationship locally, where both
    # live in the project root.
    agent.profile = yaml.safe_load(
        (Path(__file__).resolve().parent / "profile.yaml").read_text(encoding="utf-8")
    )
    agent.dry_run = False
    agent.jobs, agent.judged = {}, {}
    agent.done, agent.summary, agent.rejected_letters = False, "", 0

    result = agent.run()
    shortlist = result["shortlist"]

    log.info(
        "Scanned %d, judged %d, shortlist %d",
        result["seen_this_run"],
        result["judged"],
        len(shortlist),
    )

    if shortlist:
        tmp = "/tmp/digest.html"  # noqa: S108 - the only writable path in Lambda
        render_digest(shortlist, tmp)
        with open(tmp, encoding="utf-8") as fh:
            html = fh.read()
        n = len(shortlist)
        _send_email(html, f"Job shortlist — {n} role{'' if n == 1 else 's'}")
    else:
        log.info("Nothing shortlisted; no email sent.")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "scanned": result["seen_this_run"],
                "judged": result["judged"],
                "shortlisted": len(shortlist),
                "summary": result["summary"],
            }
        ),
    }
