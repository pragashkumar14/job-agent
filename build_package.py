"""Build the Lambda deployment package.

Run from the project root:  python build_package.py

The subtlety: you are on Windows but Lambda runs Linux. Dependencies with
compiled parts (pydantic-core, jiter) must be the Linux wheels, so pip is
told explicitly which platform to fetch for rather than building for the
machine it is running on. Getting this wrong produces a zip that imports
fine locally and fails in Lambda with an obscure .so error.

boto3 is already in the Lambda runtime, so it is deliberately not bundled.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
STAGE = BUILD / "package"
ZIP = BUILD / "lambda.zip"

# Lambda's Python 3.13 runtime, x86_64.
PLATFORM = "manylinux2014_x86_64"
PY_VERSION = "3.13"

DEPS = ["anthropic", "requests", "PyYAML"]

# Everything the handler imports at runtime.
INCLUDE_FILES = ["lambda_handler.py", "profile.yaml"]
INCLUDE_DIRS = ["src"]


def run(cmd: list[str]) -> None:
    print("  $", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main() -> int:
    print("Building Lambda package\n")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    STAGE.mkdir(parents=True)

    print("Installing dependencies for Linux x86_64:")
    run([
        sys.executable, "-m", "pip", "install",
        "--target", str(STAGE),
        "--platform", PLATFORM,
        "--python-version", PY_VERSION,
        "--implementation", "cp",
        "--only-binary=:all:",
        "--upgrade",
        "--quiet",
        *DEPS,
    ])

    print("\nCopying application files:")
    for name in INCLUDE_FILES:
        shutil.copy2(ROOT / name, STAGE / name)
        print("  ", name)
    for name in INCLUDE_DIRS:
        shutil.copytree(
            ROOT / name,
            STAGE / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db"),
        )
        print("  ", name + "/")

    # Trim what Lambda does not need. Keeps the upload small and the cold
    # start quick.
    #
    # NOTE: *.dist-info is deliberately NOT trimmed. Several packages read
    # their own version at import time via importlib.metadata, which reads
    # that directory — httpx2 (an anthropic dependency) is one, and stripping
    # it produces a PackageNotFoundError at runtime that never shows up
    # locally.
    removed = 0
    for pattern in ("__pycache__", "*.pyc", "tests"):
        for path in STAGE.rglob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed += 1
    print(f"\nTrimmed {removed} unneeded paths")

    print("Zipping...")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in STAGE.rglob("*"):
            if path.is_file():
                z.write(path, path.relative_to(STAGE))

    size_mb = ZIP.stat().st_size / 1_000_000
    print(f"\n{ZIP}  ({size_mb:.1f} MB)")

    if size_mb > 50:
        print("\n!! Over 50 MB — too large for a direct upload.")
        print("   It will need to go via S3, or move dependencies to a layer.")
        return 1

    print("\nReady. Next: cd infra && terraform apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
