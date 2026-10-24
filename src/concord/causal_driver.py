"""Collect dumps using Harvest's verified compiler; run under Harvest's Python."""

import json
import shlex
import sys
from pathlib import Path

from hv import builds, toolchain


def collect(unit: str, destination: str) -> dict:
    root = builds.ROOT.resolve()
    out = Path(destination).resolve()
    if not out.is_relative_to(root / "build" / "concord"):
        raise ValueError("dump output must be under build/concord")
    source = (root / "src" / unit).resolve()
    if not source.is_relative_to(root / "src"):
        raise ValueError("unit must be under src/")
    compiler = toolchain.Compiler(toolchain.load_flags(builds.canonical_build()), out)
    command = compiler.compile_command(source, "trace")
    command += ["-g", "-dp", "-fdump-tree-all", "-fdump-rtl-all", "-fdump-ipa-inline",
                "-dumpbase", "/out/trace", "-Wa,-adhln,--listing-rhs-width=4096"]
    # Listings can quote non-UTF-8 source bytes. Keep the original bytes rather
    # than routing them through toolchain.command's strict text decoder.
    compiler.command('sh', '-c', shlex.join(command) + ' > /out/listing.txt')
    metadata = compiler.metadata(source, "trace", command, None)
    metadata['artifacts'] = {p.name: builds.sha256_file(p) for p in sorted(out.iterdir()) if p.is_file()}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return {"object": str(out / "trace.o"), "listing": str(out / "listing.txt"), "metadata": metadata}


if __name__ == "__main__":
    print(json.dumps(collect(*sys.argv[1:])))
