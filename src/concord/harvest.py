"""Read a Harvest checkout's matching outputs.

`hv match` writes, per unit, a report (build/match/<build>/<unit>.json) with every
function's verdict and every unresolved reference, and two objects for objdiff
(build/objdiff/<build>/{target,base}/<unit>.o): the target's bytes cut out of the
executable and laid out like the compiled object, and the compiled object itself.
concord reads those files; it does not import Harvest's code.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
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


DRIVER = Path(__file__).with_name("harvest_driver.py")


@dataclass
class Evaluation:
    """One candidate source compiled and compared by Harvest's matcher."""

    name: str
    unit: str  # report slug
    object: Path | None = None
    error: str = ""
    sections: list[dict] = field(default_factory=list)

    def verdict(self, symbol: str) -> FunctionVerdict | None:
        for section in self.sections:
            for row in section.get("functions", []):
                if row["symbol"] == symbol:
                    return FunctionVerdict(self.unit, symbol, row, section)
        return None

    def exact_functions(self) -> set[str]:
        return {row["symbol"] for s in self.sections for row in s.get("functions", []) if row["exact"]}


class Harvest:
    def __init__(self, root: Path, build: str = BUILD) -> None:
        self.root = root
        self.build = build
        self.reports = root / "build" / "match" / build
        self.objects = root / "build" / "objdiff" / build
        if not self.reports.is_dir():
            raise FileNotFoundError(f"{self.reports} is missing; run `hv match` in {root}")

    def _driver(self, request: str) -> subprocess.CompletedProcess:
        """Run harvest_driver.py with the checkout's Python; its stderr explains a failure."""
        result = subprocess.run(
            ["uv", "--no-config", "run", "--locked", "--project", str(self.root), "python", str(DRIVER)],
            cwd=self.root,
            input=request,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"harvest_driver.py failed:\n{result.stderr[-3000:]}")
        return result

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

    def source(self, unit: str) -> str:
        """The unit's source path under src/, from its report."""
        return json.loads((self.reports / f"{self.slug(unit)}.json").read_text())["unit"]

    def evaluate(self, unit: str, sources: dict[str, bytes]) -> dict[str, Evaluation]:
        """Compile each candidate source of the unit in Harvest's pinned toolchain,
        in one batch, and compare it with the target. Work files go under
        build/concord/, never into the checkout's own match outputs."""
        slug = self.slug(unit)
        out = self.root / "build" / "concord" / slug / uuid.uuid4().hex
        out.mkdir(parents=True)
        paths = {}
        for name, text in sources.items():
            paths[name] = out / f"{name}.cpp"
            paths[name].write_bytes(text)
        request = {"unit": self.source(unit), "out": str(out), "candidates": {n: str(p) for n, p in paths.items()}}
        result = self._driver(json.dumps(request))
        evaluations = {}
        for name, row in json.loads(result.stdout).items():
            evaluations[name] = Evaluation(
                name,
                slug,
                Path(row["object"]) if "object" in row else None,
                row.get("error", ""),
                row.get("sections", []),
            )
        return evaluations

    def evaluate_overlays(self, items: dict[str, tuple[str, dict[str, bytes]]]) -> dict[str, Evaluation]:
        """Compile each item, a unit source path with repository files replaced
        (path under the checkout -> new bytes), in one batch, and compare it with
        the target."""
        out = self.root / "build" / "concord" / "overlays" / uuid.uuid4().hex
        out.mkdir(parents=True)
        files: dict[bytes, Path] = {}
        request_items = {}
        for name, (unit, overlays) in items.items():
            mapped = {}
            for path, text in overlays.items():
                if text not in files:
                    files[text] = out / f"overlay{len(files)}{Path(path).suffix}"
                    files[text].write_bytes(text)
                mapped[path] = str(files[text])
            request_items[name] = {"unit": unit, "overlays": mapped}
        result = self._driver(json.dumps({"out": str(out), "items": request_items}))
        return {
            name: Evaluation(
                name,
                self.slug(items[name][0]),
                Path(row["object"]) if "object" in row else None,
                row.get("error", ""),
                row.get("sections", []),
            )
            for name, row in json.loads(result.stdout).items()
        }

    def debug_objects(self, units: list[str], sources: dict[str, bytes] | None = None) -> dict[str, Path]:
        """Compile each unit with -g added, for its line tables and variable types,
        from its checkout source or from `sources` (unit source path -> candidate
        text). Units that fail to compile are left out."""
        out = self.root / "build" / "concord" / "debug" / uuid.uuid4().hex
        out.mkdir(parents=True)
        names = {f"u{n}": unit for n, unit in enumerate(units)}
        overlays = {}
        for unit, text in (sources or {}).items():
            path = out / f"{self.slug(unit)}{Path(unit).suffix}"
            path.write_bytes(text)
            overlays[unit] = {f"src/{unit}": str(path)}
        items = {n: {"unit": u, "overlays": overlays.get(u, {})} for n, u in names.items()}
        request = {"out": str(out), "debug": True, "items": items}
        result = self._driver(json.dumps(request))
        return {names[n]: Path(row["object"]) for n, row in json.loads(result.stdout).items() if "object" in row}

    def vtables(self) -> tuple[int, list[dict]]:
        """Compare every vtable the last `hv match` compiled with the target's. A slot that
        disagrees means a class declaration with a missing, extra or misplaced virtual."""
        result = self._driver(json.dumps({"vtables": True}))
        header, *mismatches = json.loads(result.stdout)
        return header["compared"], mismatches

    def dependents(self, path: str) -> list[str]:
        """Units whose last `hv match` compile read the file (path under the checkout)."""
        found = []
        for depfile in sorted(self.reports.glob("*.d")):
            if path in {os.path.normpath(word) for word in depfile.read_text().split()}:
                found.append(self.source(depfile.stem))
        return found

    def verdict(self, unit: str, symbol: str) -> FunctionVerdict:
        for v in self.verdicts(unit):
            if v.symbol == symbol:
                return v
        raise KeyError(f"{symbol} is not in {unit}'s report")

    def diff(self, verdict: FunctionVerdict, candidate_object: Path | None = None) -> DiffResult:
        """Attribute the function's remaining differences, for the checkout's own
        compile or for a candidate object from evaluate()."""
        if verdict.row["exact"]:
            return DiffResult(score=100.0, exact=True)
        target = read_function(self.objects / "target" / f"{verdict.unit}.o", verdict.symbol)
        candidate = read_function(candidate_object or self.objects / "base" / f"{verdict.unit}.o", verdict.symbol)
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
            destinations = {name: {a for n, a in row["candidates"] if n == name} for name, _ in row["candidates"]}
            detail = f"references to symbols with unknown addresses: {names}"
            if all(len(a) == 1 for a in destinations.values()):
                detail += f"; every reference agrees, so `hv match --learn {self.source(verdict.unit)}` places them"
            findings.append(DiffFinding(Cause.REFERENCE, 0, detail))
        # Harvest reports the extent's source as "extent" (fde, thunk or null); older reports
        # had an "fde" flag instead
        if row.get("extent", "fde" if row.get("fde") else None) is None:
            detail = f"the target has no unwind entry of {row['size']} bytes at {row['address']}"
            if verdict.symbol.startswith(("_ZTh", "_ZTv")):
                detail += "; GCC 4.4 emits no unwind entry for thunks"
            findings.append(DiffFinding(Cause.PLACEMENT, 0, detail))
        if not findings:
            findings.append(DiffFinding(Cause.UNKNOWN, 0, "bytes identical; the matcher reports the function inexact"))
        return findings
