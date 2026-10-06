"""Read a Harvest checkout's matching outputs.

`hv match` writes, per unit, a report (build/match/<build>/<unit>.json) with every
function's verdict and every unresolved reference, and two objects for objdiff
(build/objdiff/<build>/{target,base}/<unit>.o): the target's bytes cut out of the
executable and laid out like the compiled object, and the compiled object itself.
concord reads those files; it does not import Harvest's code.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from concord.diff import diff_code, read_function
from concord.model import Cause, DiffFinding, DiffResult

BUILD = "1.18-linux-amd64"


@dataclass(frozen=True)
class FunctionVerdict:
    unit: str  # report slug, such as ox__net__CVariablePacket
    symbol: str
    row: dict  # the function's row in the match report
    section: dict  # the section the function belongs to


class Harvest:
    def __init__(self, root: Path, build: str = BUILD) -> None:
        self.root = root
        self.build = build
        self.reports = root / "build" / "match" / build
        self.objects = root / "build" / "objdiff" / build
        if not self.reports.is_dir():
            raise FileNotFoundError(f"{self.reports} is missing; run `hv match` in {root}")

    def slug(self, unit: str) -> str:
        """Accept a report slug or a source path such as ox/net/CVariablePacket.cpp."""
        return unit.removesuffix(".cpp").replace("/", "__")

    def verdicts(self, unit: str | None = None) -> Iterator[FunctionVerdict]:
        paths = [self.reports / f"{self.slug(unit)}.json"] if unit else sorted(self.reports.glob("*.json"))
        for path in paths:
            report = json.loads(path.read_text())
            for section in report["sections"]:
                for row in section.get("functions", []):
                    yield FunctionVerdict(path.stem, row["symbol"], row, section)

    def verdict(self, unit: str, symbol: str) -> FunctionVerdict:
        for v in self.verdicts(unit):
            if v.symbol == symbol:
                return v
        raise KeyError(f"{symbol} is not in {unit}'s report")

    def diff(self, verdict: FunctionVerdict) -> DiffResult:
        if verdict.row["exact"]:
            return DiffResult(score=100.0, exact=True)
        target = read_function(self.objects / "target" / f"{verdict.unit}.o", verdict.symbol)
        candidate = read_function(self.objects / "base" / f"{verdict.unit}.o", verdict.symbol)
        if target.code != candidate.code:
            return diff_code(target, candidate)
        return DiffResult(score=100.0, exact=False, findings=self._unproven(verdict, candidate.start))

    def _unproven(self, verdict: FunctionVerdict, start: int) -> list[DiffFinding]:
        """Findings for a function whose bytes match but which the matcher does not
        prove exact: wrong reference destinations, unknown symbols, or placement."""
        row, end = verdict.row, start + verdict.row["size"]
        findings = [
            DiffFinding(
                Cause.REFERENCE,
                r["offset"] - start,
                f"{r['symbol']}: {r['reason']}, candidate resolves to {r.get('destination', '?')}",
            )
            for r in verdict.section.get("bad_references", [])
            if start <= r["offset"] < end
        ]
        if row.get("exact_but_unknown"):
            names = ", ".join(sorted({name for name, _ in row.get("candidates", [])}))
            findings.append(DiffFinding(Cause.REFERENCE, 0, f"references to symbols with unknown addresses: {names}"))
        if not row["fde"]:
            detail = f"the target has no unwind entry of {row['size']} bytes at {row['address']}"
            if verdict.symbol.startswith(("_ZTh", "_ZTv")):
                detail += "; GCC 4.4 emits no unwind entry for thunks"
            findings.append(DiffFinding(Cause.PLACEMENT, 0, detail))
        if not findings:
            findings.append(DiffFinding(Cause.UNKNOWN, 0, "bytes identical; the matcher reports the function inexact"))
        return findings
