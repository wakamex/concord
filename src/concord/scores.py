"""The progress a Harvest build reports upstream, saved and compared.

decomp.dev scores a pull request from Harvest's progress report
(`python -m hv.progress report`): objdiff's fuzzy score and match state for every
function, and the target bytes each matched data section covers. A batch of
changes is ready to submit when, by that report, no function scores lower or
stops matching and no data byte that matched stops matching. Data is compared by
bytes because the report names a matched data run by its start address: two runs
that merge into one show up as a run that disappeared and a new, larger one.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from concord.harvest import Harvest


def report(harvest: Harvest, capture: bool = False, ref: str | None = None) -> dict:
    """Harvest's progress report for the checkout, after recapturing its evidence
    when `capture` is set, or for the evidence committed at git revision `ref`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = harvest.root
        if ref is not None:
            root = Path(tmp) / "worktree"
            subprocess.run(["git", "-C", str(harvest.root), "worktree", "add", "-q", "--detach", str(root), ref], check=True)
        try:
            output = Path(tmp) / "report.json"
            command = "capture" if capture and ref is None else "report"
            subprocess.run(
                ["uv", "--no-config", "run", "--locked", "python", "-m", "hv.progress", command, "--output", str(output)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(output.read_text())
        finally:
            if ref is not None:
                subprocess.run(["git", "-C", str(harvest.root), "worktree", "remove", "--force", str(root)], check=True)


def snapshot(progress: dict) -> dict:
    """{"functions": {address: {"name", "fuzzy", "matched"}}, "data": [[start, size], ...]}
    from a progress report: every function, and the matched data runs."""
    functions, data = {}, []
    for item in progress["units"]:
        measures = item["measures"]
        for function in item.get("functions", []):
            address = function["metadata"]["virtual_address"]
            functions[address] = {
                "name": function["metadata"].get("demangled_name", function["name"]),
                "fuzzy": function.get("fuzzy_match_percent", 0.0),
                "matched": measures["matched_code"] == measures["total_code"],
            }
        if int(measures["total_data"]) and measures["matched_data"] == measures["total_data"]:
            data += [[int(s["metadata"]["virtual_address"]), int(s["size"])] for s in item.get("sections", [])]
    return {"functions": functions, "data": sorted(data)}


@dataclass
class Change:
    address: str
    name: str
    before: dict | None
    after: dict | None

    @property
    def worse(self) -> bool:
        if self.before is None:
            return False
        if self.after is None:
            return True
        return (self.before["matched"] and not self.after["matched"]) or self.after["fuzzy"] < self.before["fuzzy"]


def compare(before: dict, after: dict) -> tuple[list[Change], int, int]:
    """Changed functions, and the matched data bytes lost and gained."""
    changes = []
    for address in sorted(before["functions"].keys() | after["functions"].keys(), key=int):
        a, b = before["functions"].get(address), after["functions"].get(address)
        if a != b:
            changes.append(Change(address, (b or a)["name"], a, b))
    old, new = _bytes(before["data"]), _bytes(after["data"])
    return changes, len(old - new), len(new - old)


def _bytes(runs: list) -> set[int]:
    return {address for start, size in runs for address in range(start, start + size)}
