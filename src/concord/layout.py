"""Find misplaced fields by comparing where the original and our compile access memory.

harvest-oracle's `accesses` command follows each pointer from its source (`this`,
argument N, a global, or a pointer loaded from one of those, such as this.0xe0)
and lists every load and store with its offset and size, for the original
executable's code and for a compiled object. Instruction order differs between
the two compiles, so the lists are compared per base as sets of (offset, size,
read or write). An offset only one side uses, under a base both sides use,
points at a field the declarations place differently from the original.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass

from concord.harvest import Harvest


@dataclass
class Mismatch:
    base: str
    original: list[tuple[int, int, str]]  # (offset, size, access) only the original uses
    ours: list[tuple[int, int, str]]  # (offset, size, access) only our compile uses


def accesses(harvest: Harvest, symbol: str, unit: str | None = None) -> list[dict]:
    """The function's memory accesses in the original executable, or in the unit's
    object from the last `hv match` when `unit` is given."""
    command = ["harvest-oracle", "accesses", symbol]
    if unit is not None:
        command += ["--object", str(harvest.reports / f"{harvest.slug(unit)}.o")]
    done = subprocess.run(command, cwd=harvest.root, capture_output=True, text=True, check=True)
    [function] = json.loads(done.stdout).values()
    return function["accesses"]


def _by_base(rows: list[dict]) -> dict[str, set[tuple[int, int, str]]]:
    grouped = defaultdict(set)
    for row in rows:
        if row["offset"] is not None:  # an indexed access has no fixed offset
            grouped[row["base"]].add((int(row["offset"], 16), row["size"], row["access"]))
    return grouped


def compare(original: list[dict], ours: list[dict]) -> list[Mismatch]:
    """Bases both sides access whose offsets differ."""
    a, b = _by_base(original), _by_base(ours)
    return [
        Mismatch(base, sorted(a[base] - b[base]), sorted(b[base] - a[base]))
        for base in sorted(a.keys() & b.keys())
        if a[base] != b[base]
    ]

