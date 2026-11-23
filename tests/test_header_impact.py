"""The `header-impact` check, on a fake git checkout laid out as `gruntz build` leaves one, with the compiles faked.

The checkout's units are a (includes Gruntz/Shared.h) and b (does not). Each committed file is HEAD; the working tree
differs only where a test writes the marker CHANGED into Gruntz/Shared.h. The fake compile scores a unit from the
committed table, or from the working table when the header on disk carries the marker, so a test controls which
functions a header change moves, and the build report is a separate number the check must not use.
"""

import json
import subprocess
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

FOO = "?Foo@CA@@QAEHXZ"  # 100 in the report and at its banked best of 100
BAR = "?Bar@CA@@QAEHXZ"  # 96 in the report, banked best 97
QUX = "?Qux@CB@@QAEHXZ"  # in unit b, which does not include the header
MARK = "// CHANGED\n"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


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
    write(root / REPORT, json.dumps(report))
    write(root / "config/match_baseline.tsv", "# header\n"
          f"a\t{FOO}\t100.0000\t100.0000\t1\tabc\t0x1000\t100.0000\t\n"
          f"a\t{BAR}\t97.0000\t96.0000\t1\tabc\t0x1100\t97.0000\t\n"
          f"b\t{QUX}\t100.0000\t100.0000\t1\tabc\t0x2000\t100.0000\t\n")
    write(root / "include/Gruntz/Shared.h", "inline int Helper() { return 1; }\n")
    write(root / "include/Gruntz/Other.h", "inline int Other() { return 2; }\n")
    write(root / "src/a.cpp", "#include <Gruntz/Shared.h>\n")
    write(root / "src/b.cpp", "#include <Gruntz/Other.h>\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    git(root, "add", "--", "config", "build", "include", "src")
    git(root, "commit", "-q", "-m", "fixture")


class HeaderImpact(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        make_checkout(self.root)
        self.target = Gruntz(self.root)
        self.committed = {"a": {FOO: 100.0, BAR: 96.0}, "b": {QUX: 100.0}}
        self.working = {}  # the scores of the working tree when the marker is on disk
        self.failing = False
        self.compiled = []  # (unit, side) for each compile

    def tearDown(self):
        self.directory.cleanup()

    def edit_header(self, text=MARK):
        """Changes the shared header in the working tree, uncommitted."""
        write(self.root / "include/Gruntz/Shared.h", "inline int Helper() { return 1; }\n" + text)

    def fake_evaluate(self, target, unit, sources, headers=None, flags=None, jobs=None):
        marked = b"CHANGED" in (headers or {}).get("include/Gruntz/Shared.h", b"")
        side = "working" if marked else "committed"
        self.compiled.append((unit, side))
        if marked and self.failing:
            return {name: Evaluation(name, unit, error="cl: fatal error") for name in sources}
        table = self.working if marked and unit in self.working else self.committed
        rows = [{"symbol": symbol, "address": "0x0", "size": 1, "fuzzy": score, "exact": score >= 100}
                for symbol, score in table.get(unit, {}).items()]
        return {name: Evaluation(name, unit, sections=[{"functions": rows}]) for name in sources}

    def run_check(self, headers=("Gruntz/Shared.h",)):
        with patch.object(Gruntz, "evaluate", lambda target, unit, sources, headers=None, flags=None, jobs=None:
                          self.fake_evaluate(target, unit, sources, headers, flags, jobs)):
            return header_impact.run(self.target, list(headers))

    def test_only_units_whose_closure_holds_the_header_are_compiled_at_both_versions(self):
        result = self.run_check()
        self.assertEqual(result["units"], ["a"])
        self.assertEqual(sorted(self.compiled), [("a", "committed"), ("a", "committed")])  # the unchanged tree is one side twice

    def test_a_header_given_relative_to_include_resolves(self):
        self.assertEqual(header_impact.resolve_header(self.root, "Gruntz/Shared.h"), "include/Gruntz/Shared.h")
        with self.assertRaises(FileNotFoundError):
            header_impact.resolve_header(self.root, "Gruntz/Missing.h")

    def test_an_unchanged_tree_reports_nothing_even_where_the_build_report_differs(self):
        # The report says FOO scores 100, but both compiles of the unchanged tree score it 99.095: a difference from the
        # build that the header change did not cause, so it is not reported.
        self.committed = {"a": {FOO: 99.095, BAR: 96.0}}
        result = self.run_check()
        self.assertEqual(result["functions"], [])
        self.assertEqual(result["summary"]["regressions"], 0)

    def test_a_working_change_that_loses_exact_status_is_a_regression(self):
        self.edit_header()
        self.working = {"a": {FOO: 99.5, BAR: 96.0}}
        result = self.run_check()
        row = result["functions"][0]
        self.assertEqual((row["symbol"], row["committed"], row["working"], row["best"]), (FOO, 100.0, 99.5, 100.0))
        self.assertEqual(row["flags"], ["lost exact", "below banked best"])
        self.assertEqual(result["summary"]["regressions"], 1)

    def test_a_working_change_that_raises_a_score_is_not_a_regression(self):
        self.edit_header()
        self.working = {"a": {FOO: 100.0, BAR: 98.0}}
        result = self.run_check()
        self.assertEqual([(row["symbol"], row["flags"]) for row in result["functions"]], [(BAR, [])])
        self.assertEqual((result["summary"]["regressions"], result["summary"]["higher"]), (0, 1))

    def test_a_score_already_below_its_banked_best_is_not_an_exact_loss(self):
        self.committed = {"a": {FOO: 88.0, BAR: 96.0}}
        self.edit_header()
        self.working = {"a": {FOO: 88.0, BAR: 96.0}}
        self.assertEqual(self.run_check()["functions"], [])

    def test_a_function_the_working_tree_no_longer_emits_is_a_regression(self):
        self.edit_header()
        self.working = {"a": {FOO: 100.0}}
        result = self.run_check()
        self.assertIn("not emitted", result["functions"][0]["flags"])

    def test_a_unit_that_does_not_compile_in_the_working_tree_fails_the_check(self):
        self.edit_header()
        self.failing = True
        result = self.run_check()
        self.assertIn("working tree", result["errors"]["a"])
        self.assertEqual(result["summary"]["failed_units"], 1)

    def test_the_command_exits_nonzero_on_a_regression_and_zero_without_one(self):
        def run_command():
            with patch.object(Gruntz, "evaluate", lambda target, unit, sources, headers=None, flags=None, jobs=None:
                              self.fake_evaluate(target, unit, sources, headers, flags, jobs)):
                output = StringIO()
                with redirect_stdout(output):
                    code = main(["header-impact", "--gruntz", str(self.root), "Gruntz/Shared.h"])
            return code, output.getvalue()

        self.assertEqual(run_command()[0], 0)
        self.edit_header()
        self.working = {"a": {FOO: 99.5, BAR: 96.0}}
        code, output = run_command()
        self.assertEqual(code, 1)
        self.assertIn("lost exact", output)


if __name__ == "__main__":
    unittest.main()
