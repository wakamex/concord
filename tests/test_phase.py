"""Phase scans sort a unit's functions by how their exactness depends on the
number of declarations before them."""

import unittest

from concord.harvest import Evaluation
from concord.phase import Scan, pad, scan, spans


class FakeHarvest:
    """A unit of four functions: one exact at every phase, one only as the source
    stands, one only with two more declarations, one never."""

    def __init__(self, root):
        self.root = root
        self.sources = {}

    def source(self, unit):
        return unit

    def evaluate(self, unit, sources):
        self.sources = sources
        out = {}
        for name, text in sources.items():
            k = text.count(b"concordPhase")
            rows = [
                {"symbol": "always", "exact": True},
                {"symbol": f"T.{100 + k}", "address": "0x4982f0", "exact": True},
                {"symbol": "witness", "exact": k % 31 == 0},
                {"symbol": "locked", "exact": k % 31 == 2},
                {"symbol": "never", "exact": False},
            ]
            out[name] = Evaluation(name, unit, sections=[{"functions": rows}])
        return out


class PhaseTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.cpp").write_bytes(b'#include "a.h"\nint f() { return 1; }\n')

    def test_padding_keeps_line_numbers(self):
        padded = pad(b"int a;\nint b;\n", 3)
        self.assertEqual(padded.count(b"\n"), 2)
        self.assertEqual(padded.count(b"extern int concordPhase"), 3)
        self.assertTrue(padded.endswith(b"int a;\nint b;\n"))
        self.assertEqual(pad(b"int a;\n", 0), b"int a;\n")

    def test_scan_separates_locked_functions_from_witnesses(self):
        harvest = FakeHarvest(self.root)
        result = scan(harvest, "a.cpp", 31)
        self.assertEqual(len(harvest.sources), 31)
        self.assertEqual(result.locked, {"locked": {2}})
        self.assertEqual(result.witnesses, {"witness": {0}})
        self.assertEqual(result.exact(0), 3)
        self.assertEqual(result.exact_at["never"], set())
        self.assertEqual(len(result.exact_at["always"]), 31)
        self.assertEqual(len(result.exact_at["0x4982f0"]), 31)

    def test_best_phase_prefers_the_source_as_it_stands_on_a_tie(self):
        self.assertEqual(scan(FakeHarvest(self.root), "a.cpp", 31).best, 0)
        more = Scan("a.cpp", 4, {"a": {1}, "b": {1}, "c": {0}})
        self.assertEqual(more.best, 1)

    def test_a_phase_that_failed_to_compile_does_not_make_a_witness(self):
        result = Scan("a.cpp", 3, {"a": {0, 1}}, errors={2: "boom"})
        self.assertEqual(result.witnesses, {})

    def test_spans(self):
        self.assertEqual(spans({1, 2, 3, 7}), "1-3,7")
        self.assertEqual(spans(set()), "")
        self.assertEqual(spans({0}), "0")


if __name__ == "__main__":
    unittest.main()
