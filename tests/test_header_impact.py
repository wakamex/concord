"""The `header-impact` check, on a fake checkout laid out as `gruntz build` leaves one, with the compiles faked.

The checkout's units are a (includes Gruntz/Shared.h) and b (does not). The fake compile scores each function by
`scores[unit][symbol]`, so a test controls which functions a header change lowers.
"""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from concord import header_impact
from concord.cli import main
from concord.gruntz import REPORT, Gruntz
from concord.harvest import Evaluation

FOO = "?Foo@CA@@QAEHXZ"  # exact in the report and at its banked best of 100
BAR = "?Bar@CA@@QAEHXZ"  # 96 in the report, banked best 97
QUX = "?Qux@CB@@QAEHXZ"  # exact in unit b, which does not include the header


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_checkout(root):
    write(root / "config/units.toml", '[flags]\ncpp = ["/O2"]\n\n'
          '[[unit]]\nunit = "a"\nsource = "src/a.cpp"\nflags = "cpp"\n\n'
          '[[unit]]\nunit = "b"\nsource = "src/b.cpp"\nflags = "cpp"\n')
    write(root / "build/gen/bindings.tsv", "# comment\nspace\tname\trva\tunit\n"
          f"text\t{FOO}\t0x1000\ta\ntext\t{BAR}\t0x1100\ta\ntext\t{QUX}\t0x2000\tb\n")
    report = {"units": [
        {"name": "x/a", "functions": [{"name": FOO, "fuzzy_match_percent": 100.0, "size": "32"},
                                      {"name": BAR, "fuzzy_match_percent": 96.0, "size": "48"}]},
        {"name": "x/b", "functions": [{"name": QUX, "fuzzy_match_percent": 100.0, "size": "16"}]},
    ]}
    write(root / "build/objdiff/compare-new/report.json", json.dumps(report))
    write(root / "config/match_baseline.tsv", "# header\n"
          f"a\t{FOO}\t100.0000\t100.0000\t1\tabc\t0x1000\t100.0000\t\n"
          f"a\t{BAR}\t97.0000\t96.0000\t1\tabc\t0x1100\t97.0000\t\n"
          f"b\t{QUX}\t100.0000\t100.0000\t1\tabc\t0x2000\t100.0000\t\n")
    write(root / "include/Gruntz/Shared.h", "inline int Helper() { return 1; }\n")
    write(root / "include/Gruntz/Other.h", "inline int Other() { return 2; }\n")
    write(root / "src/a.cpp", "#include <Gruntz/Shared.h>\n")
    write(root / "src/b.cpp", "#include <Gruntz/Other.h>\n")


class HeaderImpact(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        make_checkout(self.root)
        self.target = Gruntz(self.root)
        self.scores = {"a": {FOO: 100.0, BAR: 96.0}, "b": {QUX: 100.0}}
        self.compiled = []

    def tearDown(self):
        self.directory.cleanup()

    def fake_evaluate(self, target, unit, sources, headers=None, flags=None, jobs=None):
        self.compiled.append((unit, headers))
        rows = [{"symbol": symbol, "address": "0x0", "size": 1, "fuzzy": score, "exact": score >= 100}
                for symbol, score in self.scores[unit].items()]
        return {name: Evaluation(name, unit, sections=[{"functions": rows}]) for name in sources}

    def run_with(self, scores, headers=("Gruntz/Shared.h",)):
        self.scores = scores
        with patch.object(Gruntz, "evaluate", lambda target, unit, sources, headers=None, flags=None, jobs=None:
                          self.fake_evaluate(target, unit, sources, headers, flags, jobs)):
            return header_impact.run(self.target, list(headers))

    def test_only_units_whose_closure_holds_the_header_are_compiled(self):
        result = self.run_with({"a": {FOO: 100.0, BAR: 96.0}, "b": {QUX: 100.0}})
        self.assertEqual(result["units"], ["a"])
        self.assertEqual([unit for unit, _ in self.compiled], ["a"])

    def test_a_header_given_relative_to_include_resolves(self):
        self.assertEqual(header_impact.resolve_header(self.root, "Gruntz/Shared.h"), "include/Gruntz/Shared.h")
        with self.assertRaises(FileNotFoundError):
            header_impact.resolve_header(self.root, "Gruntz/Missing.h")

    def test_an_unchanged_build_reports_no_regression(self):
        result = self.run_with({"a": {FOO: 100.0, BAR: 96.0}})
        self.assertEqual(result["functions"], [])
        self.assertEqual(result["summary"]["regressions"], 0)

    def test_a_function_that_loses_exact_status_is_a_regression(self):
        result = self.run_with({"a": {FOO: 99.5, BAR: 96.0}})
        row = result["functions"][0]
        self.assertEqual((row["symbol"], row["report"], row["now"], row["best"]), (FOO, 100.0, 99.5, 100.0))
        self.assertEqual(row["flags"], ["lost exact", "below banked best"])
        self.assertEqual(result["summary"]["regressions"], 1)

    def test_a_drop_below_a_score_already_under_best_is_lower_not_a_regression(self):
        result = self.run_with({"a": {FOO: 100.0, BAR: 96.5}})  # BAR rose from its report of 96; nothing to flag
        self.assertEqual(result["functions"], [])
        result = self.run_with({"a": {FOO: 100.0, BAR: 95.0}})
        self.assertEqual(result["functions"][0]["flags"], [])
        self.assertEqual(result["summary"]["regressions"], 0)

    def test_a_dip_below_its_banked_best_is_not_an_exact_loss(self):
        # FOO scores 88 in the report under a banked best of 100, so the change keeps it where the report had it
        report = json.loads((self.root / REPORT).read_text())
        report["units"][0]["functions"][0]["fuzzy_match_percent"] = 88.0
        (self.root / REPORT).write_text(json.dumps(report))
        self.target = Gruntz(self.root)
        self.assertEqual(self.run_with({"a": {FOO: 88.0, BAR: 96.0}})["functions"], [])

    def test_a_function_the_unit_no_longer_emits_is_a_regression(self):
        result = self.run_with({"a": {BAR: 96.0}})
        self.assertIn("not emitted", result["functions"][0]["flags"])

    def test_the_command_exits_nonzero_on_a_regression(self):
        with patch.object(Gruntz, "evaluate", lambda target, unit, sources, headers=None, flags=None, jobs=None:
                          self.fake_evaluate(target, unit, sources, headers, flags, jobs)):
            self.scores = {"a": {FOO: 99.5, BAR: 96.0}}
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["header-impact", "--gruntz", str(self.root), "Gruntz/Shared.h"]), 1)
        self.assertIn("lost exact", output.getvalue())


if __name__ == "__main__":
    unittest.main()
