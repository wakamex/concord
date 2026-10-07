"""Source rewrites on C++ parsed with tree-sitter."""

import shutil
import unittest

from concord.transforms import comparisons, find_function, swap_branches, swap_operands

SOURCE = b"""namespace game {
int Board::score(int a, int b) const
{
    if (a < b && b != 0) return 1;
    return a >= 3;
}
int Board::score(int a) const { return a == 2; }
}
"""
SYMBOL = "_ZNK4game5Board5scoreEii"  # game::Board::score(int, int) const


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class SwapOperands(unittest.TestCase):
    def test_finds_the_overload_with_the_symbols_arity(self):
        function = find_function(SOURCE, SYMBOL)
        self.assertIn(b"a < b", function.text)
        self.assertEqual(len(comparisons(function)), 3)

    def test_each_rewrite_swaps_one_comparison_and_keeps_the_rest(self):
        # b != 0 and a >= 3 compare with a constant, which GCC canonicalizes itself
        rewrites = list(swap_operands(SOURCE, SYMBOL))
        self.assertEqual(len(rewrites), 1)
        first = rewrites[0].source
        self.assertEqual(first.replace(b"b > a", b"a < b"), SOURCE)
        self.assertIn(b"return a == 2;", first)


BRANCHES = b"""int Board::pick(int a)
{
    if (!a) return 1; else { a++; }
    if (a < 2) { return 2; } else if (a > 9) return 3; else return 4;
    if (a) return 5;
    return 0;
}
"""


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class SwapBranches(unittest.TestCase):
    def test_each_if_else_swaps_with_a_negated_condition(self):
        rewrites = list(swap_branches(BRANCHES, "_ZN5Board4pickEi"))
        self.assertEqual(len(rewrites), 3)  # the if without an else is left alone
        self.assertIn(b"if (a) { a++; } else { return 1; }", rewrites[0].source)

    def test_a_moved_else_if_chain_is_braced(self):
        outer = list(swap_branches(BRANCHES, "_ZN5Board4pickEi"))[1].source
        self.assertIn(b"if (!(a < 2)) { if (a > 9) return 3; else return 4; } else { return 2; }", outer)


if __name__ == "__main__":
    unittest.main()
