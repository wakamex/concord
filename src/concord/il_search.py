"""Search legal edits of one function's captured intermediate language (IL) for the variant that compiles to the
retail code. Each variant is replayed through the unmodified code generator, c2.exe, and scored against retail.

The search tries every legal single edit of the captured body, then the legal edits of the best variants found so
far, up to `depth` edits, within a budget of replays. It stops at the first variant that is exact.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from concord.gruntz import Gruntz
from concord.il import Edit, Item, Symbols, emit_body, neighbours, parse
from concord.transforms import ACCESSOR

WORKERS = 4  # replays run at once; CONCORD_JOBS lowers it
MARKERS = rb"([?_A-Za-z][\x21-\x7e]{3,})\x00"  # gl names, as capture_il's files list them


@dataclass
class Capture:
    """A capture's three IL files (ex bodies, gl global table, sy symbols) as concord.gruntz writes them."""

    directory: Path

    def _file(self, suffix: str) -> Path:
        return next(self.directory.glob(f"*{suffix}"))

    def read(self, suffix: str) -> bytes:
        return self._file(suffix).read_bytes()

    def bodies(self) -> list[tuple[str, int, int]]:
        """Every body in ex, as (symbol, start, end), in order of position. Each gl record holds the body's offset as
        the first 0x80-flagged 32-bit value that points into ex; a body ends where the next one starts."""
        ex, gl = self.read("ex"), self.read("gl")
        starts = []
        for match in re.finditer(MARKERS, gl):
            tail = gl[match.end() : match.end() + 24]
            values = [int.from_bytes(tail[i + 1 : i + 5], "little") for i in range(len(tail) - 5) if tail[i] == 0x80]
            values = [v for v in values if 0 < v < len(ex)]
            if values:
                starts.append((values[0], match[1].decode("latin-1")))
        starts.sort()
        return [(name, start, starts[k + 1][0] if k + 1 < len(starts) else len(ex)) for k, (start, name) in enumerate(starts)]

    def function(self, symbol: str) -> tuple[int, int]:
        for name, start, end in self.bodies():
            if name == symbol:
                return start, end
        raise KeyError(f"{symbol} has no body in the capture")

    def symbols(self, accessors: frozenset[str]) -> Symbols:
        return Symbols(self.read("sy"), self.read("gl"), accessors)

    def variant(self, destination: Path, start: int, end: int, body: bytes) -> Path:
        """Copy the capture to destination with the body at [start, end) replaced. Every gl value that points at or
        after the old end moves by the length change, since those values are the offsets of later bodies."""
        destination.mkdir(parents=True)
        for path in self.directory.iterdir():
            shutil.copy2(path, destination / path.name)
        ex, gl = self.read("ex"), self.read("gl")
        delta = len(body) - (end - start)
        shifted = bytearray(gl)
        for i in range(len(gl) - 4):
            if gl[i] == 0x80:
                value = int.from_bytes(gl[i + 1 : i + 5], "little")
                if end <= value <= len(ex):
                    shifted[i + 1 : i + 5] = (value + delta).to_bytes(4, "little")
        (destination / self._file("ex").name).write_bytes(ex[:start] + body + ex[end:])
        (destination / self._file("gl").name).write_bytes(bytes(shifted))
        return destination


def trivial_accessors(root: Path) -> frozenset[str]:
    """The names of trivial accessors in the checkout's headers, `Name() const { return m_member; }`, that no other
    definition in the headers or sources gives a different body. A call of one only reads memory."""
    texts = [path.read_bytes() for path in sorted((root / "include").rglob("*.h"))]
    texts += [path.read_bytes() for path in sorted((root / "src").rglob("*")) if path.suffix in (".h", ".cpp", ".hpp")]
    trivial: dict[bytes, int] = {}
    for text in texts:
        for match in ACCESSOR.finditer(text):
            trivial[match[1]] = trivial.get(match[1], 0) + 1
    names = []
    for name, count in trivial.items():
        definitions = sum(len(re.findall(rb"\b" + re.escape(name) + rb"\s*\(\s*\)\s*(?:const\s*)?\{", text)) for text in texts)
        if definitions == count:
            names.append(name.decode("latin-1"))
    return frozenset(names)


def _describe(edits: tuple[Edit, ...]) -> list[dict]:
    return [{"kind": e.kind, "text": e.text, "lines": sorted({line for line in e.lines if line is not None})} for e in edits]


def search(
    gruntz: Gruntz,
    unit: str,
    symbol: str,
    capture: Capture,
    depth: int,
    budget: int,
    beam: int,
    workers: int,
    log=print,
) -> dict:
    """Run the search and return its report (see the module docstring and README)."""
    started = time.monotonic()
    accessors = trivial_accessors(gruntz.root)
    symbols = capture.symbols(accessors)
    start, end = capture.function(symbol)
    ex = capture.read("ex")
    original = ex[start:end]
    items = parse(original, symbols)
    if emit_body(items) != original:
        raise RuntimeError("the parser does not give back the captured body's bytes")
    statements = sum(1 for item in items if not isinstance(item, bytes))
    workroot = gruntz.root / "build" / "concord" / gruntz.slug(unit) / f"ilsearch-{uuid.uuid4().hex}"
    workroot.mkdir(parents=True)

    replays = 0
    exact: list[dict] = []
    seen = {original}

    def run(batch: list[tuple[tuple[Edit, ...], tuple[Item, ...], bytes]]) -> list[dict]:
        nonlocal replays
        dirs = {}
        for k, (_, _, body) in enumerate(batch):
            dirs[f"v{k}"] = capture.variant(workroot / f"v{replays + k}", start, end, body)
        evaluations = gruntz.replay_many(unit, dirs, jobs=workers)
        replays += len(batch)
        rows = []
        for k, (edits, _, _) in enumerate(batch):
            evaluation = evaluations[f"v{k}"]
            verdict = None if evaluation.error else evaluation.verdict(symbol)
            fuzzy = verdict.row["fuzzy"] if verdict else None
            rows.append({"edits": edits, "fuzzy": fuzzy, "exact": bool(verdict and verdict.row["exact"]),
                         "error": evaluation.error[-300:] if evaluation.error else ""})
        for path in workroot.glob("v*"):
            shutil.rmtree(path, ignore_errors=True)
        return rows

    baseline = run([((), items, original)])[0]
    if baseline["error"] or baseline["fuzzy"] is None:
        raise RuntimeError(f"the unedited capture does not replay: {baseline['error']}")
    log(f"baseline {symbol}: fuzzy {baseline['fuzzy']:.3f}, {statements} statements")
    frontier: list[tuple[tuple[Edit, ...], tuple[Item, ...]]] = [((), items)]
    rows_all = [baseline]
    for level in range(1, depth + 1):
        batch: list[tuple[tuple[Edit, ...], tuple[Item, ...], bytes]] = []
        for edits, parent in frontier:
            for edit, child in neighbours(parent, symbols):
                body = emit_body(child)
                if body in seen:
                    continue
                seen.add(body)
                batch.append((edits + (edit,), child, body))
        log(f"depth {level}: {len(batch)} new variants, {budget - replays} replays left")
        level_rows: list[dict] = []
        for at in range(0, len(batch), workers):
            if replays >= budget:
                break
            chunk = batch[at : at + workers][: max(0, budget - replays)]
            rows = run(chunk)
            for row, (edits, child, _) in zip(rows, chunk):
                row["_child"] = child
                level_rows.append(row)
            rows_all.extend(rows)
            hits = [row for row in rows if row["exact"]]
            if hits:
                exact.extend(_entry(row) for row in hits)
                break
            if replays >= budget:
                break
        if exact:
            break
        ranked = sorted((r for r in level_rows if r["fuzzy"] is not None), key=lambda r: -r["fuzzy"])
        frontier = [(row["edits"], row["_child"]) for row in ranked[:beam]]
        log(f"depth {level}: {len(level_rows)} replayed; best {ranked[0]['fuzzy']:.3f}" if ranked else f"depth {level}: none replayed")
        if replays >= budget or not frontier:
            break
    shutil.rmtree(workroot, ignore_errors=True)
    candidates = [r for r in rows_all if r["fuzzy"] is not None]
    best = max(candidates, key=lambda r: r["fuzzy"])
    return {
        "symbol": symbol,
        "unit": unit,
        "depth": depth,
        "budget": budget,
        "beam": beam,
        "statements": statements,
        "opaque_items": sum(1 for item in items if isinstance(item, bytes)),
        "body_bytes": len(original),
        "accessors": sorted(accessors),
        "baseline": {"fuzzy": baseline["fuzzy"], "exact": baseline["exact"]},
        "exact": exact,
        "found_exact": bool(exact),
        "best": {"fuzzy": best["fuzzy"], "exact": best["exact"], "edits": _describe(best["edits"])},
        "replays": replays,
        "seconds": round(time.monotonic() - started, 1),
    }


def _entry(row: dict) -> dict:
    return {"fuzzy": row["fuzzy"], "edits": _describe(row["edits"])}


def workers_for_run() -> int:
    return max(1, min(WORKERS, int(os.environ.get("CONCORD_JOBS", WORKERS))))


def run(gruntz_root: Path, unit: str, symbol: str, depth: int, budget: int, beam: int, save: Path | None, log=print) -> dict:
    gruntz = Gruntz(gruntz_root)
    source = gruntz.source_path(unit).read_bytes()
    il, evaluation = gruntz.capture_il(unit, source)
    if il is None:
        raise RuntimeError(f"the unit did not compile: {evaluation.error[-800:]}")
    capture = Capture(il)
    report = search(gruntz, unit, symbol, capture, depth, budget, beam, workers_for_run(), log)
    report["capture"] = str(il)
    if save:
        save.write_text(json.dumps(report, indent=2) + "\n")
    return report
