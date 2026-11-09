"""Compile candidate sources of a gruntz-decomp unit and compare each with the
retail object, using the checkout's own compiler bridge, object canonicalizer,
objdiff build and exact-match rule.

Runs under the Python of the checkout's dev shell, with the checkout as the
working directory and its `scripts/` on PYTHONPATH; concord.gruntz starts it.
It reads one JSON request per line and writes one JSON reply per line, after a
first line `{"ready": true}`:

    {"unit": UNIT, "out": DIRECTORY, "candidates": {NAME: SOURCE_PATH}, "jobs": N, "wibo": PATH}

Each candidate is compiled as a sibling of the unit's source, so its includes
resolve as the unit's do, and the sibling is removed afterwards. The reply is
`{"ok": {NAME: RESULT}, "using": [...]}`, where `using` names the shortcuts
below that held for the unit and a result is `{"object": PATH, "functions":
[...]}` or `{"error": TEXT}`. A request with `"lines": SOURCE_PATH` in place of
`candidates` compiles that one source with cl's /Zd, which adds line records
and leaves the code as it was, and replies `{"ok": {"object": PATH}}` or
`{"ok": {"error": TEXT}}` without scoring it. A function row has `symbol`, `address`, `size`, `fuzzy` (objdiff's score against
the retail object) and `exact` (the checkout's closure rule: an unrounded 100%,
the retail size, and equal ordered relocation streams).

What a candidate costs through the checkout's own helpers, and what is done
about each part:

- The `cl` bridge asks Wine for eight Windows paths per compile, as long as cl
  itself takes on a small unit, so each directory is translated once.
- objdiff's JSON result holds every instruction row of every symbol, 9 MB for a
  large unit, which takes objdiff as long to write as the diff took and Python
  longer to parse. objdiff writes its protobuf form instead and
  objdiff_scores.rs, compiled beside this file on first use, reads the two
  numbers per symbol out of it.
- Workers are processes, since canonicalizing an object is Python.
- cl.exe, c1xx.exe and c2.exe each start a Wine process, 0.2 seconds a compile.
  With `wibo`, the path of a wibo binary (https://github.com/decompals/wibo, a
  minimal loader for 32-bit Windows command-line programs), the same cl.exe runs
  under wibo instead.

Neither shortcut is taken on trust. For each unit the unchanged source is
compiled through the bridge and scored by the checkout's `objdiff_scores`; the
reader is used only if it gives those scores and sizes, and wibo only if its
object then gets the same function rows. A candidate compiled under wibo whose
set of exact functions differs from the unit's is compiled again through the
bridge, so a function is never called exact, or no longer exact, on wibo's
word. Whole objects are not compared: cl 5.0 orders some data sections by the
loader's memory layout, so they differ between Wine and wibo in 19 of 289 units
whose function code is identical.

A precompiled header would halve the compile and is not used: cl 5.0 generates
different code for some functions after one.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import re
import struct
import subprocess
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from gruntz.core.paths import dxsdk_dir, msvc_dir
from gruntz.graph.cc import coff_defect, stabilise
from gruntz.permute.tu_state_noise import (
    binding_rows,
    canonicalize_disposable_object,
    exact_closure_rejections,
    load_units,
    objdiff_scores,
    object_metrics,
)
from gruntz.tool import ToolError, cl, wine

TIMEOUT = 300.0
READER = Path(__file__).with_name("objdiff_scores")
_windows: dict[Path, str] = {}  # directory -> its Windows path
_proofs: dict[str, tuple[bool, bool, frozenset[str]]] = {}  # a unit's source and flags -> what Unit.prove found


def winepath(path: Path | str) -> str:
    """The path as Wine sees it, from one `winepath` run per directory."""
    path = Path(path)
    directory = path if path.is_dir() else path.parent
    if directory not in _windows:
        _windows[directory] = wine.winepath(directory)
    return _windows[directory] if directory == path else f"{_windows[directory]}\\{path.name}"


cl.winepath = winepath


def system_directories() -> dict[str, tuple[Path, Path]]:
    """Where cl finds its system headers and libraries; the bridge keeps these in
    Wine's registry, and wibo needs them in the environment."""
    return {"INCLUDE": (dxsdk_dir() / "Include", msvc_dir() / "include"), "LIB": (dxsdk_dir() / "Lib", msvc_dir() / "lib")}


def build_reader() -> bool:
    """Compile objdiff_scores.rs when it is newer than its binary; whether there
    is a binary to use."""
    source = READER.with_suffix(".rs")
    if not source.exists():
        return False
    if not READER.exists() or READER.stat().st_mtime < source.stat().st_mtime:
        fresh = READER.with_name(f"{READER.name}.{os.getpid()}")
        try:
            done = subprocess.run(["rustc", "--edition", "2021", "-O", "-o", str(fresh), str(source)], capture_output=True, text=True, check=False)
        except OSError as error:
            print(f"no rustc for objdiff_scores.rs ({error}); scoring through the checkout's JSON reader", file=sys.stderr)
            return False
        if done.returncode:
            print(f"objdiff_scores.rs did not compile; scoring through the checkout's JSON reader:\n{done.stderr}", file=sys.stderr)
            return False
        fresh.replace(READER)
    return True


def compile(source: Path, out: Path, flags: list[str], wibo: str | None) -> str:
    """Compile one source to `out`, without cl's time stamp so equal sources give
    equal objects; the compiler's complaint when it wrote no complete object."""
    if wibo:
        environment = os.environ | {name: ";".join(winepath(d) for d in found) for name, found in system_directories().items()}
        out.unlink(missing_ok=True)
        command = [wibo, str(wine.era_tool("cl.exe")), *cl.repo_include_flags(), *flags, f"/Fo{winepath(out)}", winepath(source)]
        try:
            done = subprocess.run(command, cwd=out.parent, env=environment, capture_output=True, timeout=TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            return f"cl under wibo ran past {TIMEOUT:g} seconds"
        if not out.exists():
            return (done.stdout + done.stderr).decode(errors="replace")[-4000:] or "cl under wibo wrote no object"
    else:
        try:
            cl.compile(source, out, flags, timeout=TIMEOUT)
        except ToolError as error:
            return str(error)[-4000:]
    data = out.read_bytes()
    if coff_defect(data):
        return f"cl wrote an incomplete object: {coff_defect(data)}"
    out.write_bytes(stabilise(data))
    return ""


def scores(retail: Path, candidate: Path, anchor: str, reader: bool) -> tuple[dict[str, float], dict[str, int], str]:
    """objdiff's score and size of each symbol of the candidate, as the checkout's
    `objdiff_scores` gives them, and objdiff's complaint when there are none."""
    if not reader:
        found, sizes, _, log = objdiff_scores(retail, candidate, anchor)
        return found, sizes, log
    result = candidate.with_suffix(".diff")
    command = ["objdiff-cli", "diff", "-1", str(retail), "-2", str(candidate), anchor, "-o", str(result), "--format", "proto"]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode or not result.exists():
        return {}, {}, done.stdout + done.stderr
    read = subprocess.run([str(READER), str(result)], capture_output=True, text=True, check=False)
    result.unlink()
    if read.returncode:
        return {}, {}, read.stderr
    found, sizes = {}, {}
    for line in read.stdout.splitlines():
        name, size, percent = line.split("\t")
        if int(size):  # objdiff's JSON leaves a zero size out, so `objdiff_scores` has none for the symbol
            sizes[name] = int(size)
        if percent:
            found[name] = as_json(float(percent))
    return found, sizes, done.stderr


def as_json(score: float) -> float:
    """The number `objdiff_scores` reads for the same 32-bit score. objdiff's JSON
    writes the shortest decimal that gives the float back, rounding a tie to even,
    where the reader's decimal rounds the tie up: 28.3203125 is 28.320312 there
    and 28.320313 here."""
    bits = struct.pack("<f", score)
    exact = struct.unpack("<f", bits)[0]
    for digits in range(1, 10):
        short = float(f"{exact:.{digits}g}")
        if struct.pack("<f", short) == bits:
            return short
    return exact


class Unit:
    """A unit's compile settings and its retail side, read for each request so a
    rebuild between requests is seen."""

    def __init__(self, root: Path, name: str, out: Path, wibo: str | None, reader: bool) -> None:
        settings = load_units(root)[name]
        self.source = root / settings["source"]
        self.flags = settings["flags"]
        retail = root / "build/objdiff/target-new" / f"{name}.c.obj"
        if not retail.is_file():
            raise FileNotFoundError(f"{retail} is missing; run `gruntz build`")
        self.retail = canonicalize_disposable_object(retail, out / "retail.obj")
        self.retail_metrics = object_metrics(self.retail)
        self.functions = {
            row["name"]: (int(row["rva"], 0), int(row["size"], 0))
            for row in binding_rows(root)
            if row["space"] == "text" and row["name"] and (row["unit"] == name or name in row["also_units"].split(","))
        }
        # siblings a driver left when it was killed mid-compile
        for stale in self.source.parent.glob(f".{self.source.stem}.concord*"):
            owner = re.match(r"concord(\d+)-", stale.name.split(".")[2])
            if owner and not Path(f"/proc/{owner[1]}").exists():
                stale.unlink(missing_ok=True)
        self.reader, self.wibo, self.exact = False, None, frozenset()
        if wibo or reader:
            self.reader, agrees, self.exact = self.prove(out, wibo, reader)
            self.wibo = wibo if agrees else None
        # every path a compile translates, so the workers start no Wine process for one
        for path in (self.source, out, *(d for found in system_directories().values() for d in found)):
            winepath(path)
        cl.repo_include_flags()
        self.windows = dict(_windows)

    def prove(self, out: Path, wibo: str | None, reader: bool) -> tuple[bool, bool, frozenset[str]]:
        """Check the shortcuts against the checkout's own path on the unit's
        unchanged source, once per source and flags: whether the reader gives
        `objdiff_scores`' scores and sizes, whether an object compiled under wibo
        gets the bridge's function rows, and the functions exact through the bridge."""
        key = hashlib.sha256(self.source.read_bytes() + repr((self.flags, wibo, reader)).encode()).hexdigest()
        if key not in _proofs:
            bridge, rows = build(self, self.source, out, "bridge", None)
            if bridge is None:
                raise RuntimeError(f"the unit's own source does not compile:\n{rows}")
            anchor = next(iter(self.functions), "")
            reads = reader and scores(self.retail, bridge, anchor, True)[:2] == scores(self.retail, bridge, anchor, False)[:2]
            if reader and not reads:
                print(f"objdiff_scores disagrees with the checkout's reader on {self.source.name}; not using it", file=sys.stderr)
            self.reader = reads
            agrees = bool(wibo) and build(self, self.source, out, "wibo", wibo)[1] == rows
            if wibo and not agrees:
                print(f"wibo does not reproduce the bridge's verdicts for {self.source.name}; compiling it under Wine", file=sys.stderr)
            _proofs[key] = (reads, agrees, exact(rows))
        return _proofs[key]


def exact(rows: list[dict]) -> frozenset[str]:
    return frozenset(row["symbol"] for row in rows if row["exact"])


def build(unit: Unit, source: Path, out: Path, name: str, wibo: str | None) -> tuple[Path | None, list[dict] | str]:
    """Compile a source and score its object: the canonical object and one row per
    function it emits, or None and what went wrong."""
    raw = out / f"{name}.raw.obj"
    error = compile(source, raw, unit.flags, wibo)
    if error:
        return None, error
    candidate = canonicalize_disposable_object(raw, out / f"{name}.obj")
    raw.unlink()
    found, sizes, log = scores(unit.retail, candidate, next(iter(unit.functions), ""), unit.reader)
    if not found:
        return None, f"objdiff gave no scores:\n{log[-4000:]}"
    metrics = object_metrics(candidate)
    rows = []
    for symbol, (address, size) in unit.functions.items():
        if symbol not in found:
            continue  # the candidate does not emit it
        rejections = exact_closure_rejections(
            found[symbol], sizes.get(symbol), size, metrics.get(symbol, {}), unit.retail_metrics.get(symbol, {})
        )
        rows.append({"symbol": symbol, "address": f"{address:#x}", "size": size, "fuzzy": found[symbol], "exact": not rejections})
    return candidate, rows


def evaluate(unit: Unit, out: Path, name: str, path: Path) -> dict:
    _windows.update(unit.windows)
    sibling = unit.source.with_name(f".{unit.source.stem}.concord{os.getpid()}-{name}{unit.source.suffix}")
    sibling.write_bytes(path.read_bytes())
    try:
        candidate, rows = build(unit, sibling, out, name, unit.wibo)
        if unit.wibo and isinstance(rows, list) and exact(rows) != unit.exact:
            candidate, rows = build(unit, sibling, out, name, None)
    finally:
        sibling.unlink(missing_ok=True)
    if isinstance(rows, str):
        return {"error": rows.replace(sibling.name, unit.source.name)}
    return {"object": str(candidate), "functions": rows}


def line_object(unit: Unit, out: Path, path: Path) -> dict:
    """The source compiled with line records, unscored."""
    sibling = unit.source.with_name(f".{unit.source.stem}.concord{os.getpid()}-lines{unit.source.suffix}")
    sibling.write_bytes(path.read_bytes())
    try:
        error = compile(sibling, out / "lines.obj", [*unit.flags, "/Zd"], unit.wibo)
    finally:
        sibling.unlink(missing_ok=True)
    return {"error": error.replace(sibling.name, unit.source.name)} if error else {"object": str(out / "lines.obj")}


def serve() -> None:
    root = Path.cwd()
    reader = build_reader()
    pool, workers = None, 0
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            out = Path(request["out"])
            unit = Unit(root, request["unit"], out, request.get("wibo"), reader)
            if "lines" in request:
                print(json.dumps({"ok": line_object(unit, out, Path(request["lines"]))}), flush=True)
                continue
            jobs = max(1, int(request.get("jobs", 1)))
            if jobs != workers:
                if pool:
                    pool.shutdown()
                pool, workers = ProcessPoolExecutor(jobs, multiprocessing.get_context("fork")), jobs
            futures = {n: pool.submit(evaluate, unit, out, n, Path(p)) for n, p in request["candidates"].items()}
            using = [name for name, used in (("objdiff_scores", unit.reader), ("wibo", unit.wibo)) if used]
            reply = {"ok": {n: future.result() for n, future in futures.items()}, "using": using}
        except Exception:  # noqa: BLE001 (the client reports it and decides)
            reply = {"error": traceback.format_exc()}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    serve()
