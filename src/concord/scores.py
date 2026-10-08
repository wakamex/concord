"""Per-function scores of a whole Harvest build, saved and compared.

A batch of source changes is ready to submit when no function anywhere gets
worse: none that was exact stops being exact, and no inexact one scores lower.
`snapshot` scores every function the last `hv match` compiled, and `compare`
lists what moved between two snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass

from concord.harvest import Harvest


def snapshot(harvest: Harvest) -> dict[str, dict]:
    """{unit/symbol: {"score": float, "exact": bool}} for every function in the build."""
    scores = {}
    for verdict in harvest.verdicts():
        key = f"{harvest.source(verdict.unit)}/{verdict.symbol}"
        if verdict.row["exact"]:
            scores[key] = {"score": 100.0, "exact": True}
            continue
        try:
            scores[key] = {"score": harvest.diff(verdict).score, "exact": False}
        except (KeyError, FileNotFoundError):
            scores[key] = {"score": None, "exact": False}
    return scores


@dataclass
class Change:
    function: str
    before: dict | None
    after: dict | None

    @property
    def worse(self) -> bool:
        if self.before is None:
            return False
        if self.after is None:
            return True
        if self.before["exact"] and not self.after["exact"]:
            return True
        before, after = self.before["score"], self.after["score"]
        return before is not None and (after is None or after < before)


def compare(before: dict[str, dict], after: dict[str, dict]) -> list[Change]:
    """Every function whose score or exactness differs, or that appears or disappears."""
    return [
        Change(key, before.get(key), after.get(key))
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key)
    ]
