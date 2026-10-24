"""Limit scans sort a unit's functions by whether lowered inliner limits
return them to exact."""

import unittest
from pathlib import Path

from concord.harvest import Evaluation
from concord.limits import SETTINGS, Scan, scan


class FakeHarvest:
    """A unit of four functions: one exact at every setting, one only as
    compiled, one only once inlining is throttled, one never."""

    def __init__(self, root):
        self.root = root
        self.items = {}

    def source(self, unit):
        return unit

    def evaluate_overlays(self, items):
        self.items = items
        out = {}
        for name, item in items.items():
            flags = tuple(item[2])
            rows = [
                {"symbol": "always", "exact": True},
                {"symbol": "baseline-only", "exact": not flags},
                {"symbol": "throttled", "exact": flags == ("--param", "inline-unit-growth=0")},
                {"symbol": "never", "exact": False},
            ]
            out[name] = Evaluation(name, item[0], sections=[{"functions": rows}])
        return out


class LimitsTest(unittest.TestCase):
    def test_scan_separates_throttled_functions_from_untouched(self):
        result = scan(FakeHarvest(Path("/tmp")), "a.cpp", {"baseline": [], "u0": ["--param", "inline-unit-growth=0"]})
        self.assertEqual(result.throttled, {"throttled": {"u0"}})
        self.assertEqual(result.exact("baseline"), 2)
        self.assertEqual(result.exact_at["never"], set())

    def test_every_setting_compiles_the_unit_unchanged_with_its_flags(self):
        harvest = FakeHarvest(Path("/tmp"))
        scan(harvest, "a.cpp")
        self.assertEqual(set(harvest.items), set(SETTINGS))
        for name, item in harvest.items.items():
            self.assertEqual((item[0], item[1]), ("a.cpp", {}))
            self.assertEqual(list(item[2]), SETTINGS[name])

    def test_regressed_lists_functions_a_lowered_limit_loses(self):
        result = Scan("a.cpp", ["baseline", "u0"], {"a": {"baseline"}, "b": {"baseline", "u0"}, "c": {"u0"}})
        self.assertEqual(result.regressed("u0"), ["a"])
        self.assertEqual(result.throttled, {"c": {"u0"}})


if __name__ == "__main__":
    unittest.main()
