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
`{"ok": {"error": TEXT}}` without scoring it. A request may add `"include": DIRECTORY`, a tree of
replacement headers laid out as under the checkout's `include/`, searched before the checkout's own
include directories, so a header variant is measured without touching the checkout; every
`#include` in gruntz-decomp uses angle brackets, so the replacement is found wherever it is included. One with `"types": SOURCE_PATH`
and `"range": [START, END]` replies `{"ok": {TEXT: [TYPE, NARROW]}}`: the type
libclang gives each expression between those byte offsets whose type is a
number, an enumeration or a pointer, and whether it is an integer narrower than
int, for the rewrite that names a temporary, since cl 5.0 has nothing like
`__typeof__`. One with `"layouts": true` replies `{"ok": {RECORD: {"size": N, "pointers": [[OFFSET,
TARGET], ...]}}}`, where each record the unit defines keeps its pointer fields, for the behaviour oracle
to lay out memory by type. A function row has `symbol`, `address`, `size`, `fuzzy` (objdiff's score against
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

A request with `"capture": SOURCE_PATH` compiles that source through c2wrap.c (built here with the
checkout's cl on first use and passed to cl as `/B2`), which keeps the intermediate-language files cl
hands the code generator, c2.exe, and the flags it passes it; it replies `{"ok": {"il": DIRECTORY,
"object": PATH, "functions": [...]}}`. One with `"replay": DIRECTORY` runs c2.exe alone on such a
directory, edited or not, and replies with the object and its function rows. One with `"regtrace":
SOURCE_PATH` captures the source the same way and then runs c2.exe on it under winedbg, stopping where
c2's code generator starts a function (REGTRACE_RESET) and where it gives a temporary a register
(REGTRACE_GRANT), and replies with winedbg's log next to the capture's reply; concord.regtrace reads it.
One with `"regforce": DIRECTORY` and `"force": {N: REGISTER}` runs c2.exe on a capture under winedbg,
overwrites the register of the Nth register grant (counting from 1) with c2's number for REGISTER
before c2 records it, and replies with the object and its function rows: what c2 would have emitted
had its allocator chosen so. c2.exe is a deterministic
function of those files: a replay of an unedited capture gives the capture's object.

A precompiled header would halve the compile and is not used: cl 5.0 generates
different code for some functions after one.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import re
import shutil
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


def compile(source: Path, out: Path, flags: list[str], wibo: str | None, include: Path | None = None) -> str:
    """Compile one source to `out`, without cl's time stamp so equal sources give
    equal objects, with `include` searched before the checkout's include
    directories; the compiler's complaint when it wrote no complete object."""
    first = [f"/I{winepath(include)}"] if include else []
    if wibo:
        environment = os.environ | {name: ";".join(winepath(d) for d in found) for name, found in system_directories().items()}
        out.unlink(missing_ok=True)
        command = [wibo, str(wine.era_tool("cl.exe")), *first, *cl.repo_include_flags(), *flags, f"/Fo{winepath(out)}", winepath(source)]
        try:
            done = subprocess.run(command, cwd=out.parent, env=environment, capture_output=True, timeout=TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            return f"cl under wibo ran past {TIMEOUT:g} seconds"
        if not out.exists():
            return (done.stdout + done.stderr).decode(errors="replace")[-4000:] or "cl under wibo wrote no object"
    elif include:
        # cl.compile puts its extra include directories after the checkout's, where they shadow nothing
        out.unlink(missing_ok=True)
        command = ["wine", str(wine.era_tool("cl.exe")), *first, *cl.repo_include_flags(), *flags, f"/Fo{winepath(out)}", winepath(source)]
        output, _ = wine.run(command, cwd=out.parent, timeout=TIMEOUT, success=out)
        if not out.exists():
            return output[-4000:] or "cl wrote no object"
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


def build(unit: Unit, source: Path, out: Path, name: str, wibo: str | None, include: Path | None = None) -> tuple[Path | None, list[dict] | str]:
    """Compile a source and score its object: the canonical object and one row per
    function it emits, or None and what went wrong."""
    raw = out / f"{name}.raw.obj"
    error = compile(source, raw, unit.flags, wibo, include)
    if error:
        return None, error
    return scored(unit, raw, out, name)


def scored(unit: Unit, raw: Path, out: Path, name: str) -> tuple[Path | None, list[dict] | str]:
    """Score a compiled object against the retail one: the canonical object and one row per function."""
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


def evaluate(unit: Unit, out: Path, name: str, path: Path, include: Path | None = None) -> dict:
    _windows.update(unit.windows)
    sibling = unit.source.with_name(f".{unit.source.stem}.concord{os.getpid()}-{name}{unit.source.suffix}")
    sibling.write_bytes(path.read_bytes())
    try:
        candidate, rows = build(unit, sibling, out, name, unit.wibo, include)
        if unit.wibo and isinstance(rows, list) and exact(rows) != unit.exact:
            candidate, rows = build(unit, sibling, out, name, None, include)
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


WRAPPER = Path(__file__).with_name("c2wrap.c")


def wrapper() -> Path:
    """c2wrap.exe, compiled from c2wrap.c with the checkout's cl when missing or older than its source."""
    exe = WRAPPER.with_suffix(".exe")
    if not exe.exists() or exe.stat().st_mtime < WRAPPER.stat().st_mtime:
        environment = {name: ";".join(winepath(d) for d in found) for name, found in system_directories().items()}
        command = ["wine", str(wine.era_tool("cl.exe")), "/nologo", "/O2", f"/Fe{winepath(exe)}", winepath(WRAPPER)]
        with environ(environment):
            output, _ = wine.run(command, cwd=exe.parent, timeout=TIMEOUT, success=exe)
        if not exe.exists():
            raise RuntimeError(f"c2wrap.c did not compile:\n{output[-2000:]}")
    return exe


class environ:
    """Set environment variables for the Wine processes started inside the block."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values, self.saved = values, {}

    def __enter__(self) -> None:
        self.saved = {name: os.environ.get(name) for name in self.values}
        os.environ.update(self.values)

    def __exit__(self, *_) -> None:
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def capture(unit: Unit, out: Path, path: Path) -> dict:
    """Compile the source through c2wrap, keeping the code generator's input in out/il."""
    il = out / "il"
    il.mkdir(parents=True, exist_ok=True)
    sibling = unit.source.with_name(f".{unit.source.stem}.concord{os.getpid()}-capture{unit.source.suffix}")
    sibling.write_bytes(path.read_bytes())
    raw = out / "capture.raw.obj"
    command = ["wine", str(wine.era_tool("cl.exe")), *cl.repo_include_flags(), *unit.flags, f"/B2{winepath(wrapper())}",
               f"/Fo{winepath(raw)}", winepath(sibling)]  # fmt: skip
    try:
        with environ({"C2CAPTURE": winepath(il), "C2REAL": winepath(wine.era_tool("c2.exe"))}):
            output, _ = wine.run(command, cwd=out, timeout=TIMEOUT, success=raw)
    finally:
        sibling.unlink(missing_ok=True)
    if not raw.exists() or not (il / "flags.txt").exists():
        return {"error": output.replace(sibling.name, unit.source.name)[-4000:] or "cl wrote no object"}
    candidate, rows = scored(unit, raw, out, "capture")
    if isinstance(rows, str):
        return {"error": rows}
    return {"il": str(il), "object": str(candidate), "functions": rows}


def replay(unit: Unit, out: Path, il: Path) -> dict:
    """Run c2.exe alone on a captured, possibly edited, directory of intermediate-language files."""
    prefix = next(il.glob("*ex")).name[:-2]
    raw = out / "replay.raw.obj"
    raw.unlink(missing_ok=True)
    flags = (il / "flags.txt").read_text()
    flags = re.sub(r"-il \S+", lambda _: f"-il {winepath(il)}\\{prefix}", flags)
    flags = re.sub(r"-Fo\S+", lambda _: f"-Fo{winepath(raw)}", flags)
    with environ({"MSC_CMD_FLAGS": flags}):
        output, _ = wine.run(["wine", str(wine.era_tool("c2.exe"))], cwd=out, timeout=TIMEOUT, success=raw)
    if not raw.exists():
        return {"error": output[-4000:] or "c2 wrote no object"}
    data = raw.read_bytes()
    if coff_defect(data):
        return {"error": f"c2 wrote an incomplete object: {coff_defect(data)}"}
    raw.write_bytes(stabilise(data))
    candidate, rows = scored(unit, raw, out, "replay")
    if isinstance(rows, str):
        return {"error": rows}
    return {"object": str(candidate), "functions": rows}


# c2.exe 11.00.7303 (VS97 SP3): where its code generator resets the round-robin register cursor at the start
# of a function's code, the global holding that function's record, and the routine recording that a register
# now holds a temporary (fastcall: ecx the register, edx the temporary's record).
REGTRACE_RESET, REGTRACE_FUNCTION, REGTRACE_GRANT = 0x42A90E, 0x495F1C, 0x435F38
REGTRACE_STOPS = 20000


def regtrace(unit: Unit, out: Path, path: Path) -> dict:
    """Capture the source's code-generator input, then run c2.exe on it under winedbg, logging every
    function start and every register grant."""
    captured = capture(unit, out, path)
    if "error" in captured:
        return captured
    il = Path(captured["il"])
    c2 = out / "c2.exe"
    shutil.copy(wine.era_tool("c2.exe"), c2)
    commands = out / "regtrace.cmd"
    commands.write_text("\n".join([
        f"break *{REGTRACE_RESET:#x}", f"break *{REGTRACE_GRANT:#x}",
        *(["cont", f"x/1x {REGTRACE_FUNCTION:#x}", "info regs", "x/1x $esp", "x/12x $edx"] * REGTRACE_STOPS),
        "quit", ""]))  # fmt: skip
    prefix = next(il.glob("*ex")).name[:-2]
    flags = (il / "flags.txt").read_text()
    flags = re.sub(r"-il \S+", lambda _: f"-il {winepath(il)}\\{prefix}", flags)
    flags = re.sub(r"-Fo\S+", lambda _: f"-Fo{winepath(out / 'regtrace.obj')}", flags)
    log = out / "regtrace.log"
    with environ({"MSC_CMD_FLAGS": flags, "WINEDEBUG": "-all"}), log.open("w") as stream:
        subprocess.run(["winedbg", "--file", str(commands), str(c2)], cwd=out, stdout=stream, stderr=subprocess.STDOUT,
                       timeout=TIMEOUT * 4, check=False)  # fmt: skip
    text = log.read_text(errors="replace")
    return {**captured, "log": text[: text.find("has terminated") + 200] if "has terminated" in text else text}


def peek(unit: Unit, out: Path, il: Path, stop: int, dumps: list[list[int]]) -> dict:
    """Run c2.exe on a capture under winedbg, stop where its code generator starts the `stop`th function
    (counting from 1), and dump each [address, words] range of memory there; reply with winedbg's log.
    With no ranges, print the name of each of the first `stop` functions in the order code generation starts
    them, which differs from the object's order and can start one function twice."""
    c2 = out / "c2.exe"
    shutil.copy(wine.era_tool("c2.exe"), c2)
    commands = out / "peek.cmd"
    if dumps:
        steps = [*(["cont"] * stop), f"x/1x {REGTRACE_FUNCTION:#x}", *(f"x/{words}x {address:#x}" for address, words in dumps)]
    else:  # the name of every function in code-generation order, at +0x18 in c2's function record
        steps = ["cont", f"x/s *(int*)(*(int*)({REGTRACE_FUNCTION:#x}) + 0x18)"] * stop
    commands.write_text("\n".join([f"break *{REGTRACE_RESET:#x}", *steps, "quit", ""]))
    prefix = next(il.glob("*ex")).name[:-2]
    flags = (il / "flags.txt").read_text()
    flags = re.sub(r"-il \S+", lambda _: f"-il {winepath(il)}\\{prefix}", flags)
    flags = re.sub(r"-Fo\S+", lambda _: f"-Fo{winepath(out / 'peek.obj')}", flags)
    log = out / "peek.log"
    with environ({"MSC_CMD_FLAGS": flags, "WINEDEBUG": "-all"}), log.open("w") as stream:
        subprocess.run(["winedbg", "--file", str(commands), str(c2)], cwd=out, stdout=stream, stderr=subprocess.STDOUT,
                       timeout=TIMEOUT * 4, check=False)  # fmt: skip
    text = log.read_text(errors="replace")
    return {"log": text if not dumps else text[text.rfind("Stopped on breakpoint"):]}


def regforce(unit: Unit, out: Path, il: Path, force: dict[int, int]) -> dict:
    """Run c2.exe on a capture with chosen register grants overwritten, and score the object."""
    c2 = out / "c2.exe"
    shutil.copy(wine.era_tool("c2.exe"), c2)
    steps = [f"break *{REGTRACE_GRANT:#x}"]
    for n in range(1, max(force) + 1):
        steps.append("cont")
        if n in force:
            steps.append(f"set $ecx = {force[n]}")
    commands = out / "regforce.cmd"
    commands.write_text("\n".join([*steps, *(["cont"] * REGTRACE_STOPS), "quit", ""]))
    prefix = next(il.glob("*ex")).name[:-2]
    raw = out / "regforce.raw.obj"
    flags = (il / "flags.txt").read_text()
    flags = re.sub(r"-il \S+", lambda _: f"-il {winepath(il)}\\{prefix}", flags)
    flags = re.sub(r"-Fo\S+", lambda _: f"-Fo{winepath(raw)}", flags)
    with environ({"MSC_CMD_FLAGS": flags, "WINEDEBUG": "-all"}):
        subprocess.run(["winedbg", "--file", str(commands), str(c2)], cwd=out, capture_output=True, timeout=TIMEOUT * 4, check=False)
    if not raw.exists() or coff_defect(raw.read_bytes()):
        return {"error": "c2 wrote no complete object with those registers"}
    raw.write_bytes(stabilise(raw.read_bytes()))
    candidate, rows = scored(unit, raw, out, "regforce")
    return {"error": rows} if isinstance(rows, str) else {"object": str(candidate), "functions": rows}


SCALARS = ("BOOL", "CHAR_U", "UCHAR", "USHORT", "UINT", "ULONG", "ULONGLONG", "CHAR_S", "SCHAR", "WCHAR", "SHORT", "INT", "LONG",
           "LONGLONG", "FLOAT", "DOUBLE", "POINTER", "ENUM")  # fmt: skip


NARROW = ("BOOL", "CHAR_U", "UCHAR", "USHORT", "CHAR_S", "SCHAR", "WCHAR", "SHORT")


def expression_types(root: Path, unit: Unit, path: Path, start: int, end: int) -> dict[str, tuple[str, bool]]:
    """The type of each expression text between two byte offsets of the source,
    and whether it is an integer narrower than int, parsed by libclang with the
    flags the checkout's own syntax-tree tools use. A text that has two types
    there, or a type that cannot be written before a name, is left out."""
    import clang.cindex as ci
    from gruntz.permute.generate_ast_variants import clang_args, configure_libclang

    configure_libclang()
    text = path.read_bytes()
    main = str(unit.source)
    parsed = ci.Index.create().parse(main, args=clang_args(root, unit.source), unsaved_files=[(main, text.decode())])
    scalars = {getattr(ci.TypeKind, name) for name in SCALARS}
    narrow = {getattr(ci.TypeKind, name) for name in NARROW}
    found: dict[str, set[tuple[str, bool]]] = {}
    for top in parsed.cursor.get_children():
        extent = top.extent
        if top.location.file is None or top.location.file.name != main or extent.end.offset <= start or extent.start.offset >= end:
            continue
        for cursor in top.walk_preorder():
            extent = cursor.extent
            # an implicit conversion is an unexposed expression with its operand's extent and another type
            if not cursor.kind.is_expression() or cursor.kind == ci.CursorKind.UNEXPOSED_EXPR:
                continue
            if extent.start.offset < start or extent.end.offset > end:
                continue
            kind = cursor.type.get_canonical().kind
            if kind in scalars:
                found.setdefault(text[extent.start.offset : extent.end.offset].decode(), set()).add((cursor.type.spelling, kind in narrow))
    return {expression: next(iter(types)) for expression, types in found.items() if len(types) == 1 and "(" not in next(iter(types))[0]}


def layouts(root: Path, unit: Unit) -> dict[str, dict]:
    """Every complete record the unit's source defines, by name: its size and where its pointer fields are,
    with the type each points to (a record's name, or "*" before a pointer's target), base classes, embedded
    records and arrays laid out in place, as libclang computes them for the target. A union's storage is
    described by its first member; a pointer to anything else (numbers, functions, void) is left out."""
    import clang.cindex as ci
    from gruntz.permute.generate_ast_variants import clang_args, configure_libclang

    configure_libclang()
    base_offset = ci.conf.lib.clang_getOffsetOfBase
    base_offset.argtypes, base_offset.restype = [ci.Cursor, ci.Cursor], ci.c_longlong
    parsed = ci.Index.create().parse(str(unit.source), args=clang_args(root, unit.source))
    definitions = {}
    for cursor in parsed.cursor.walk_preorder():
        if cursor.kind in (ci.CursorKind.CLASS_DECL, ci.CursorKind.STRUCT_DECL, ci.CursorKind.UNION_DECL) and cursor.is_definition():
            name = cursor.type.get_canonical().spelling
            if cursor.type.get_size() > 0 and not cursor.is_anonymous():
                definitions.setdefault(name, cursor)

    def target(kind, depth=0):
        kind = kind.get_canonical()
        if kind.kind == ci.TypeKind.RECORD:
            return kind.spelling if kind.spelling in definitions else None
        if kind.kind == ci.TypeKind.POINTER and depth < 3:
            inner = target(kind.get_pointee(), depth + 1)
            return "*" + inner if inner else None
        return None

    def place(kind, at, pointers):
        """Add the pointer fields of a value of this type stored at byte offset `at`."""
        kind = kind.get_canonical()
        if kind.kind in (ci.TypeKind.POINTER, ci.TypeKind.LVALUEREFERENCE):
            pointee = target(kind.get_pointee())
            if pointee:
                pointers[at] = pointee
        elif kind.kind == ci.TypeKind.CONSTANTARRAY:
            size = kind.element_type.get_size()
            for index in range(min(kind.element_count, 64)):
                place(kind.element_type, at + index * size, pointers)
        elif kind.kind == ci.TypeKind.RECORD and kind.spelling in definitions:
            members(definitions[kind.spelling], at, pointers)

    def members(cursor, at, pointers):
        record = cursor.type.get_canonical()
        for child in cursor.get_children():
            if child.kind == ci.CursorKind.CXX_BASE_SPECIFIER and child.type.get_canonical().spelling in definitions:
                members(definitions[child.type.get_canonical().spelling], at + base_offset(cursor, child) // 8, pointers)
        def fields(parent):
            for child in parent.get_children():
                if child.kind == ci.CursorKind.FIELD_DECL and child.spelling:
                    offset = record.get_offset(child.spelling)
                    if offset >= 0 and offset % 8 == 0 and at + offset // 8 not in pointers:
                        place(child.type, at + offset // 8, pointers)
                elif child.kind in (ci.CursorKind.UNION_DECL, ci.CursorKind.STRUCT_DECL) and child.is_anonymous():
                    fields(child)
        fields(cursor)

    found = {}
    for name, cursor in definitions.items():
        pointers: dict[int, str] = {}
        members(cursor, 0, pointers)
        found[name] = {"size": cursor.type.get_size(), "pointers": sorted(pointers.items())}
    return found


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
            if "capture" in request:
                print(json.dumps({"ok": capture(unit, out, Path(request["capture"]))}), flush=True)
                continue
            if "regtrace" in request:
                print(json.dumps({"ok": regtrace(unit, out, Path(request["regtrace"]))}), flush=True)
                continue
            if "peek" in request:
                print(json.dumps({"ok": peek(unit, out, Path(request["peek"]), int(request["stop"]), request["dumps"])}), flush=True)
                continue
            if "regforce" in request:
                force = {int(n): int(r) for n, r in request["force"].items()}
                print(json.dumps({"ok": regforce(unit, out, Path(request["regforce"]), force)}), flush=True)
                continue
            if "replay" in request:
                print(json.dumps({"ok": replay(unit, out, Path(request["replay"]))}), flush=True)
                continue
            if "layouts" in request:
                print(json.dumps({"ok": layouts(root, unit)}), flush=True)
                continue
            if "types" in request:
                print(json.dumps({"ok": expression_types(root, unit, Path(request["types"]), *request["range"])}), flush=True)
                continue
            jobs = max(1, int(request.get("jobs", 1)))
            if jobs != workers:
                if pool:
                    pool.shutdown()
                pool, workers = ProcessPoolExecutor(jobs, multiprocessing.get_context("fork")), jobs
            include = Path(request["include"]) if request.get("include") else None
            futures = {n: pool.submit(evaluate, unit, out, n, Path(p), include) for n, p in request["candidates"].items()}
            using = [name for name, used in (("objdiff_scores", unit.reader), ("wibo", unit.wibo)) if used]
            reply = {"ok": {n: future.result() for n, future in futures.items()}, "using": using}
        except Exception:  # noqa: BLE001 (the client reports it and decides)
            reply = {"error": traceback.format_exc()}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    serve()
