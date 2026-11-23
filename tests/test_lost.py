"""The `lost` audit and the header definitions it compares.

These run on a temporary git history and a fake target, so they need no gruntz-decomp checkout. The checkout test
runs `concord lost --limit 2` when CONCORD_GRUNTZ names a checkout after `gruntz build`, and skips otherwise.
"""

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from concord import lost
from concord.cli import main
from concord.harvest import Evaluation, FunctionVerdict
from concord.includes import Tree, definitions
from concord.lost import (
    Row,
    called,
    episodes,
    free_renames,
    mangled_identifier,
    parse_baseline,
    reached_changes,
    renames,
)

BASELINE_HEADER = "# gruntz match baseline\n# [functions]\tunit\tfunction\tbest_pct\tcur_pct\ttries\tsrc_hash\trva\thist_pct\tstate\n"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def baseline_row(unit, name, rva, cur, hist):
    return "\t".join([unit, name, f"{cur:.4f}", f"{cur:.4f}", "1", "abc", rva, f"{hist:.4f}", ""])


class FakeTarget:
    """The part of Gruntz that lost reads. A compile scores each function by `scores(headers, rva)`."""

    def __init__(self, root, functions, scores):
        self.root = root
        self.functions = functions  # (unit, symbol, rva, fuzzy, size) as the report scores them now
        self.scores = scores
        self.compiled = []  # the headers of each compile

    def source(self, unit):
        return f"src/{unit}.cpp"

    def verdicts(self, unit=None):
        for unit_name, symbol, rva, fuzzy, size in self.functions:
            if unit in (None, unit_name):
                row = {"symbol": symbol, "exact": fuzzy >= 100, "fuzzy": fuzzy, "best": fuzzy, "size": size, "address": hex(rva)}
                yield FunctionVerdict(unit_name, symbol, row, {})

    def evaluate(self, unit, sources, headers=None):
        self.compiled.append(headers)
        rows = [{"symbol": symbol, "address": hex(rva), "size": size, "fuzzy": self.scores(headers or {}, rva), "exact": False}
                for unit_name, symbol, rva, _, size in self.functions if unit_name == unit]
        return {name: Evaluation(name, unit, sections=[{"functions": rows}]) for name in sources}


SOURCE = b"""#include <Brickz.h>

// RVA(0x00001000, 0x20)
int CFoo::Bar(int a) {
    return CellFlagsAt(a);
}

// RVA(0x00001100, 0x20)
int CFoo::Baz() {
    return 0;
}
"""


class History(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "test")

    def tearDown(self):
        self.directory.cleanup()

    def commit(self, message, files):
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_bytes(text)
        git(self.root, "add", "--", *files)
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD")


class BaselineHistory(unittest.TestCase):
    def test_parse_skips_headers_unit_rows_and_anonymous_data(self):
        text = (BASELINE_HEADER + "u\t2\t2\n" + baseline_row("u", "?Bar@CFoo@@QAEHH@Z", "0x1000", 97.0, 99.0) + "\n"
                + "u\t$anon_data_1\t100.0\t100.0\t1\tcpp:x\t\t100.0\t\n").encode()
        self.assertEqual(parse_baseline(text), {("u", 0x1000): Row("u", "?Bar@CFoo@@QAEHH@Z", 97.0, 97.0, 99.0, 0x1000, "")})

    def test_episodes_split_at_each_return_to_the_high(self):
        points = [("c1", 99.0), ("c2", 97.0), ("c3", 95.0), ("c4", 99.0), ("c5", 96.0), ("c6", 96.0)]
        found = episodes(points, 99.0)
        self.assertEqual([episode.high for episode in found], ["c1", "c4"])
        self.assertEqual(found[0].drops, [{"commit": "c2", "from": 99.0, "to": 97.0}, {"commit": "c3", "from": 97.0, "to": 95.0}])
        self.assertEqual(found[1].drops, [{"commit": "c5", "from": 99.0, "to": 96.0}])

    def test_no_episode_without_a_commit_at_the_high(self):
        self.assertEqual(episodes([("c1", 90.0), ("c2", 91.0)], 99.0), [])

    def test_a_rise_below_the_high_is_not_a_drop(self):
        found = episodes([("c1", 99.0), ("c2", 90.0), ("c3", 95.0)], 99.0)
        self.assertEqual([drop["commit"] for drop in found[0].drops], ["c2"])

    def test_renames_pair_only_names_with_the_same_mangled_tail(self):
        before = {("u", 1): Row("u", "?LoadTable@CGrunt@@QAEHW4Kind@@HHH@Z", 0, 0, 0, 1, ""),
                  ("u", 2): Row("u", "?Old@CGrunt@@QAEHXZ", 0, 0, 0, 2, ""),
                  ("u", 3): Row("u", "?Other@CGrunt@@QAEHXZ", 0, 0, 0, 3, "")}
        after = {("u", 1): Row("u", "?ApplyPickup@CGrunt@@QAEHW4Kind@@HHH@Z", 0, 0, 0, 1, ""),
                 ("u", 2): Row("u", "?New@CGrunt@@QAEXH@Z", 0, 0, 0, 2, ""),
                 ("u", 3): Row("u", "?Other@CGrunt@@QAEHXZ", 0, 0, 0, 3, "")}
        self.assertEqual(renames("u", before, after), [("LoadTable", "ApplyPickup")])
        self.assertIsNone(mangled_identifier("??1CGrunt@@UAE@XZ"))

    def test_a_rename_onto_an_occurring_name_is_left_out(self):
        files = {"include/a.h": b"void Deselect();\n", "src/a.cpp": b"ClearAllSprites();\n"}
        self.assertEqual(free_renames([("ClearAllSprites", "Deselect"), ("Old", "Fresh")], files), [("Old", "Fresh")])


class HeaderDefinitions(unittest.TestCase):
    def test_comments_and_whitespace_are_not_changes(self):
        before = definitions(b"inline int F(int a) { return a + 1; }\n")
        after = definitions(b"// note\ninline int F(int a) {\n    /* same */ return a + 1;\n}\n")
        key = ("function", "", "F", "( int a )")
        self.assertEqual(before[key].text, after[key].text)

    def test_scope_and_macros_are_keyed(self):
        found = definitions(b"class CMapMgr {\npublic:\n    inline int CellFlagsAt(int x, int y) const { return x + y; }\n};\n"
                            b"#define LIMIT(a) ((a) + 1)\n#define COUNT 3\n")  # fmt: skip
        key = ("function", "CMapMgr", "CellFlagsAt", "( int x , int y ) const")
        self.assertEqual(found[key].qualified, "CMapMgr::CellFlagsAt")
        self.assertEqual(found[key].line, 3)
        self.assertIn(("macro", "", "LIMIT", ""), found)
        self.assertIn(("macro", "", "COUNT", ""), found)

    def test_calls_reach_helpers_and_type_names_do_not(self):
        self.assertEqual(called("CMapMgr :: CellFlagsAt ( a , b ) ;"), {"CellFlagsAt"})
        self.assertEqual(called("CLogicRecord x ;"), set())
        self.assertEqual(called("if ( MAX_COUNT > 2 )"), {"if", "MAX_COUNT"})

    def test_a_changed_header_is_found_and_reached_through_its_calls(self):
        source = b"#include <h.h>\nint f() { return CellFlagsAt(1); }\n"
        old = {"src/u.cpp": source, "include/h.h": b"inline int CellFlagsAt(int x) { return x; }\n"}
        new = {"src/u.cpp": source, "include/h.h": b"inline int CellFlagsAt(int x) { return x + 1; }\nclass CLogicRecord { public: CLogicRecord(); };\n"}
        before = Tree(set(old), old.__getitem__)
        after = Tree(set(new), new.__getitem__)
        changed, pool = lost.compare_headers(before, after, "src/u.cpp")
        self.assertEqual(changed, ["include/h.h"])
        self.assertEqual(reached_changes(pool, called("int f ( ) { return CellFlagsAt ( 1 ) ; }")),
                         [{"header": "include/h.h", "name": "CellFlagsAt", "kind": "function", "change": "changed", "line": 1}])
        self.assertEqual(reached_changes(pool, called("CLogicRecord x ;")), [])


class Lost(History):
    def setUp(self):
        super().setUp()
        self.first = self.commit("one", {
            "config/match_baseline.tsv": (BASELINE_HEADER + baseline_row("u", "?Bar@CFoo@@QAEHH@Z", "0x1000", 99.0, 99.0) + "\n"
                                          + baseline_row("u", "?Baz@CFoo@@QAEHXZ", "0x1100", 99.0, 99.0) + "\n").encode(),
            "src/u.cpp": SOURCE,
            "include/Brickz.h": b"// the helper\ninline int CellFlagsAt(int a) { return 1; }\n",
        })
        self.second = self.commit("two", {
            "config/match_baseline.tsv": (BASELINE_HEADER + baseline_row("u", "?Bar@CFoo@@QAEHH@Z", "0x1000", 97.0, 99.0) + "\n"
                                          + baseline_row("u", "?Baz@CFoo@@QAEHXZ", "0x1100", 97.0, 99.0) + "\n").encode(),
            "include/Brickz.h": b"inline int CellFlagsAt(int a) { return 2; }\n",
        })

    def fake(self):
        def scores(headers, rva):
            if rva == 0x1100:
                return 98.0  # Baz never reproduces its high
            return 99.0 if b"return 1;" in headers.get("include/Brickz.h", b"return 1;") else 97.0

        functions = [("u", "?Bar@CFoo@@QAEHH@Z", 0x1000, 97.0, 32), ("u", "?Baz@CFoo@@QAEHXZ", 0x1100, 97.0, 32)]
        return FakeTarget(self.root, functions, scores)

    def test_a_drop_is_confirmed_at_its_high_and_attributed_to_the_header(self):
        result = lost.run(self.fake())
        bar = next(f for f in result["functions"] if f["symbol"] == "?Bar@CFoo@@QAEHH@Z")
        self.assertEqual(bar["status"], "confirmed")
        self.assertEqual(bar["high_commit"], self.first)
        self.assertEqual(bar["compiled"], 99.0)
        self.assertEqual(bar["drops"], [{"commit": self.second, "from": 99.0, "to": 97.0, "own_text_changed": False}])
        self.assertFalse(bar["own_source_changed"])
        self.assertEqual(bar["changed_headers"], ["include/Brickz.h"])
        self.assertEqual([(h["name"], h["change"]) for h in bar["inline_helpers"]], [("CellFlagsAt", "changed")])

    def test_a_high_the_compile_does_not_reproduce_is_stale(self):
        result = lost.run(self.fake())
        baz = next(f for f in result["functions"] if f["symbol"] == "?Baz@CFoo@@QAEHXZ")
        self.assertEqual(baz["status"], "stale")
        self.assertEqual(baz["compiled"], 98.0)
        self.assertIn("scores 98.0000, not 99.0000", baz["detail"])
        self.assertEqual((result["summary"]["confirmed"], result["summary"]["stale"]), (1, 1))

    def test_the_compile_at_the_high_uses_the_snapshot_headers(self):
        target = self.fake()
        lost.run(target, limit=1)
        self.assertEqual(target.compiled[0]["include/Brickz.h"], b"// the helper\ninline int CellFlagsAt(int a) { return 1; }\n")

    def test_min_gap_filters_small_gaps(self):
        self.assertEqual(lost.run(self.fake(), min_gap=5.0)["functions"], [])


class CheckoutTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("CONCORD_GRUNTZ"), "CONCORD_GRUNTZ not set")
    def test_lost_runs_on_the_checkout(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["lost", "--gruntz", os.environ["CONCORD_GRUNTZ"], "--limit", "2"]), 0)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["lost"], 2)


if __name__ == "__main__":
    unittest.main()
