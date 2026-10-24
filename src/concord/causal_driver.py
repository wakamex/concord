"""Collect dumps using Harvest's verified compiler; run under Harvest's Python."""

import json
import shlex
import subprocess
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
    command[command.index('-c')] = '-S'
    command[command.index('-o') + 1] = '/out/trace.s'
    command += ["-g", "-dp", "-fdump-tree-all", "-fdump-rtl-all", "-fdump-ipa-inline",
                "-dumpbase", "/out/trace"]
    assembler = [compiler.flags['compiler'], '-c', '/out/trace.s', '-o', '/out/trace.o', '-Wa,-adln']
    # Listings can quote non-UTF-8 source bytes. Keep the original bytes rather
    # than routing them through toolchain.command's strict text decoder.
    try:
        compiler.command('sh', '-c', shlex.join(command) + ' 2> /out/compiler.stderr')
        compiler.command('sh', '-c', shlex.join(assembler) + ' > /out/listing.txt 2> /out/assembler.stderr')
    except subprocess.CalledProcessError:
        errors = '\n'.join(p.read_text(errors='replace') for p in (out / 'compiler.stderr', out / 'assembler.stderr') if p.exists())
        raise RuntimeError(errors[-4000:]) from None
    metadata = compiler.metadata(source, "trace", command, None)
    metadata['assembler_command'] = assembler
    metadata['artifacts'] = {p.name: builds.sha256_file(p) for p in sorted(out.iterdir()) if p.is_file()}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return {"object": str(out / "trace.o"), "listing": str(out / "listing.txt"), "metadata": metadata}


if __name__ == "__main__":
    print(json.dumps(collect(*sys.argv[1:])))
