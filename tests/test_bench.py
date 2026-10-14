"""The benchmark's own mutations, its case filter and its table."""

import tempfile
import unittest
from pathlib import Path

from concord.bench import (
    MUTATIONS,
    Case,
    Located,
    Outcome,
    cases,
    changed_lines,
    commute,
    expand_assignment,
    location_table,
    nest_condition,
    split_declaration,
    table,
    while_loop,
)
from concord.harvest import Evaluation
from concord.transforms import find_function

SOURCE = b"""int total(int* values, int count, int scale)
{
    int sum = 0;
    for (int i = 0; i < count; ++i)
    {
        sum += values[i];
    }
    return sum * scale + count;
}

int skip(int count)
{
    int n = 0;
    for (int i = 0; i < count; ++i)
    {
        if (i == 3)
            continue;
        ++n;
    }
    return n;
}
"""
TOTAL, SKIP = "_Z5totalPiii", "_Z4skipi"


class Mutations(unittest.TestCase):
    def test_commutative_operands_swap_and_comparisons_do_not(self):
        found = [r.description for r in commute(SOURCE, find_function(SOURCE, TOTAL))]
        self.assertEqual(found, ["line 8: sum * scale -> scale * sum"])

    def test_a_for_loop_becomes_a_while_loop_with_its_step_last(self):
        [rewrite] = while_loop(SOURCE, find_function(SOURCE, TOTAL))
        self.assertIn(b"{ int i = 0; while (i < count) {\n        sum += values[i];\n++i;\n} }", rewrite.source)

    def test_a_loop_with_continue_is_left_alone(self):
        self.assertEqual(list(while_loop(SOURCE, find_function(SOURCE, SKIP))), [])


    def test_a_braceless_loop_body_gets_braces(self):
        source = b"void f(int n)\n{\n    for (int i = 0; i < n; ++i)\n        use(i);\n}\n"
        [rewrite] = while_loop(source, find_function(source, "_Z1fi"))
        self.assertIn(b"{ int i = 0; while (i < n) {\nuse(i);\n++i;\n} }", rewrite.source)

    def test_other_changes_concord_cannot_undo(self):
        source = b"int f(int a, int b)\n{\n    int n = a * 2;\n    n += b - 1;\n    if (a && b)\n        n = 0;\n    return n;\n}\n"
        scope = find_function(source, "_Z1fii")
        self.assertIn(b"    n = n + (b - 1);\n", next(expand_assignment(source, scope)).source)
        self.assertIn(b"    int n;\n    n = a * 2;\n", next(split_declaration(source, scope)).source)
        self.assertIn(b"    if (a) { if (b) n = 0; }\n", next(nest_condition(source, scope)).source)


class FakeHarvest:
    """The while-loop form stops `total` matching; the commuted form stops `skip`
    as well, which is an effect beyond the function."""

    def __init__(self, root):
        self.root = root

    def evaluate(self, unit, sources):
        out = {}
        for name, text in sources.items():
            exact = {TOTAL, SKIP}
            if b"while (" in text:
                exact = {SKIP}
            if b"scale * sum" in text:
                exact = set()
            rows = [{"symbol": s, "exact": s in exact} for s in (TOTAL, SKIP)]
            out[name] = Evaluation(name, unit, sections=[{"functions": rows}], fuzzy={TOTAL: 91.5})
        return out


class Cases(unittest.TestCase):
    def test_a_case_is_a_change_that_stops_only_its_function_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.cpp").write_bytes(SOURCE)
            found = cases(FakeHarvest(root), [("a.cpp", TOTAL)])
        self.assertEqual([(c.mutation, c.symbol, c.score) for c in found], [("for loop as while loop", TOTAL, 91.5)])

    def test_each_kind_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.cpp").write_bytes(SOURCE)
            self.assertEqual(cases(FakeHarvest(root), [("a.cpp", TOTAL)], per_kind=0), [])

    def test_the_table_counts_recoveries_by_mutation(self):
        def outcome(mutation, match, permute):
            return Outcome(Case("a.cpp", TOTAL, mutation, "", b"", 90.0), match, 1, permute, 8)

        rows = table([outcome("swap if branches", True, True), outcome("swap if branches", False, True), outcome("for loop as while loop", False, False)])
        self.assertEqual(rows, [("swap if branches", True, 2, 1, 2, 90.0, 8.0), ("for loop as while loop", True, 1, 0, 0, 90.0, 0.0)])
        self.assertEqual(sum(1 for _, known in MUTATIONS.values() if not known), 2)


class Location(unittest.TestCase):
    def test_changed_lines_are_numbered_in_the_mutated_source(self):
        self.assertEqual(changed_lines(b"a\nb\nc\n", b"a\nB\nc\n"), {2})
        self.assertEqual(changed_lines(b"a\nb\nc\n", b"a\nx\ny\nb\nc\n"), {2, 3})
        self.assertEqual(changed_lines(b"a\nb\nc\n", b"a\nc\n"), {2})

    def test_distance_share_and_table(self):
        def located(attributed, changed):
            return Located(Case("a.cpp", TOTAL, "swap if branches", "", b"", 90.0), [], attributed, changed, 10)

        hit, near, far, blind = located({4, 9}, {4}), located({6}, {4}), located({1}, {9}), located(set(), {4})
        self.assertEqual([i.distance for i in (hit, near, far, blind)], [0, 2, 8, None])
        self.assertEqual(hit.share, 0.2)
        self.assertEqual(location_table([hit, near, far, blind]), [("swap if branches", 4, 3, 1, 2, 0.1)])


if __name__ == "__main__":
    unittest.main()
