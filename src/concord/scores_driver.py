"""Capture with Harvest's own progress command and concord's worker limit."""

import os
import sys

from hv import progress, toolchain

compile_each = toolchain.Compiler.compile_each


def bounded(self, items, jobs=None):
    return compile_each(self, items, jobs=jobs or max(1, int(os.environ.get('CONCORD_JOBS', '8'))))


toolchain.Compiler.compile_each = bounded
raise SystemExit(progress.main(sys.argv[1:]))
