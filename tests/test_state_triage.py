"""The `state-triage` check: pad placement, first-line detection, the classes, resuming, and the CLI, on a fake checkout.

The fake compile scores each function by how many pad declarations the candidate and its header overlay carry together:
FOO is exact at five pads and 96 otherwise, BAR is 98 at two pads and 90 otherwise, and BAZ is always 90. So FOO is a
state function, BAR improves, BAZ is a source function, and the test controls every class through the pad count. A
test that runs against a real checkout is at the end and needs CONCORD_GRUNTZ.
"""

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from concord import state_triage
from concord.cli import main
from concord.gruntz import REPORT, Gruntz
from concord.harvest import Evaluation

FOO = "?Foo@CA@@QAEHXZ"
BAR = "?Bar@CA@@QAEHXZ"
BAZ = "?Baz@CA@@QAEHXZ"
QUX = "?Qux@CB@@QAEHXZ"  # exact in unit b, which is not open
SHARED = "include/Gruntz/Shared.h"  # first line of code at line 4
OTHER = "include/Gruntz/Other.h"  # first line of code at line 5, and it includes Leaf.h
LEAF = "include/Gruntz/Leaf.h"  # first line of code at line 1
SOURCE = "src/a.cpp"  # includes at lines 1 and 2, the line after the last at 3


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_checkout(root):
    write(root / "config/units.toml", '[flags]\ncpp = ["/O2"]\n\n'
          '[[unit]]\nunit = "a"\nsource = "src/a.cpp"\nflags = "cpp"\n\n'
          '[[unit]]\nunit = "b"\nsource = "src/b.cpp"\nflags = "cpp"\n')  # fmt: skip
    write(root / "build/gen/bindings.tsv", "# comment\nspace\tname\trva\tunit\n"
          f"text\t{FOO}\t0x1000\ta\ntext\t{BAR}\t0x1100\ta\ntext\t{BAZ}\t0x1200\ta\ntext\t{QUX}\t0x2000\tb\n")  # fmt: skip
    report = {"units": [
        {"name": "x/a", "functions": [{"name": FOO, "fuzzy_match_percent": 96.0, "size": "32"},
                                      {"name": BAR, "fuzzy_match_percent": 90.0, "size": "48"},
                                      {"name": BAZ, "fuzzy_match_percent": 90.0, "size": "16"}]},
        {"name": "x/b", "functions": [{"name": QUX, "fuzzy_match_percent": 100.0, "size": "16"}]},
    ]}  # fmt: skip
    write(root / REPORT, json.dumps(report))
    write(root / "config/match_baseline.tsv", "# header\n"
          f"a\t{FOO}\t96.0000\t96.0000\t1\tabc\t0x1000\t96.0000\t\n"
          f"a\t{BAR}\t90.0000\t90.0000\t1\tabc\t0x1100\t90.0000\t\n"
          f"a\t{BAZ}\t90.0000\t90.0000\t1\tabc\t0x1200\t90.0000\t\n"
          f"b\t{QUX}\t100.0000\t100.0000\t1\tabc\t0x2000\t100.0000\t\n")  # fmt: skip
    write(root / "include/Gruntz/Shared.h", "#ifndef SHARED_H\n#define SHARED_H\n\ninline int Helper() { return 1; }\n#endif\n")
    write(root / "include/Gruntz/Other.h", "// comment\n/* block\n more */\n#include <Gruntz/Leaf.h>\nint Other();\n")
    write(root / "include/Gruntz/Leaf.h", "static int Leaf;\n")
    write(root / "src/a.cpp", "#include <Gruntz/Shared.h>\n#include <Gruntz/Other.h>\nint Value = 1;\n")
    write(root / "src/b.cpp", "#include <Gruntz/Unrelated.h>\n")
    write(root / "include/Gruntz/Unrelated.h", "int Unrelated;\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    git(root, "add", "--", "config", "build", "include", "src")
    git(root, "commit", "-q", "-m", "fixture")


def pads_in(data: bytes) -> int:
    return data.count(b"concord_state_pad_")


class PadPlacement(unittest.TestCase):
    def test_a_pad_goes_before_the_line_and_removing_it_restores_the_text(self):
        text = b"#include <a>\nint x;\n"
        padded = state_triage.insert_pad(text, 2, 2)
        self.assertEqual(padded, b"#include <a>\n" + state_triage.pad(2) + b"int x;\n")
        self.assertEqual(b"".join(line for line in padded.splitlines(keepends=True) if b"concord_state_pad_" not in line), text)

    def test_a_pad_after_a_last_line_without_a_newline_stays_on_its_own_line(self):
        self.assertEqual(state_triage.insert_pad(b"#include <a>", 2, 1), b"#include <a>\ntypedef int concord_state_pad_1_0;\n")

    def test_source_cuts_are_each_include_line_and_the_line_after_the_last(self):
        self.assertEqual(state_triage.source_cuts(b"#include <a>\n#include <b>\nint x;\n"), [1, 2, 3])
        self.assertEqual(state_triage.source_cuts(b"int a;\n#include <x>\n#include <y>\nint b;\n"), [2, 3, 4])
        self.assertEqual(state_triage.source_cuts(b"int a;\n"), [])

    def test_the_first_declaration_skips_guards_directives_and_comments(self):
        guarded = b"#ifndef G\n#define G\n\n#include <A.h>\n// c\n/* block\nstill */\nint x;\n"
        self.assertEqual(state_triage.first_declaration(guarded), 8)
        self.assertEqual(state_triage.first_declaration(b"/* a */ int y;\n"), 1)
        self.assertEqual(state_triage.first_declaration(b"#define M(x) \\\n  x\nint z;\n"), 3)
        self.assertIsNone(state_triage.first_declaration(b"#pragma once\n// nothing\n"))

    def test_offsets_are_a_range_that_never_includes_the_baseline(self):
        self.assertEqual(state_triage.offset_range("1-7"), [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(state_triage.offset_range("0-3"), [1, 2, 3])
        self.assertEqual(state_triage.offset_range("5"), [5])
        self.assertEqual(state_triage.offset_range("0"), [])
        with self.assertRaises(ValueError):
            state_triage.offset_range("5-2")

    def test_the_compile_cap_is_four_whatever_concord_jobs_says(self):
        with patch.dict(os.environ, {"CONCORD_JOBS": "8"}):
            self.assertEqual(state_triage.worker_count(), 4)
        with patch.dict(os.environ, {"CONCORD_JOBS": "2"}):
            self.assertEqual(state_triage.worker_count(), 2)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(state_triage.worker_count(), 4)


class Classes(unittest.TestCase):
    def test_each_class_follows_from_the_baseline_and_the_variants(self):
        rows = [{"symbol": "exact", "bytes": 1}, {"symbol": "state", "bytes": 2}, {"symbol": "improves", "bytes": 4},
                {"symbol": "source", "bytes": 8}]  # fmt: skip
        baseline = {"exact": 100.0, "state": 96.0, "improves": 90.0, "source": 90.0}
        only = state_triage.State("s.cpp:2", 5)
        variants = {only: {"state": 100.0, "improves": 98.0, "source": 90.0, "exact": 100.0},
                    state_triage.State("s.cpp:3", 1): {"state": 96.0, "improves": 97.0, "source": 90.0, "exact": 100.0}}
        by_symbol = {row["symbol"]: row for row in state_triage.classify(rows, baseline, variants)}
        self.assertEqual({symbol: row["class"] for symbol, row in by_symbol.items()},
                         {"exact": "exact", "state": "state", "improves": "improves", "source": "source"})  # fmt: skip
        self.assertEqual(by_symbol["state"]["exact_states"], [{"cut": "s.cpp:2", "offset": 5}])
        self.assertEqual(by_symbol["improves"]["best"], 98.0)

    def test_a_rise_under_half_a_point_is_source_not_improves(self):
        rows = [{"symbol": "f", "bytes": 1}]
        row = state_triage.classify(rows, {"f": 99.6}, {state_triage.State("s.cpp:2", 1): {"f": 99.9}})[0]
        self.assertEqual(row["class"], "source")

    def test_the_states_that_make_every_open_function_exact_are_reported(self):
        rows = [{"symbol": "f", "bytes": 1, "class": "state"}, {"symbol": "g", "bytes": 2, "class": "state"}]
        both = state_triage.State("s.cpp:2", 5)
        one = state_triage.State("s.cpp:3", 5)
        summary = state_triage.summarize_unit(rows, {both: {"f": 100.0, "g": 100.0}, one: {"f": 100.0, "g": 99.0}})
        self.assertEqual(summary["all_exact_states"], [{"cut": "s.cpp:2", "offset": 5}])
        self.assertEqual(summary["best_state"], {"cut": "s.cpp:2", "offset": 5, "exact": 2})
        self.assertEqual(summary["summary"]["state"], {"functions": 2, "bytes": 3})


class Discard(unittest.TestCase):
    def test_a_read_compile_directory_is_removed_and_an_error_without_one_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "retail.obj").write_bytes(b"x")
            state_triage.discard([Evaluation("c", "a", sections=[{"functions": [], "retail": str(out / "retail.obj")}]),
                                  Evaluation("d", "a", error="cl: fatal error")])
            self.assertFalse(out.exists())


class Triage(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "checkout"
        make_checkout(self.root)
        self.target = Gruntz(self.root)
        self.save = Path(self.directory.name) / "triage-save.json"  # outside the checkout, as a run's save file would be
        self.calls = []  # (unit, candidate names, overlay headers, jobs) for each compile request

    def tearDown(self):
        self.directory.cleanup()

    def fake_evaluate(self, unit, sources, headers=None, jobs=None):
        self.calls.append((unit, sorted(sources), sorted(headers or {}), jobs))
        overlay = b"".join((headers or {}).values())
        evaluations = {}
        for name, body in sources.items():
            pads = pads_in(body + overlay)
            functions = [{"symbol": FOO, "fuzzy": 100.0 if pads == 5 else 96.0},
                         {"symbol": BAR, "fuzzy": 98.0 if pads == 2 else 90.0},
                         {"symbol": BAZ, "fuzzy": 90.0}]  # fmt: skip
            evaluations[name] = Evaluation(name, unit, sections=[{"functions": functions}])
        return evaluations

    def run_triage(self, **options):
        with patch.object(Gruntz, "evaluate", lambda target, unit, sources, headers=None, flags=None, jobs=None:
                          self.fake_evaluate(unit, sources, headers, jobs)):
            return state_triage.run(self.target, **options)

    def by_symbol(self, result, unit="a"):
        return {row["symbol"]: row for row in result["units"][unit]["functions"]}

    def test_the_source_cut_points_give_each_function_its_class_and_exact_states(self):
        result = self.run_triage(units=["a"], offsets=[1, 2, 3, 4, 5, 6, 7])
        rows = self.by_symbol(result)
        self.assertEqual({symbol: row["class"] for symbol, row in rows.items()}, {FOO: "state", BAR: "improves", BAZ: "source"})
        self.assertEqual(rows[FOO]["exact_states"], [{"cut": f"{SOURCE}:{line}", "offset": 5} for line in (1, 2, 3)])
        self.assertEqual(rows[BAR]["best"], 98.0)
        self.assertEqual(result["units"]["a"]["summary"]["state"], {"functions": 1, "bytes": 32})

    def test_with_headers_each_project_header_adds_a_cut_before_its_first_line_of_code(self):
        result = self.run_triage(units=["a"], headers=True, offsets=[5])
        exact = [state["cut"] for state in self.by_symbol(result)[FOO]["exact_states"]]
        self.assertEqual(sorted(exact), sorted([f"{SOURCE}:1", f"{SOURCE}:2", f"{SOURCE}:3", f"{SHARED}:4", f"{OTHER}:5", f"{LEAF}:1"]))

    def test_the_baseline_is_compiled_once_and_each_state_once(self):
        self.run_triage(units=["a"], offsets=[1, 2])
        self.assertEqual(len(self.calls), 2)  # the baseline, then one batch of six sources (3 cuts x 2 offsets)
        self.assertEqual(len(self.calls[1][1]), 6)

    def test_source_states_are_compiled_in_groups_with_the_same_result(self):
        whole = self.run_triage(units=["a"], offsets=[1, 2])
        self.calls.clear()
        with patch.object(state_triage, "SOURCES_PER_REQUEST", 2):
            grouped = self.run_triage(units=["a"], offsets=[1, 2])
        self.assertEqual([len(names) for _, names, _, _ in self.calls], [1, 2, 2, 2])  # the baseline, then three groups of the six sources
        self.assertEqual(grouped["units"], whole["units"])

    def test_every_compile_stays_within_four_jobs_and_no_pad_reaches_the_checkout(self):
        with patch.dict(os.environ, {"CONCORD_JOBS": "8"}):
            self.run_triage(units=["a"], headers=True, offsets=[1, 5])
        self.assertTrue(self.calls)
        self.assertTrue(all(jobs == 4 for *_, jobs in self.calls))
        for path in self.root.rglob("*"):
            if path.is_file() and ".git" not in path.parts and "build" not in path.relative_to(self.root).parts:
                self.assertNotIn(b"concord_state_pad_", path.read_bytes(), path)

    def test_a_saved_run_resumes_without_compiling_again(self):
        first = self.run_triage(units=["a"], headers=True, offsets=[1, 5], save=self.save)
        self.assertTrue(self.calls)
        self.calls.clear()
        second = self.run_triage(units=["a"], headers=True, offsets=[1, 5], save=self.save)
        self.assertEqual(self.calls, [])
        self.assertEqual(second["units"], first["units"])

    def test_a_save_file_from_another_build_report_is_refused(self):
        self.run_triage(units=["a"], offsets=[1], save=self.save)
        write(self.root / REPORT, (self.root / REPORT).read_text() + "\n")
        with self.assertRaises(ValueError):
            self.run_triage(units=["a"], offsets=[1], save=self.save)

    def test_a_unit_that_does_not_compile_is_reported_and_not_classed(self):
        def failing(target, unit, sources, headers=None, flags=None, jobs=None):
            return {name: Evaluation(name, unit, error="cl: fatal error C1083") for name in sources}

        with patch.object(Gruntz, "evaluate", failing):
            result = state_triage.run(self.target, units=["a"], offsets=[1])
        self.assertEqual(result["units"]["a"]["error"], "cl: fatal error C1083")
        self.assertEqual(result["summary"]["failed_units"], 1)
        self.assertEqual(result["units"]["a"]["functions"], [])

    def test_the_command_prints_each_class_and_exits_zero(self):
        output = StringIO()
        with patch.object(Gruntz, "evaluate", lambda target, unit, sources, headers=None, flags=None, jobs=None:
                          self.fake_evaluate(unit, sources, headers, jobs)), redirect_stdout(output), redirect_stderr(StringIO()):
            code = main(["state-triage", "--gruntz", str(self.root), "--unit", "a", "--offsets", "1-7"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("state", text)
        self.assertIn("improves", text)
        self.assertIn(FOO, text)


@unittest.skipUnless(os.environ.get("CONCORD_GRUNTZ"), "needs a gruntz-decomp checkout after `gruntz build` (CONCORD_GRUNTZ)")
class RealCheckout(unittest.TestCase):
    def test_one_small_open_unit_triages_with_real_compiles(self):
        target = Gruntz(Path(os.environ["CONCORD_GRUNTZ"]).resolve())
        open_bytes = {unit: sum(row["size"] for row in (v.row for v in target.verdicts(unit)) if row["fuzzy"] < 100) for unit in target.open_units()}
        unit = min(open_bytes, key=open_bytes.get)
        result = state_triage.run(target, units=[unit], offsets=[1])
        data = result["units"][unit]
        self.assertEqual(data["error"], "")
        self.assertTrue(data["functions"])
        self.assertTrue(all(row["class"] in state_triage.CLASSES for row in data["functions"]))


if __name__ == "__main__":
    unittest.main()
