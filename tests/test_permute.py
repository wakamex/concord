"""Target selection and candidate order for the permute search."""

import random
import unittest

from concord.permute import _mutate, drop_twins, singles

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
