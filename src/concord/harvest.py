"""Read a Harvest checkout's matching outputs.

`hv match` writes, per unit, a report (build/match/<build>/<unit>.json) with every
function's verdict and every unresolved reference, and two objects for objdiff
(build/objdiff/<build>/{target,base}/<unit>.o): the target's bytes cut out of the
executable and laid out like the compiled object, and the compiled object itself.
concord reads those files; it does not import Harvest's code.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
import time
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


def function_key(row: dict) -> str:
    """A function's stable identifier across compiles: its symbol, or its target
    address for a GCC synthetic clone (T.<uid>) whose symbol suffix is the
    declaration counter."""
    symbol = row["symbol"]
    return row["address"] if symbol.startswith("T.") and symbol[2:].isdigit() and row.get("address") else symbol


@dataclass
class Evaluation:
    """One candidate source compiled and compared by Harvest's matcher."""

    name: str
    unit: str  # report slug
    object: Path | None = None
    error: str = ""
    sections: list[dict] = field(default_factory=list)
    fuzzy: dict[str, float] = field(default_factory=dict)  # objdiff's score by symbol, as decomp.dev reports it
    score_basis: str = ""
    score_error: str = ""

    def verdict(self, symbol: str) -> FunctionVerdict | None:
        for section in self.sections:
            for row in section.get("functions", []):
                if row["symbol"] == symbol:
                    return FunctionVerdict(self.unit, symbol, row, section)
        return None

    def verdict_by_key(self, key: str) -> FunctionVerdict | None:
        for section in self.sections:
            for row in section.get("functions", []):
                if function_key(row) == key:
                    return FunctionVerdict(self.unit, row["symbol"], row, section)
        return None

    def fuzzy_by_key(self) -> dict[str, float]:
        rows = [row for section in self.sections for row in section.get("functions", [])]
        seen = {row["symbol"] for row in rows}
        scores = {function_key(row): self.fuzzy[row["symbol"]] for row in rows if row["symbol"] in self.fuzzy}
        for name, score in self.fuzzy.items():
            if name not in seen:
                scores[name] = score
        return scores

    def exact_functions(self) -> set[str]:
        return {function_key(row) for s in self.sections for row in s.get("functions", []) if row["exact"]}


_drivers: dict[Path, subprocess.Popen] = {}  # one driver per checkout, for the life of the process


@atexit.register
def _stop_drivers() -> None:
    processes = list(_drivers.values())
    _drivers.clear()
    for process in processes:
        if process.poll() is None:
            process.stdin.close()
            process.terminate()


class Harvest:
    def __init__(self, root: Path, build: str = BUILD) -> None:
        self.root = root.resolve()
        self.build = build
        self.reports = root / "build" / "match" / build
        self.objects = root / "build" / "objdiff" / build
        if not self.reports.is_dir():
            raise FileNotFoundError(f"{self.reports} is missing; run `hv match` in {root}")

    def _driver(self, request: dict) -> object:
        """Send one request to the checkout's driver and return its result. The
        driver (harvest_driver.py under the checkout's Python) is started on the
        first request and kept for the life of this process, so the target is
        loaded once; a driver that has died is started again."""
        process = _drivers.get(self.root)
        if process is None or process.poll() is not None:
            log = tempfile.TemporaryFile(mode="w+")  # noqa: SIM115 (read when the driver stops, which is later)
            process = subprocess.Popen(
                ["uv", "--no-config", "run", "--locked", "--project", str(self.root), "python", str(DRIVER), "--serve"],
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
            )
            process.log = log
            _drivers[self.root] = process
        try:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
        except BrokenPipeError:
            line = ""
        if not line:
            process.log.seek(0)
            raise RuntimeError(f"harvest_driver.py stopped:\n{process.log.read()[-3000:]}")
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(f"harvest_driver.py failed:\n{reply['error']}")
        return reply["ok"]

    def clean(self, older_than: float) -> tuple[int, int]:
        """Remove concord's work directories under build/concord that were last
        changed more than `older_than` seconds ago; (directories removed, bytes
        freed). Each evaluate, overlay or debug batch leaves one, with every
        candidate's source and object."""
        base = self.root / "build" / "concord"
        cutoff = time.time() - older_than
        removed = freed = 0
        for group in sorted(base.iterdir()) if base.is_dir() else []:
            for batch in sorted(group.iterdir()) if group.is_dir() else []:
                if batch.is_dir() and batch.stat().st_mtime < cutoff:
                    freed += sum(f.stat().st_size for f in batch.rglob("*") if f.is_file())
                    shutil.rmtree(batch)
                    removed += 1
        return removed, freed

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
        evaluations = {}
        for name, row in self._driver(request).items():
            evaluations[name] = Evaluation(
                name,
                slug,
                Path(row["object"]) if "object" in row else None,
                row.get("error", ""),
                row.get("sections", []),
                row.get("fuzzy", {}),
            )
        return evaluations

    def evaluate_overlays(self, items: dict[str, tuple], *, baseline_target_fallback: bool = False,
                          units: dict[str, dict[str, int]] | None = None,
                          claims: list[dict] | None = None, inline_decisions: bool = False) -> dict[str, Evaluation]:
        """Compile each item, a unit source path with repository files replaced
        (path under the checkout -> new bytes), in one batch, and compare it with
        the target. An item is (unit, overlays) or (unit, overlays, flags) where
        flags are extra compiler options for measuring, such as --param values.
        inline_decisions retains GCC's IPA and early-inline dumps beside each
        object, with a separate dump base for every item.
        A flag audit can fall back to its own freshly delinked baseline target
        if a variant cannot be delinked; ordinary searches never use it."""
        out = self.root / "build" / "concord" / "overlays" / uuid.uuid4().hex
        out.mkdir(parents=True)
        files: dict[bytes, Path] = {}
        request_items = {}
        for name, item in items.items():
            unit, overlays = item[0], item[1]
            mapped = {}
            for path, text in overlays.items():
                if text not in files:
                    files[text] = out / f"overlay{len(files)}{Path(path).suffix}"
                    files[text].write_bytes(text)
                mapped[path] = str(files[text])
            request_items[name] = {"unit": unit, "overlays": mapped, "flags": list(item[2]) if len(item) > 2 else [],
                                   "inline_decisions": inline_decisions}
        result = self._driver({"out": str(out), "items": request_items, "baseline_target_fallback": baseline_target_fallback,
                               "units": units or {}, "claims": claims or []})
        return {
            name: Evaluation(
                name,
                self.slug(items[name][0]),
                Path(row["object"]) if "object" in row else None,
                row.get("error", ""),
                row.get("sections", []),
                row.get("fuzzy", {}),
                row.get("score_basis", ""),
                row.get("score_error", ""),
            )
            for name, row in result.items()
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
        return {names[n]: Path(row["object"]) for n, row in self._driver(request).items() if "object" in row}

    def vtables(self) -> tuple[int, list[dict]]:
        """Compare every vtable the last `hv match` compiled with the target's. A slot that
        disagrees means a class declaration with a missing, extra or misplaced virtual."""
        header, *mismatches = self._driver({"vtables": True})
        return header["compared"], mismatches

    def dependents(self, path: str) -> list[str]:
        """Units whose last `hv match` compile read the file (path under the checkout)."""
        registered = (self.root / "config" / self.build / "units.toml").read_text()
        found = []
        for depfile in sorted(self.reports.glob("*.d")):
            if not depfile.with_suffix(".json").exists():
                continue  # left by a unit that did not compile
            if path in {os.path.normpath(word) for word in depfile.read_text().split()}:
                unit = self.source(depfile.stem)
                if f'["{unit}"]' in registered:  # a report can outlive its unit
                    found.append(unit)
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
