"""Locating a finding's source through the line tables of a -g compile."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.diff import diff_code, read_function
from concord.lines import editable, inline_chains

HEADER = """inline int smaller(int a, int b)
{
    return a < b ? a : b;
}
"""
UNIT = """#include "smaller.h"
int f(int x, int y)
{
    return smaller(x, y) * 3;
}
"""


@unittest.skipUnless(shutil.which("g++") and shutil.which("addr2line"), "needs g++ and addr2line")
class InlineChains(unittest.TestCase):
    def test_inlined_code_maps_to_the_header_then_the_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "smaller.h").write_text(HEADER)
            (src / "f.cpp").write_text(UNIT)
            obj = Path(tmp) / "f.o"
            subprocess.run(["g++", "-O2", "-g", f"-fdebug-prefix-map={tmp}=/work", "-c", "src/f.cpp", "-o", str(obj)], cwd=tmp, check=True)
            function = read_function(obj, "_Z1fii")
            chain = inline_chains(obj, function.section, [function.start])[0]
            self.assertEqual([(f.path, f.line) for f in chain], [("src/smaller.h", 3), ("src/f.cpp", 4)])
            self.assertEqual(chain[0].function, "smaller(int, int)")
            self.assertEqual(editable(chain), chain[0])


@unittest.skipUnless(shutil.which("as"), "needs the GNU assembler")
class CandidateOffsets(unittest.TestCase):
    def test_a_finding_records_where_the_candidate_side_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            objs = []
            for name, body in (("t", "nop\ncmp edi, esi\njl 1f\nret\n1: ret"), ("c", "cmp esi, edi\njg 1f\nret\n1: ret")):
                (Path(tmp) / f"{name}.s").write_text(
                    f".intel_syntax noprefix\n.text\n.globl f\n.type f,@function\nf:\n{body}\n.size f,.-f\n"
                )
                subprocess.run(["as", "-o", f"{tmp}/{name}.o", f"{tmp}/{name}.s"], check=True)
                objs.append(read_function(Path(tmp) / f"{name}.o", "f"))
            finding = diff_code(*objs).findings[0]
            self.assertEqual((finding.offset, finding.candidate), (1, 0))


if __name__ == "__main__":
    unittest.main()
