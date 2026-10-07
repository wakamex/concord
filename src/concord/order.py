"""Definition-order search, through Harvest's `hv search`.

GCC 4.4 processes a unit's functions in an order that depends on where they are
defined, and that order changes inlining and register allocation, also in
header code whose own source is fixed. `hv search` searches top-level definition
orders of one unit and verifies an improved winner with a fresh canonical
compile; this module runs it, reads its evidence, and applies a verified winner
only when it is a pure reorder of the unchanged source.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from concord.harvest import Harvest


@dataclass
class OrderResult:
    unit: str  # source path under src/
    reason: str
    run: Path | None = None
    baseline: set[str] = field(default_factory=set)
    verified: set[str] | None = None  # exact functions of the verified winner

    @property
    def gained(self) -> set[str]:
        return (self.verified or set()) - self.baseline

    @property
    def lost(self) -> set[str]:
        return self.baseline - self.verified if self.verified is not None else set()


def _exact(path: Path) -> set[str]:
    data = json.loads(path.read_text())
    data = data.get("result", data)
    return {f["symbol"] for s in data["sections"] for f in s.get("functions", []) if f["exact"]}


def search_order(
    harvest: Harvest, unit: str, budget: int = 128, restarts: int = 2, seed: int = 0, batch: int = 32
) -> OrderResult:
    source = harvest.source(unit)
    command = [
        "uv", "--no-config", "run", "--locked", "hv", "search", source,
        "--budget", str(budget), "--restarts", str(restarts), "--seed", str(seed), "--batch", str(batch),
    ]  # fmt: skip
    done = subprocess.run(command, cwd=harvest.root, capture_output=True, text=True)
    output = done.stdout + done.stderr
    if "at least two explicit blocks" in output:
        return OrderResult(source, "a single definition, nothing to reorder")
    match = re.search(r"^evidence\s+(\S+)", output, re.M)
    if done.returncode or not match:
        return OrderResult(source, f"hv search failed: {output.strip()[-300:]}")
    run = Path(match.group(1))
    summary = json.loads((run / "summary.json").read_text())
    result = OrderResult(source, summary["reason"], run, _exact(run / "baseline.json"))
    if (run / "verification.json").exists():
        result.verified = _exact(run / "verification.json")
    return result


def apply_order(harvest: Harvest, result: OrderResult) -> None:
    """Write a verified winner over the unit's source, refusing anything but a
    reorder of the exact source the search started from."""
    if not result.gained or result.lost:
        raise ValueError(f"{result.unit}: no verified gain to apply")
    path = harvest.root / "src" / result.unit
    current = path.read_bytes()
    context = json.loads((result.run / "context.json").read_text())
    if hashlib.sha256(current).hexdigest() != context["blocks"]["source_sha256"]:
        raise ValueError(f"{result.unit} changed since the search")
    candidate = (result.run / "candidate.cpp").read_bytes()
    if sorted(candidate.splitlines()) != sorted(current.splitlines()):
        raise ValueError(f"{result.unit}: the winner is not a pure reorder")
    path.write_bytes(candidate)
