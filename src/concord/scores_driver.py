"""Use Harvest's progress command and retain its verified data ranges."""

import os
import sys

from hv import metrics, progress, toolchain

compile_each = toolchain.Compiler.compile_each
export = progress.export


def bounded(self, items, jobs=None):
    return compile_each(self, items, jobs=jobs or max(1, int(os.environ.get('CONCORD_JOBS', '8'))))


def export_with_ranges(build, output):
    report = export(build, output)
    inventory, _, evidence = progress.validate(build)
    ranges = [[start, end - start] for spans in metrics.data_ranges(inventory, evidence).values()
              for start, end in spans]
    if sum(size for _, size in ranges) != int(report['measures']['matched_data']):
        raise ValueError('matched data ranges disagree with the progress report')
    report['concord_matched_data'] = sorted(ranges)
    progress.write_json(output, report)
    return report


toolchain.Compiler.compile_each = bounded
progress.export = export_with_ranges
raise SystemExit(progress.main(sys.argv[1:]))
