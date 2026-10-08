"""Compile candidate sources in a Harvest checkout and compare each with the target.

This file runs with a Harvest checkout's own Python (`uv run` in that checkout),
because it calls Harvest's compiler and matcher. It reads one request from stdin.

Unit candidates, replacing one unit's source:

    {"unit": "ox/net/CVariablePacket.cpp", "out": "/abs/dir",
     "candidates": {"name": "/abs/dir/name.cpp", ...}}

Overlay items, compiling a unit with any repository files replaced (a header
shared by many units, for instance):

    {"out": "/abs/dir",
     "items": {"name": {"unit": "ox/gui/X.cpp", "overlays": {"src/ox/gui/Y.h": "/abs/dir/y.h"}}, ...}}

With "debug": true, the items compile with -g added and are not compared: each
name maps to {"object": path} or {"error": ...}. GCC 4.4 emits the same code with
and without -g, so a debug object's line tables locate the code of a normal compile.

Either way every compile runs in the pinned toolchain at the unit's own path, in
parallel lanes, and stdout gets one JSON object mapping each name to
{"object": path, "exact": bool, "sections": [...], "fuzzy": {symbol: percent}} or
{"error": compiler output}, where "fuzzy" is objdiff's score for each function.
Candidate files must be inside `out`; nothing under the checkout's build/match is
touched.
"""

import json
import multiprocessing
import os
import shlex
import subprocess
import sys
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

_shared = None  # (target, known, placements by unit), inherited by forked workers


def _compare(job: tuple[str, str]) -> dict:
    obj, unit = job
    target, known, placements = _shared
    report = match.compare_object(Elf.load(Path(obj), "ET_REL"), target, known, placements[unit])
    return {"object": obj, "exact": report["exact"], "sections": report["sections"]}


def fuzzy_scores(target, known: list, reports: dict[str, dict], out: Path) -> dict[str, dict[str, float]]:
    """objdiff's fuzzy score of every function in each compared object, measured the
    way `hv.progress capture` measures it (the pinned objdiff, functionRelocDiffs=
    data_value), which is the score decomp.dev reports."""
    tool = objdiff.tool()
    if builds.sha256_file(tool) not in metrics.OBJDIFF_HASHES:
        raise ValueError("objdiff executable differs from the pinned release")
    extents_by_address = {address: size for address, (size, _) in target.function_extents().items()}
    project = out / f"fuzzy-{uuid.uuid4().hex}"
    project.mkdir(parents=True)
    entries = []
    for name, report in reports.items():
        obj = Path(report["object"])
        try:
            sections, symbols_ = delink.delink_unit(target, Elf.load(obj, "ET_REL"), report, known, extents_by_address)
        except ValueError:
            # a candidate whose sections the matcher placed outside the target cannot be delinked;
            # it gets no objdiff scores, which ranks it last and rejects it, rather than ending the batch
            continue
        destination = project / f"{name}.target.o"
        delink.write_object(destination, sections, symbols_)
        entries.append({"name": name, "target_path": str(destination), "base_path": str(obj)})
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


def compile_overlays(compiler: toolchain.Compiler, items: dict[str, dict], unit_paths: dict[str, Path]) -> dict:
    """Compile every item in one container. Each lane copies the source roots once;
    each item first restores the files any earlier item of the lane overlaid, then
    copies its own overlays in, then compiles its unit."""
    roots = {"src"} | {
        Path(flag[2:]).parts[0] for flag in compiler.flags["flags"] if flag.startswith("-I") and not Path(flag[2:]).is_absolute()
    }
    touched = sorted({path for item in items.values() for path in item["overlays"]})
    lanes = max(1, min(len(items), os.cpu_count() or 1))
    work: list[list[str]] = [[] for _ in range(lanes)]
    for index, (name, item) in enumerate(items.items()):
        prepare = [f"cp /repo/{shlex.quote(p)} {shlex.quote(p)}" for p in touched]
        prepare += [
            f"cp {shlex.quote(compiler.container_path(Path(c)))} {shlex.quote(p)}" for p, c in item["overlays"].items()
        ]
        command = shlex.join(compiler.compile_command(unit_paths[item["unit"]], name))
        work[index % lanes].append(
            f"if {' && '.join(prepare) or 'true'}; then {command} 2> /out/{name}.err; "
            f"echo $? > /out/{name}.status; else echo 125 > /out/{name}.status; fi"
        )
    batch = uuid.uuid4().hex
    lines = ["set -u"]
    for lane, steps in enumerate(work):
        copies = " && ".join(f"cp -a /repo/{shlex.quote(r)} {shlex.quote(r)}" for r in sorted(roots))
        lines.append(f"(mkdir -p /lanes/{batch}/{lane} && cd /lanes/{batch}/{lane} && {copies}")
        lines.extend(steps)
        lines.append(") &")
    lines += ["wait", f"rm -rf /lanes/{batch}"]
    script = compiler.out / f"overlay-{batch}.sh"
    script.write_text("\n".join(lines) + "\n")
    try:
        toolchain.run(
            "docker", "run", "--rm", "--network=none", "--platform", "linux/amd64",
            "--security-opt=label=disable",
            "-v", f"{builds.ROOT}:/repo:ro", "-v", f"{compiler.out}:/out", "--tmpfs", "/lanes:exec",
            "-w", "/lanes", compiler.image_id, "sh", compiler.container_path(script),
        )  # fmt: skip
    finally:
        script.unlink()
    outcomes = {}
    for name in items:
        status = (compiler.out / f"{name}.status").read_text().strip()
        if status != "0":
            outcomes[name] = {"error": (compiler.out / f"{name}.err").read_text()[-2000:]}
        else:
            outcomes[name] = {"object": str(compiler.out / f"{name}.o")}
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


def main() -> None:
    global _shared
    request = json.load(sys.stdin)
    if request.get("vtables"):
        build = builds.canonical_build()
        target = extents.load_target(build)
        known = symbols.by_name(symbols.load(build))
        objects = sorted((builds.ROOT / "build" / "match" / build).glob("*.o"))
        json.dump(vtable_mismatches(target, objects, known), sys.stdout)
        return
    build = builds.canonical_build()
    by_source = {u.source: u for u in units.load(build)}
    target = extents.load_target(build)
    known = symbols.by_name(symbols.load(build))
    out = Path(request["out"])
    out.mkdir(parents=True, exist_ok=True)
    compiler = toolchain.Compiler(toolchain.load_flags(build), out)
    if request.get("debug"):
        compiler.flags = {**compiler.flags, "flags": [*compiler.flags["flags"], "-g"]}
    result: dict[str, dict] = {}
    jobs: dict[str, tuple[str, str]] = {}
    if "items" in request:
        items = request["items"]
        paths = {item["unit"]: by_source[item["unit"]].path for item in items.values()}
        for name, outcome in compile_overlays(compiler, items, paths).items():
            if "error" in outcome or request.get("debug"):
                result[name] = outcome
            else:
                jobs[name] = (outcome["object"], items[name]["unit"])
    else:
        unit = by_source[request["unit"]]
        names = list(request["candidates"])
        pairs = [(name, Path(request["candidates"][name])) for name in names]
        for name, outcome in zip(names, compiler.compile_many(unit.path, pairs), strict=True):
            if isinstance(outcome, subprocess.CalledProcessError):
                result[name] = {"error": (outcome.stderr or str(outcome))[-2000:]}
            else:
                jobs[name] = (str(outcome[0]), unit.source)
    _shared = (target, known, {source: u.placements for source, u in by_source.items()})
    with ProcessPoolExecutor(os.cpu_count() or 1, mp_context=multiprocessing.get_context("fork")) as pool:
        for name, report in zip(jobs, pool.map(_compare, jobs.values())):
            result[name] = report
    if jobs and not request.get("debug"):
        known_list = [(s.address, s.size, s.name) for s in symbols.load(build)]
        scores = fuzzy_scores(target, known_list, {name: result[name] for name in jobs}, out)
        for name in jobs:
            result[name]["fuzzy"] = scores.get(name, {})
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
