"""Sort a gruntz-decomp unit's open functions by whether their own source or the compiler's unit state decides them.

MSVC 5.0 compiles a function differently when the number of declarations before it in the translation unit changes, so
a function whose source is right can score below 100 only because of its unit's state. This module measures that. For
each open unit it inserts k inert declarations, `typedef int concord_state_pad_K_I;` one per line, at each cut point,
compiles every variant with the checkout's own compiler, and scores each open function in each variant. The pads exist
only in the compiles: the source goes to the driver as a candidate and a header variant is passed as an overlay, so no
tracked file of the checkout is written.

Cut points in the unit's source are the line before each #include and the line after the last one. With headers, each
project header under include/ that the unit reaches gets one cut point, before the first line of code, which is the
first line that is neither blank, a comment, nor a preprocessor directive.

The baseline is the unit compiled with no pad, by the same path as the variants, so a score that differs from the build
report only because of the compile itself is not counted as a state effect. Each open function gets one class:
exact when the baseline is already 100, state when some variant is 100, improves when no variant is 100 but the best
variant is at least 0.5 above the baseline, and source when neither holds.

Compiles are cached in the save file, keyed by the build report, HEAD and the diff from HEAD, so an interrupted run
resumes where it stopped. A compile that fails is cached too, until the save file is removed or the checkout changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from concord.gruntz import REPORT, Gruntz
from concord.includes import Tree, closure

EXACT = 100.0
RISE = 0.5  # the smallest gain over the baseline that makes a function "improves"
MAX_JOBS = 4  # compiles in parallel, however high CONCORD_JOBS is
# A typedef, not an enumerator: cl 5.0 counts the two differently, and only the typedef (like an extern variable) moves the
# palette builders the way the declaration count does. An enumerator pad leaves them as they are.
PAD = "typedef int concord_state_pad_{k}_{i};\n"
CLASSES = ("exact", "state", "improves", "source")
INCLUDE_LINE = re.compile(rb"^\s*#\s*include\b")
BLOCK_COMMENT = re.compile(rb"/\*.*?\*/")


def rounded(value: float) -> float:
    return round(value, 4)


def worker_count() -> int:
    return max(1, min(MAX_JOBS, int(os.environ.get("CONCORD_JOBS", str(MAX_JOBS)))))


class Cut(NamedTuple):
    """Where a pad goes: before the 1-based `line` of `path`, the unit's source or a header under include/."""

    path: str
    line: int

    def label(self) -> str:
        return f"{self.path}:{self.line}"


class State(NamedTuple):
    """One variant: the pad of `offset` declarations at `cut`."""

    cut: str
    offset: int

    def key(self) -> str:
        return f"{self.cut}@{self.offset}"

    def json(self) -> dict:
        return {"cut": self.cut, "offset": self.offset}


def pad(k: int) -> bytes:
    return b"".join(PAD.format(k=k, i=i).encode() for i in range(k))


def insert_pad(text: bytes, line: int, k: int) -> bytes:
    """The text with k pad declarations before its 1-based `line`; the pads are the only change."""
    lines = text.splitlines(keepends=True)
    if 2 <= line <= len(lines) + 1 and not lines[line - 2].endswith(b"\n"):
        lines[line - 2] += b"\n"  # a last line with no newline must not take the first pad onto its line
    return b"".join(lines[: line - 1]) + pad(k) + b"".join(lines[line - 1 :])


def source_cuts(text: bytes) -> list[int]:
    """The 1-based lines a source's pads go before: each #include line, and the line after the last one."""
    lines = text.splitlines()
    marks = [index for index, line in enumerate(lines) if INCLUDE_LINE.match(line)]
    if not marks:
        return []
    return sorted({index + 1 for index in marks} | {marks[-1] + 2})


def first_declaration(text: bytes) -> int | None:
    """The 1-based line of the first line of code, or None when the header has none."""
    in_comment = in_directive = False
    for number, line in enumerate(text.splitlines(), start=1):
        if in_directive:
            in_directive = line.rstrip().endswith(b"\\")
            continue
        code = line
        if in_comment:
            if b"*/" not in code:
                continue
            code = code.split(b"*/", 1)[1]
            in_comment = False
        code = BLOCK_COMMENT.sub(b"", code)
        if b"/*" in code:
            code, in_comment = code.split(b"/*", 1)[0], True
        code = code.split(b"//", 1)[0].strip()
        if not code:
            continue
        if code.startswith(b"#"):
            in_directive = code.endswith(b"\\")
            continue
        return number
    return None


def project_headers(root: Path, source: str) -> list[str]:
    """The headers under include/ that the source reaches, the only ones an overlay can replace."""
    tree = Tree.working(root, [source])
    return sorted(path for path in closure(tree, source) if path.startswith("include/") and path != source)


def fingerprint(root: Path) -> dict[str, str]:
    """What the checkout's compiles depend on: the build report, HEAD, and the diff from HEAD."""
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    diff = subprocess.run(["git", "-C", str(root), "diff", "--binary", "HEAD"], capture_output=True, check=True).stdout
    report = hashlib.sha256((root / REPORT).read_bytes()).hexdigest()
    return {"head": head, "diff": hashlib.sha256(diff).hexdigest(), "report": report}


class Store:
    """Compiled scores by unit and state, kept in the save file when there is one."""

    def __init__(self, path: Path | None, settings: dict[str, str]) -> None:
        self.path = path
        self.settings = settings
        self.units: dict[str, dict[str, dict]] = {}
        if path and path.exists():
            saved = json.loads(path.read_text())
            if saved.get("settings") != settings:
                raise ValueError(f"{path} was written for another checkout state or build report; remove it to start over")
            self.units = saved["units"]

    def get(self, unit: str, key: str) -> dict | None:
        return self.units.get(unit, {}).get(key)

    def put(self, unit: str, key: str, value: dict) -> None:
        self.units.setdefault(unit, {})[key] = value
        if self.path:
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps({"settings": self.settings, "units": self.units}) + "\n")
            os.replace(temporary, self.path)


def scores_of(evaluation, symbols: list[str]) -> dict:
    """The open functions' scores in one compile, or the compiler's complaint."""
    if evaluation.error:
        return {"error": evaluation.error.strip()[-800:]}
    emitted = {row["symbol"]: rounded(row["fuzzy"]) for row in evaluation.sections[0]["functions"]}
    return {"scores": {symbol: emitted[symbol] for symbol in symbols if symbol in emitted}}


def discard(evaluations) -> None:
    """Remove the compile directories of read evaluations: each holds a copy of its candidate and of the retail object, about 128 KB."""
    for evaluation in evaluations:
        for section in evaluation.sections:
            if "retail" in section:
                shutil.rmtree(Path(section["retail"]).parent, ignore_errors=True)


def classify(open_rows: list[dict], baseline: dict[str, float], variants: dict[State, dict[str, float]]) -> list[dict]:
    """One row per open function: its class, its best score over all states, and the states where it is exact."""
    rows = []
    for verdict in open_rows:
        symbol = verdict["symbol"]
        current = baseline.get(symbol, 0.0)
        exact = [state.json() for state, scores in variants.items() if scores.get(symbol, 0.0) >= EXACT]
        best = max([current, *(scores.get(symbol, 0.0) for scores in variants.values())])
        if current >= EXACT:
            kind = "exact"
        elif exact:
            kind = "state"
        elif best - current >= RISE:
            kind = "improves"
        else:
            kind = "source"
        rows.append({"symbol": symbol, "bytes": verdict["bytes"], "current": current, "best": rounded(best), "class": kind,
                     "exact_states": exact})  # fmt: skip
    return rows


def summarize_unit(rows: list[dict], variants: dict[State, dict[str, float]]) -> dict:
    """Per-class counts and bytes, the states that make every open function exact, and the state with the most exact."""
    counts = {kind: {"functions": 0, "bytes": 0} for kind in CLASSES}
    for row in rows:
        counts[row["class"]]["functions"] += 1
        counts[row["class"]]["bytes"] += row["bytes"]
    symbols = [row["symbol"] for row in rows]
    exact = {state: sum(scores.get(symbol, 0.0) >= EXACT for symbol in symbols) for state, scores in variants.items()}
    everything = [state.json() for state, count in exact.items() if symbols and count == len(symbols)]
    best = max(exact, key=exact.get, default=None)
    best_state = best.json() | {"exact": exact[best]} if best is not None and exact[best] else None
    return {"summary": counts, "all_exact_states": everything, "best_state": best_state}


def empty_result(source: str, error: str = "") -> dict:
    return {"source": source, "functions": [], "states": 0, "failed_states": {}, "error": error, **summarize_unit([], {})}


def triage_unit(target: Gruntz, unit: str, headers: bool, offsets: list[int], store: Store, progress: Callable[[str], None]) -> dict:
    source = target.source(unit)
    open_rows = [{"symbol": verdict.symbol, "bytes": verdict.row["size"]} for verdict in target.verdicts(unit) if verdict.row["fuzzy"] < EXACT]
    if not open_rows:
        return empty_result(source)
    symbols = [row["symbol"] for row in open_rows]
    text = target.source_path(unit).read_bytes()
    jobs = worker_count()

    baseline = store.get(unit, "baseline")
    if baseline is None:
        baseline = scores_of(target.evaluate(unit, {"candidate": text}, jobs=jobs)["candidate"], symbols)
        if "error" in baseline:
            return empty_result(source, baseline["error"])  # not cached: a rerun compiles it again
        store.put(unit, "baseline", baseline)

    wanted: dict[State, bytes | None] = {}  # state -> the source it needs, or None when a header overlay carries the pad
    for line in source_cuts(text):
        for k in offsets:
            wanted[State(Cut(source, line).label(), k)] = insert_pad(text, line, k)
    header_cuts = {}
    if headers:
        for path in project_headers(target.root, source):
            body = (target.root / path).read_bytes()
            line = first_declaration(body)
            if line is not None:
                header_cuts[Cut(path, line).label()] = (path, body, line)
                for k in offsets:
                    wanted[State(Cut(path, line).label(), k)] = None

    todo = {state: body for state, body in wanted.items() if body is not None and store.get(unit, state.key()) is None}
    if todo:
        names = {state: f"v{index}" for index, state in enumerate(todo)}
        evaluations = target.evaluate(unit, {names[state]: body for state, body in todo.items()}, jobs=jobs)
        for state in todo:
            store.put(unit, state.key(), scores_of(evaluations[names[state]], symbols))
            progress(f"{unit}: {state.key()}")
        discard(evaluations.values())
    for state, body in wanted.items():
        if body is not None or store.get(unit, state.key()) is not None:
            continue
        path, header, line = header_cuts[state.cut]
        overlay = {path: insert_pad(header, line, state.offset)}
        evaluation = target.evaluate(unit, {"candidate": text}, headers=overlay, jobs=jobs)["candidate"]
        store.put(unit, state.key(), scores_of(evaluation, symbols))
        discard([evaluation])
        progress(f"{unit}: {state.key()}")

    variants: dict[State, dict[str, float]] = {}
    failed: dict[str, str] = {}
    for state in wanted:
        value = store.get(unit, state.key())
        if "error" in value:
            lines = value["error"].strip().splitlines()
            failed[state.key()] = lines[-1] if lines else "no message"
        else:
            variants[state] = value["scores"]
    rows = classify(open_rows, baseline["scores"], variants)
    return {"source": source, "functions": rows, "states": len(variants), "failed_states": failed, "error": "", **summarize_unit(rows, variants)}


def run(target: Gruntz, units: list[str] | None = None, headers: bool = False, offsets: list[int] | None = None,
        save: Path | None = None, progress: Callable[[str], None] = lambda message: None) -> dict:
    """Triage the named units, or by default every unit with an open function in the checkout's report."""
    offsets = list(range(1, 8)) if offsets is None else offsets
    chosen = [target.slug(unit) for unit in units] if units else target.open_units()
    store = Store(save, fingerprint(target.root) | {"pad": PAD})
    results = {}
    for index, unit in enumerate(chosen, start=1):
        progress(f"{unit}: unit {index}/{len(chosen)}")
        results[unit] = triage_unit(target, unit, headers, offsets, store, progress)
    return {"offsets": offsets, "headers": headers, "units": results, "summary": totals(results)}


def totals(results: dict[str, dict]) -> dict:
    counts = {kind: {"functions": 0, "bytes": 0} for kind in CLASSES}
    for result in results.values():
        for kind, value in result["summary"].items():
            counts[kind]["functions"] += value["functions"]
            counts[kind]["bytes"] += value["bytes"]
    return {"units": len(results), "failed_units": sum(bool(result["error"]) for result in results.values()), "classes": counts,
            "units_with_all_exact_state": sorted(unit for unit, result in results.items() if result["all_exact_states"])}  # fmt: skip


def table(results: dict[str, dict]) -> str:
    head = ["unit", "function", "bytes", "current", "best", "class", "exact states"]
    rows = []
    for unit, result in results.items():
        for row in result["functions"]:
            first = row["exact_states"][0] if row["exact_states"] else None
            where = f"{len(row['exact_states'])}, first {first['cut']} offset {first['offset']}" if first else "0"
            rows.append([unit, row["symbol"][:70], str(row["bytes"]), f"{row['current']:.4f}", f"{row['best']:.4f}", row["class"], where])
    widths = [max(len(row[column]) for row in [head, *rows]) for column in range(len(head) - 1)]
    lines = ["  ".join(text.ljust(widths[i]) for i, text in enumerate(head[:-1])) + "  " + head[-1]]
    lines += ["  ".join(text.ljust(widths[i]) for i, text in enumerate(row[:-1])) + "  " + row[-1] for row in rows]
    return "\n".join(lines)


def offset_range(text: str) -> list[int]:
    """The pad sizes a `--offsets LOW-HIGH` names, without 0, which is the baseline and always compiled."""
    low, _, high = text.partition("-")
    low_value, high_value = int(low), int(high or low)
    if low_value < 0 or high_value < low_value:
        raise ValueError(f"{text} is not a range of pad sizes, such as 1-7")
    return [k for k in range(low_value, high_value + 1) if k > 0]
