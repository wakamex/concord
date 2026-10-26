"""Collect actual scratch searches; run with Harvest's pinned compiler API."""

import base64
import hashlib
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from hv import builds, extents, match, symbols, toolchain, units
from hv.elf import Elf

# This profile is for the compiler package pinned by Harvest's manifest. The
# dynamic symbols and machine-code digest guard every private layout offset.
COMPILER = '/usr/lib/gcc/x86_64-linux-gnu/4.4.3/cc1plus'
DIGEST = 'cbd886f07b000b49ffd7d3f41ef42f27c8c286e6b597d79b21a35010b84a584c'
LIBRARIES = ['/usr/lib/libmpfr.so.1', '/usr/lib/libgmp.so.3', '/lib/libdl.so.2',
             '/lib/libc.so.6', '/lib64/ld-linux-x86-64.so.2']
PROFILE = {'entry': 0x6eb690, 'return': 0x6eb94c, 'state': 0x103a180,
           'current': 0x103a1f0, 'window': 0x103a1a0, 'cfun': 0x10358d0,
           'reg_names': 0, 'order': 0xff1780, 'register_count': 53}


def collect(unit, destination):
    root, out = builds.ROOT.resolve(), Path(destination).resolve()
    if not out.is_relative_to(root / 'build' / 'concord'):
        raise ValueError('scratch output must be under build/concord')
    source = (root / 'src' / unit).resolve()
    if not source.is_relative_to(root / 'src'):
        raise ValueError('unit must be under src')
    if shutil.which('gdb') is None:
        raise ValueError('GNU Debugger is required for failed-search observation')
    compiler = toolchain.Compiler(toolchain.load_flags(builds.canonical_build()), out)
    canonical, metadata = compiler.compile(source, 'canonical')
    runtime = out / 'runtime'
    runtime.mkdir()
    digests = {}
    for name in [COMPILER, *LIBRARIES]:
        data = base64.b64decode(compiler.command('base64', name))
        path = runtime / Path(name).name
        path.write_bytes(data)
        path.chmod(0o755)
        digests[name] = hashlib.sha256(data).hexdigest()
    if digests[COMPILER] != DIGEST:
        raise ValueError('unsupported compiler binary: private layouts are not verified')
    exports = compiler.command('nm', '-D', COMPILER)
    reg_names = [int(line.split()[0], 16) for line in exports.splitlines() if line.split()[-1:] == ['reg_names']]
    if len(reg_names) != 1:
        raise ValueError('compiler register names are unavailable')
    profile = {**PROFILE, 'reg_names': reg_names[0]}
    driver = compiler.compile_command(source, 'canonical') + ['-###']
    specification = compiler.command('sh', '-c', shlex.join(driver) + ' 2>&1')
    (out / 'driver-specification.txt').write_text(specification)
    frontends = [shlex.split(line) for line in specification.splitlines()
                 if line.lstrip().startswith('"' + COMPILER + '"')]
    if len(frontends) != 1:
        raise ValueError('driver does not identify one pinned C++ frontend')
    frontend, index = [], 1
    while index < len(frontends[0]):
        flag = frontends[0][index]
        if flag in ('-MD', '-MMD', '-MF', '-MT', '-MQ'):
            index += 2
            continue
        if flag == compiler.container_path(source):
            flag = str(out / 'input.ii')
        elif index and frontends[0][index - 1] == '-o':
            flag = str(out / 'trace.s')
        frontend.append(flag)
        index += 1
    preprocess = compiler.compile_command(source, 'preprocessed')
    preprocess[preprocess.index('-c')] = '-E'
    preprocess[preprocess.index('-o') + 1] = '/out/input.ii'
    compiler.command(*preprocess)
    # The interpreter path changes in a copy only; executable code and compiler
    # data remain byte-identical. This avoids loading the host libc in cc1plus.
    with tempfile.TemporaryDirectory(prefix='cg', dir='/tmp') as tmp:
        loader = Path(tmp) / 'ld'
        loader.symlink_to(runtime / 'ld-linux-x86-64.so.2')
        executable = runtime / 'cc1plus-traced'
        executable.write_bytes(_interpreter(runtime / 'cc1plus', str(loader)))
        executable.chmod(0o755)
        config = out / 'debugger.json'
        config.write_text(json.dumps({'profile': profile, 'events': str(out / 'events.json')}))
        script = out / 'trace.gdb'
        script.write_text('set pagination off\nset startup-with-shell off\nset debuginfod enabled off\n'
                          + 'set environment LD_LIBRARY_PATH ' + str(runtime) + '\nsource '
                          + str(Path(__file__).with_name('scratch_gdb.py')) + '\nrun\n')
        command = ['gdb', '-nx', '-q', '--batch', '-iex', 'set debuginfod enabled off', '-x', str(script),
                   '--args', str(executable), *frontend, '-fpreprocessed',
                   '-fdump-rtl-all', '-dumpbase', str(out / 'trace')]
        with (out / 'debugger.log').open('wb') as log:
            subprocess.run(command, cwd=root, env={**os.environ, 'CONCORD_SCRATCH_CONFIG': str(config)},
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    trace = json.loads((out / 'events.json').read_text())
    if trace['errors'] or trace['exit_code'] != 0:
        raise ValueError('incomplete debugger trace: ' + repr(trace['errors']))
    compiler.command('as', '--64', '/out/trace.s', '-o', '/out/trace.o')
    if (out / 'trace.o').read_bytes() != canonical.read_bytes():
        raise ValueError('diagnostic execution changed the canonical object')
    target = extents.load_target(builds.canonical_build())
    selected = [u for u in units.load(builds.canonical_build()) if u.source == unit]
    if len(selected) != 1:
        raise ValueError('unit is not uniquely registered')
    comparison = match.compare_object(Elf.load(canonical, 'ET_REL'), target,
                                      symbols.by_name(symbols.load(builds.canonical_build())), selected[0].placements)
    metadata['matched_functions'] = [r['symbol'] for section in comparison['sections']
                                     for r in section.get('functions', []) if r['exact']]
    metadata['debugger_script_sha256'] = builds.sha256_file(Path(__file__).with_name('scratch_gdb.py'))
    metadata.update(runtime_sha256=digests, profile=profile, debugger_command=command,
                    preprocessor_command=preprocess, frontend_command=frontends[0], diagnostic_object_verified=True,
                    events_sha256=builds.sha256_file(out / 'events.json'))
    metadata['artifacts'] = {p.name: builds.sha256_file(p) for p in out.iterdir() if p.is_file()}
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return {'directory': str(out), 'events': len(trace['events']), 'metadata': metadata}


def _interpreter(path, replacement):
    data = path.read_bytes()
    offset, = struct.unpack_from('<Q', data, 32)
    entry_size, count = struct.unpack_from('<HH', data, 54)
    for index in range(count):
        kind, _, start, _, _, size, _, _ = struct.unpack_from('<IIQQQQQQ', data, offset + index * entry_size)
        if kind == 3:
            name = replacement.encode() + b'\0'
            if len(name) > size:
                raise ValueError('replacement loader path is too long')
            return data[:start] + name.ljust(size, b'\0') + data[start + size:]
    raise ValueError('compiler lacks an interpreter segment')


if __name__ == '__main__':
    print(json.dumps(collect(*sys.argv[1:])))
