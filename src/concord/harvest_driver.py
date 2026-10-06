"""Compile candidate sources of one Harvest unit and compare each with the target.

This file runs with a Harvest checkout's own Python (`uv run` in that checkout),
because it calls Harvest's compiler and matcher. It reads a request from stdin:

    {"unit": "ox/net/CVariablePacket.cpp", "out": "/abs/dir",
     "candidates": {"name": "/abs/dir/name.cpp", ...}}

compiles every candidate in the pinned toolchain at the unit's own path, in
parallel lanes, and writes one JSON object to stdout mapping each name to
{"object": path, "exact": bool, "sections": [...]} or {"error": compiler output}.
Candidate files must be inside `out`; nothing under the checkout's build/match is
touched.
"""

import json
import subprocess
import sys
from pathlib import Path

from hv import builds, match, symbols, toolchain, units
from hv.elf import Elf


def main() -> None:
    request = json.load(sys.stdin)
    build = builds.canonical_build()
    unit = next(u for u in units.load(build) if u.source == request["unit"])
    (image,) = builds.load_builds()[build].images.values()
    target = Elf.load(image.path, "ET_EXEC")
    known = symbols.by_name(symbols.load(build))
    out = Path(request["out"])
    out.mkdir(parents=True, exist_ok=True)
    compiler = toolchain.Compiler(toolchain.load_flags(build), out)
    items = [(name, Path(path)) for name, path in request["candidates"].items()]
    result = {}
    for (name, _), outcome in zip(items, compiler.compile_many(unit.path, items), strict=True):
        if isinstance(outcome, subprocess.CalledProcessError):
            result[name] = {"error": (outcome.stderr or str(outcome))[-2000:]}
            continue
        obj, _ = outcome
        report = match.compare_object(Elf.load(obj, "ET_REL"), target, known, unit.placements)
        result[name] = {"object": str(obj), "exact": report["exact"], "sections": report["sections"]}
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
