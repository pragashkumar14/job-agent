"""Load .env into the process environment.

Deliberately dependency-free — no python-dotenv — so there is one less thing
to install and one less thing to break on a fresh machine. Handles the usual
real-world messiness: comments, blank lines, quotes, 'export ' prefixes,
Windows line endings, and stray whitespace around the '='.

Existing environment variables always win, so you can override any value for
a single run without editing the file:

    $env:AGENT_MODEL = "claude-opus-4-5"; python -m src.agent
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def load_env(path: Path | str) -> dict[str, str]:
    """Read a .env file and set anything not already in os.environ.

    Returns the values it parsed (not necessarily the ones it set).
    """
    path = Path(path)
    if not path.exists():
        log.warning("No .env found at %s — relying on the shell environment.", path)
        return {}

    parsed: dict[str, str] = {}
    # utf-8-sig strips the BOM that Windows Notepad can prepend, which would
    # otherwise corrupt the first key name.
    for lineno, raw in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            log.warning("%s line %d: no '=', skipped: %r", path.name, lineno, raw[:40])
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip matching surrounding quotes, if present.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        if not key:
            continue
        if key in parsed:
            log.warning(
                "%s line %d: %s appears more than once — using this later value.",
                path.name, lineno, key,
            )
        parsed[key] = value

    # Apply only after the whole file is read, so a duplicated key resolves to
    # its LAST value (standard .env behaviour). Applying as we went would have
    # locked in the first one — including a leftover placeholder.
    for key, value in parsed.items():
        if value and key not in os.environ:
            os.environ[key] = value

    configured = [k for k, v in parsed.items() if v]
    log.debug("Loaded %d value(s) from %s", len(configured), path.name)
    return parsed


def describe_config(parsed: dict[str, str]) -> str:
    """A safe one-line summary — never prints secret values."""
    def state(key: str) -> str:
        return "set" if os.environ.get(key) else "missing"

    return (
        f"ANTHROPIC_API_KEY={state('ANTHROPIC_API_KEY')}, "
        f"Adzuna={state('ADZUNA_APP_ID')}/{state('ADZUNA_APP_KEY')}, "
        f"FranceTravail={state('FT_CLIENT_ID')}/{state('FT_CLIENT_SECRET')}"
    )
