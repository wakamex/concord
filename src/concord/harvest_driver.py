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

Either way every compile runs in the pinned toolchain at the unit's own path, in
parallel lanes, and stdout gets one JSON object mapping each name to
{"object": path, "exact": bool, "sections": [...]} or {"error": compiler output}.
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

from hv import builds, match, symbols, toolchain, units
from hv.elf import Elf

_shared = None  # (target, known, placements by unit), inherited by forked workers


def _compare(job: tuple[str, str]) -> dict:
    obj, unit = job
    target, known, placements = _shared
    report = match.compare_object(Elf.load(Path(obj), "ET_REL"), target, known, placements[unit])
    return {"object": obj, "exact": report["exact"], "sections": report["sections"]}


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


def main() -> None:
    global _shared
    request = json.load(sys.stdin)
    build = builds.canonical_build()
    by_source = {u.source: u for u in units.load(build)}
    (image,) = builds.load_builds()[build].images.values()
    target = Elf.load(image.path, "ET_EXEC")
    known = symbols.by_name(symbols.load(build))
    out = Path(request["out"])
    out.mkdir(parents=True, exist_ok=True)
    compiler = toolchain.Compiler(toolchain.load_flags(build), out)
    result: dict[str, dict] = {}
    jobs: dict[str, tuple[str, str]] = {}
    if "items" in request:
        items = request["items"]
        paths = {item["unit"]: by_source[item["unit"]].path for item in items.values()}
        for name, outcome in compile_overlays(compiler, items, paths).items():
            if "error" in outcome:
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
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
