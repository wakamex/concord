"""Target selection and candidate order for the permute search."""

import random
import unittest
from pathlib import Path

from concord.harvest import Evaluation
from concord.model import DiffResult
from concord.permute import _mutate, drop_twins, permute, singles
from concord.transforms import ALL, find_function

SOURCE = b"""int f(int a, int b)
{
    int low = a < b;
    int n = 0;
    n = n + 1;
    if (b > a)
        n = 2;
    else
        n = 3;
    return n + low;
}
"""
SYMBOL = "_Z1fii"


class Neighbour:
    """A target on which swapping the first comparison makes `f` exact and moves
    its neighbour `g`, ten times its size, from 80 to 79.99."""

    root = Path(".")
    rewrites = ALL
    find_function = staticmethod(find_function)

    def __init__(self, net_rule):
        self.net_rule = net_rule

    def evaluate(self, unit, sources):
        out = {}
        for name, text in sources.items():
            fixed = b"int low = b > a;" in text
            rows = [{"symbol": SYMBOL, "exact": fixed, "size": 40}, {"symbol": "g", "exact": False, "size": 400}]
            out[name] = Evaluation(name, unit, sections=[{"functions": rows}], fuzzy={SYMBOL: 100.0 if fixed else 90.0, "g": 79.99 if fixed else 80.0})
        return out

    def diff(self, verdict, candidate_object=None):
        return DiffResult(100.0 if verdict.row["exact"] else 90.0, verdict.row["exact"])


class NetRule(unittest.TestCase):
    def test_a_lower_neighbour_rejects_the_candidate_without_the_net_rule(self):
        result = permute(Neighbour(net_rule=False), "u.cpp", SYMBOL, budget=48, source=SOURCE)
        self.assertFalse(result.best.exact)
        self.assertEqual(result.lowered, {})

    def test_the_net_rule_takes_it_while_the_unit_gains_and_names_the_neighbour(self):
        result = permute(Neighbour(net_rule=True), "u.cpp", SYMBOL, budget=48, source=SOURCE)
        self.assertTrue(result.best.exact)  # 40 bytes gain 10 points, 400 bytes lose 0.01
        self.assertIn(b"int low = b > a;", result.source)
        self.assertEqual(result.lowered, {"g": (80.0, 79.99)})


class Twins(unittest.TestCase):
    def test_variants_with_a_listed_base_twin_are_dropped(self):
        functions = {
            ("a.cpp", "_ZN1AC1Ev"), ("a.cpp", "_ZN1AC2Ev"),  # C1 goes, C2 stays
            ("a.cpp", "_ZN1AD0Ev"), ("a.cpp", "_ZN1AD1Ev"), ("a.cpp", "_ZN1AD2Ev"),  # only D2 stays
            ("b.cpp", "_ZN1BC1Ev"),  # its C2 is exact or elsewhere: kept
            ("c.cpp", "_ZN1AC1Ev"),  # a twin in another unit does not count
        }  # fmt: skip
        self.assertEqual(
            drop_twins(functions),
            {("a.cpp", "_ZN1AC2Ev"), ("a.cpp", "_ZN1AD2Ev"), ("b.cpp", "_ZN1BC1Ev"), ("c.cpp", "_ZN1AC1Ev")},
        )


class Order(unittest.TestCase):
    def test_single_rewrites_come_nearest_the_given_lines_first(self):
        found = singles(SOURCE, SYMBOL, {6})
        self.assertGreater(len(found), 3)
        self.assertEqual(len({text for text, _ in found}), len(found))
        lines = [int(why.split("line ")[1].split(":")[0]) for _, why in found]
        self.assertEqual(lines[0], 6)
        self.assertEqual([abs(line - 6) for line in lines], sorted(abs(line - 6) for line in lines))

    def test_without_lines_they_come_in_source_order(self):
        lines = [int(why.split("line ")[1].split(":")[0]) for _, why in singles(SOURCE, SYMBOL, set())]
        self.assertEqual(lines, sorted(lines))

    def test_random_rewrites_lean_toward_the_given_lines(self):
        rng = random.Random(0)
        near = 0
        for _ in range(200):
            _, [step] = _mutate(SOURCE, SYMBOL, rng, 1, frozenset({6}))
            near += abs(int(step.split("line ")[1].split(":")[0]) - 6) <= 2
        self.assertGreater(near, 140)


if __name__ == "__main__":
    unittest.main()
