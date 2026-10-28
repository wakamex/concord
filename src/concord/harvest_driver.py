"""Compile candidate sources in a Harvest checkout and compare each with the target.

This file runs with a Harvest checkout's own Python (`uv run` in that checkout),
because it calls Harvest's compiler and matcher. With `--serve` it stays up and
answers one request per line of stdin with one line of stdout, {"ok": result} or
{"error": traceback}, so the target is loaded once for a whole search; it loads
the checkout's symbols, units and extents again whenever a file under
config/<build>/ changes. Without `--serve` it reads one request from stdin and
writes the result.

Unit candidates, replacing one unit's source:

    {"unit": "ox/net/CVariablePacket.cpp", "out": "/abs/dir",
     "candidates": {"name": "/abs/dir/name.cpp", ...}}

Overlay items, compiling a unit with any repository files replaced (a header
shared by many units, for instance):

    {"out": "/abs/dir",
     "items": {"name": {"unit": "ox/gui/X.cpp", "overlays": {"src/ox/gui/Y.h": "/abs/dir/y.h"}}, ...}}

With "debug": true, the items compile with -g added and are not compared: each
name maps to {"object": path} or {"error": ...}. Debug line tables locate a
normal compile only when their executable code corresponds.

Either way every compile runs in the pinned toolchain at the unit's own path, in
one container the driver keeps and in parallel lanes, and stdout gets one JSON object mapping each name to
{"object": path, "exact": bool, "sections": [...], "fuzzy": {symbol: percent}} or
{"error": compiler output}, where "fuzzy" is objdiff's score for each function.
Candidate files must be inside `out`; nothing under the checkout's build/match is
touched.
"""

import copy
import json
import multiprocessing
import os
import re
import shlex
import subprocess
import sys
import time
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from hv import (
    builds,
    delink,
    extents,
    match,
    metrics,
    objdiff,
    symbols,
    toolchain,
    units,
)
from hv.elf import Elf

_shared = None  # (target, known by name, placements by unit, known symbols, extents by address, project), for forked workers


def _jobs() -> int:
    """Most compiles or comparisons to run at once: CONCORD_JOBS, or 8, and never
    more than the machine's processors. Each comparison worker is a fork holding
    the loaded target (about 0.75 GB on Harvest), and each compile lane copies the
    sources into memory, so one worker per processor exhausted a 32 GB host."""
    return max(1, min(int(os.environ.get("CONCORD_JOBS", "8")), os.cpu_count() or 1))


def _compare(job: tuple[str, str, str]) -> dict:
    """Compare one object with the target and, for scoring, write the target's
    side of it: the unit delinked from the target as this object lays it out.
    Delinking is the slow step (about a quarter second per object on Harvest), so
    it runs here, in the workers."""
    name, obj, unit = job
    target, known, placements, known_list, extents_by_address, project = _shared
    elf = Elf.load(Path(obj), "ET_REL")
    report = match.compare_object(elf, target, known, placements[unit])
    out = {"object": obj, "exact": report["exact"], "sections": report["sections"]}
    if project is not None:
        try:
            sections, symbols_ = delink.delink_unit(target, elf, out, known_list, extents_by_address)
        except ValueError as error:
            # Ordinary searches reject candidates that cannot be delinked. An audit
            # may explicitly score them against the freshly delinked baseline target.
            out["score_error"] = str(error)
            return out
        destination = project / f"{name}.target.o"
        delink.write_object(destination, sections, symbols_)
        out["target"] = str(destination)
        out["score_basis"] = "candidate-target"
    return out


def fuzzy_scores(project: Path, reports: dict[str, dict]) -> dict[str, dict[str, float]]:
    """objdiff's fuzzy score of every function in each compared object, measured the
    way `hv.progress capture` measures it (the pinned objdiff, functionRelocDiffs=
    data_value), which is the score decomp.dev reports. Each report names the
    delinked target object _compare wrote for it."""
    tool = objdiff.tool()
    if builds.sha256_file(tool) not in metrics.OBJDIFF_HASHES:
        raise ValueError("objdiff executable differs from the pinned release")
    entries = [
        {"name": name, "target_path": report["target"], "base_path": report["object"]}
        for name, report in reports.items()
        if "target" in report
    ]
    (project / "objdiff.json").write_text(json.dumps({"build_target": False, "build_base": False, "units": entries}))
    output = project / "report.json"
    subprocess.run(
        [str(tool), "report", "generate", "-p", str(project), "-c", "functionRelocDiffs=data_value", "-o", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        unit["name"]: {f["name"]: f.get("fuzzy_match_percent", 0.0) for f in unit.get("functions", [])}
        for unit in json.loads(output.read_text())["units"]
    }


class Container:
    """One toolchain container kept for the life of the driver. It mounts the
    checkout read-only at /repo and concord's work root at /out, and reads its
    standard input, so it exits when the driver does. Each batch runs in it with
    `docker exec`, which spares starting a container per request."""

    def __init__(self, compiler: toolchain.Compiler) -> None:
        self.name = f"concord-{uuid.uuid4().hex[:12]}"
        self.process = subprocess.Popen(
            [
                "docker", "run", "--rm", "-i", "--name", self.name, "--network=none", "--platform", "linux/amd64",
                "--security-opt=label=disable",
                "-v", f"{builds.ROOT}:/repo:ro", "-v", f"{compiler.out}:/out", "--tmpfs", "/lanes:exec",
                "-w", "/lanes", compiler.image_id, "cat",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
        )  # fmt: skip
        deadline = time.monotonic() + 30
        while subprocess.run(["docker", "exec", self.name, "true"], capture_output=True, check=False).returncode:
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("the toolchain container did not start")
            time.sleep(0.05)

    def sh(self, script: str) -> None:
        toolchain.run("docker", "exec", self.name, "sh", script)


def compile_overlays(checkout: "Checkout", debug: bool, out: Path, items: dict[str, dict]) -> dict:
    """Compile every item in the kept container. Each lane copies the source roots
    once; each item first restores the files any earlier item of the lane
    overlaid, then copies its own overlays in, then compiles its unit. An item's
    "flags" are extra compiler options, for measuring, never for the build."""
    compiler = checkout.compiler(debug)
    prefix = out.resolve().relative_to(compiler.out).as_posix()  # work files sit under the mounted root
    roots = {"src"} | {
        Path(flag[2:]).parts[0] for flag in compiler.flags["flags"] if flag.startswith("-I") and not Path(flag[2:]).is_absolute()
    }
    touched = sorted({path for item in items.values() for path in item["overlays"]})
    lanes = max(1, min(len(items), _jobs()))
    work: list[list[str]] = [[] for _ in range(lanes)]
    for index, (name, item) in enumerate(items.items()):
        prepare = [
            f"mkdir -p {shlex.quote(str(Path(p).parent))} && "
            f"if test -f /repo/{shlex.quote(p)}; then cp /repo/{shlex.quote(p)} {shlex.quote(p)}; "
            f"else rm -f {shlex.quote(p)}; fi" for p in touched
        ]
        prepare += [
            f"cp {shlex.quote(compiler.container_path(Path(c)))} {shlex.quote(p)}" for p, c in item["overlays"].items()
        ]
        variant = checkout.compiler(debug, tuple(item.get("flags", ())))
        command = shlex.join(variant.compile_command(builds.ROOT / "src" / item["unit"], f"{prefix}/{name}"))
        work[index % lanes].append(
            f"if {' && '.join(prepare) or 'true'}; then {command} 2> /out/{prefix}/{name}.err; "
            f"echo $? > /out/{prefix}/{name}.status; else echo 125 > /out/{prefix}/{name}.status; fi"
        )
    batch = uuid.uuid4().hex
    lines = ["set -u"]
    for lane, steps in enumerate(work):
        copies = " && ".join(f"cp -a /repo/{shlex.quote(r)} {shlex.quote(r)}" for r in sorted(roots))
        lines.append(f"(mkdir -p /lanes/{batch}/{lane} && cd /lanes/{batch}/{lane} && {copies}")
        lines.extend(steps)
        lines.append(") &")
    lines += ["wait", f"rm -rf /lanes/{batch}"]
    script = out / f"overlay-{batch}.sh"
    script.write_text("\n".join(lines) + "\n")
    try:
        checkout.container().sh(compiler.container_path(script))
    finally:
        script.unlink()
    outcomes = {}
    for name in items:
        status = (out / f"{name}.status").read_text().strip()
        if status != "0":
            outcomes[name] = {"error": (out / f"{name}.err").read_text()[-2000:]}
        else:
            outcomes[name] = {"object": str(out / f"{name}.o")}
    return outcomes


def vtable_mismatches(target, objects: list[Path], known: dict[str, int]) -> list[dict]:
    """Compare every vtable the compiled objects define with the target's, word by word:
    offsets as numbers, slots by the symbol they point at. A slot pointing at an unnamed
    target function is unknown and not a mismatch; the first real disagreement is reported."""
    names = {address: name for name, address in known.items()}
    names |= {address: name for name, address in target.plt_symbols().items()}
    seen, found = set(), []
    for path in objects:
        obj = Elf.load(path, "ET_REL")
        for sym in obj.symtab():
            index = sym["st_shndx"]
            if not sym.name.startswith("_ZTV") or not isinstance(index, int) or sym.name in seen or sym.name not in known:
                continue
            seen.add(sym.name)
            slots = {r["r_offset"]: s.name or obj.sections[s["st_shndx"]].name for r, s in obj.relocations(index)}
            data = obj.sections[index].data()
            for slot in range(sym["st_size"] // 8):
                offset = sym["st_value"] + 8 * slot
                ours = slots.get(offset, int.from_bytes(data[offset : offset + 8], "little", signed=True))
                word = target.word(known[sym.name] + 8 * slot)
                word = word - (1 << 64) if word >= 1 << 63 else word
                theirs = names.get(word, word) if word > 0x400000 else word
                if ours != theirs and not (isinstance(ours, str) and isinstance(theirs, int)):
                    found.append({"vtable": sym.name, "slot": slot, "slots": sym["st_size"] // 8,
                                  "ours": ours, "target": theirs, "object": path.stem})
                    break
    return [{"compared": len(seen)}, *found]


class Checkout:
    """What every request needs from the checkout, loaded on first use and again
    when a file under config/<build>/ changes (hv match --learn adds symbols, and
    seeding adds units and placements)."""

    def __init__(self) -> None:
        self.stamp = None
        self._compilers: dict[tuple[bool, tuple], toolchain.Compiler] = {}
        self._container: Container | None = None

    def compiler(self, debug: bool = False, flags: tuple = ()) -> toolchain.Compiler:
        """The checkout's compiler over concord's work root, checked against the
        pinned image once. `debug` adds -g and `flags` appends options for
        measuring, such as --param name=value; only option flags that cannot
        redirect inputs or outputs are allowed."""
        if (False, ()) not in self._compilers:
            root = builds.ROOT / "build" / "concord"
            self._compilers[False, ()] = toolchain.Compiler(toolchain.load_flags(builds.canonical_build()), root)
        key = debug, flags
        if key not in self._compilers:
            for flag in flags:
                if not re.fullmatch(r"(-f|-m|-O|-W|--?param)[^\s]*|-g[0-3]?|-U_FORTIFY_SOURCE|[a-zA-Z0-9_-]+=[0-9]+", flag):
                    raise ValueError(f"unsupported extra compiler option: {flag}")
            plain = self._compilers[False, ()]
            variant = copy.copy(plain)
            variant.flags = {**plain.flags, "flags": [*plain.flags["flags"], *(["-g"] if debug else []), *flags]}
            self._compilers[key] = variant
        return self._compilers[key]

    def container(self) -> Container:
        if self._container is None or self._container.process.poll() is not None:
            self._container = Container(self.compiler())
        return self._container

    def refresh(self) -> None:
        build = builds.canonical_build()
        config = builds.ROOT / "config" / build
        stamp = tuple(sorted((str(path), path.stat().st_mtime_ns) for path in config.iterdir() if path.is_file()))
        if stamp == self.stamp:
            return
        self.build = build
        self.by_source = {u.source: u for u in units.load(build)}
        self.target = extents.load_target(build)
        loaded = symbols.load(build)
        self.known = symbols.by_name(loaded)
        self.known_list = [(s.address, s.size, s.name) for s in loaded]
        self.extents_by_address = {address: size for address, (size, _) in self.target.function_extents().items()}
        self.stamp = stamp


def handle(request: dict, checkout: Checkout) -> object:
    global _shared
    checkout.refresh()
    build, target = checkout.build, checkout.target
    known, by_source = dict(checkout.known), dict(checkout.by_source)
    known_list = list(checkout.known_list)
    for source, placements_ in request.get("units", {}).items():
        by_source[source] = units.Unit(source, placements_)
    for claim in request.get("claims", []):
        address, size, name = claim["address"], claim["size"], claim["symbol"]
        if checkout.extents_by_address.get(address) != size:
            raise ValueError(f"{name}: claim is not a target function extent")
        if name in known and known[name] != address:
            raise ValueError(f"{name}: conflicting address claim")
        known[name] = address
        known_list.append((address, size, name))
    if request.get("vtables"):
        objects = sorted((builds.ROOT / "build" / "match" / build).glob("*.o"))
        return vtable_mismatches(target, objects, known)
    out = Path(request["out"])
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict] = {}
    jobs: dict[str, tuple[str, str]] = {}
    if "items" in request:
        items = request["items"]
    else:  # candidates of one unit: the unit with its own source replaced
        unit = by_source[request["unit"]]
        source = unit.path.resolve().relative_to(builds.ROOT).as_posix()
        items = {name: {"unit": unit.source, "overlays": {source: path}} for name, path in request["candidates"].items()}
    for name, outcome in compile_overlays(checkout, bool(request.get("debug")), out, items).items():
        if "error" in outcome or request.get("debug"):
            result[name] = outcome
        else:
            jobs[name] = (outcome["object"], items[name]["unit"])
    scoring = bool(jobs) and not request.get("debug")
    project = out / f"fuzzy-{uuid.uuid4().hex}" if scoring else None
    if project is not None:
        project.mkdir(parents=True)
    placements = {source: u.placements for source, u in by_source.items()}
    _shared = (target, known, placements, known_list, checkout.extents_by_address, project)
    # candidates that compiled to the same bytes share one comparison: many rewrites change nothing
    first: dict[tuple[str, str], str] = {}
    same: dict[str, str] = {}
    for name, (obj, unit) in jobs.items():
        same[name] = first.setdefault((builds.sha256_file(Path(obj)), unit), name)
    distinct = [(name, *jobs[name]) for name in jobs if same[name] == name]
    workers = min(len(distinct), _jobs())
    if workers > 1:
        with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as pool:
            compared = dict(zip([job[0] for job in distinct], pool.map(_compare, distinct), strict=True))
    else:
        compared = {job[0]: _compare(job) for job in distinct}
    if request.get("baseline_target_fallback"):
        baseline_targets = {}
        for name, (_, unit) in jobs.items():
            item = items[name]
            result_ = compared[same[name]]
            if not item["overlays"] and not item.get("flags") and "target" in result_:
                baseline_targets[unit] = result_["target"]
        for name, row in compared.items():
            unit = jobs[name][1]
            if "target" not in row and unit in baseline_targets:
                row["target"] = baseline_targets[unit]
                row["score_basis"] = "baseline-target"
    scores = fuzzy_scores(project, compared) if scoring else {}
    for name, (obj, _) in jobs.items():
        result[name] = {key: value for key, value in compared[same[name]].items() if key != "target"} | {"object": obj}
        if scoring:
            result[name]["fuzzy"] = scores.get(same[name], {})
    return result


def main() -> None:
    replies, sys.stdout = sys.stdout, sys.stderr  # nothing but replies goes to the caller
    checkout = Checkout()
    if "--serve" not in sys.argv:
        json.dump(handle(json.load(sys.stdin), checkout), replies)
        return
    for line in sys.stdin:
        try:
            reply = {"ok": handle(json.loads(line), checkout)}
        except Exception:  # noqa: BLE001 (the caller reports it; the next request still gets served)
            reply = {"error": traceback.format_exc()[-3000:]}
        replies.write(json.dumps(reply) + "\n")
        replies.flush()


if __name__ == "__main__":
    main()
