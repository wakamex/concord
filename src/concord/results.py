"""The log of every search concord runs against a target project.

Each search appends one JSON line to results/<target>.jsonl: what was searched, at
which commit of the target, with which settings, and what it found. An improved
source is saved as a patch against that commit in results/patches/, so a later
search can start from the best known form and a contributor can apply it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
import time
from pathlib import Path

LOG = Path("results") / "harvest.jsonl"


def commit(root: Path) -> str:
    """The checkout's commit, marked dirty when its sources differ from it."""
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    changed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", "src", "config"],
        capture_output=True,
        text=True,
        check=True,
    )
    return head.stdout.strip() + ("-dirty" if changed.stdout.strip() else "")


def save_patch(log: Path, unit: str, original: bytes, candidate: bytes) -> str:
    """Write the candidate as a patch against the unit's source; return its path
    relative to the log's directory."""
    diff = "".join(
        difflib.unified_diff(
            original.decode().splitlines(keepends=True),
            candidate.decode().splitlines(keepends=True),
            f"a/src/{unit}",
            f"b/src/{unit}",
        )
    )
    name = f"{unit.removesuffix('.cpp').replace('/', '__')}-{hashlib.sha256(candidate).hexdigest()[:12]}.patch"
    path = log.parent / "patches" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(diff)
    return str(path.relative_to(log.parent))


def record(log: Path, row: dict) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {"date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row}
    with log.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")


def load(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
