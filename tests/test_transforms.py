"""Source rewrites on C++ parsed with tree-sitter."""

import shutil
import unittest

from concord.transforms import comparisons, find_function, move_declarations, name_temporaries, swap_branches, swap_operands, swap_statements

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
        rewrites = list(swap_operands(SOURCE, find_function(SOURCE, SYMBOL)))
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
        rewrites = list(swap_branches(BRANCHES, find_function(BRANCHES, "_ZN5Board4pickEi")))
        self.assertEqual(len(rewrites), 3)  # the if without an else is left alone
        self.assertIn(b"if (a) { a++; } else { return 1; }", rewrites[0].source)

    def test_a_moved_else_if_chain_is_braced(self):
        outer = list(swap_branches(BRANCHES, find_function(BRANCHES, "_ZN5Board4pickEi")))[1].source
        self.assertIn(b"if (!(a < 2)) { if (a > 9) return 3; else return 4; } else { return 2; }", outer)


STATEMENTS = b"""int Board::sum(int a)
{
    int x = a + 1;
    int y = a * 2;
    int z = x + y;
    int t;
    call(a);
    t = z;
    return t;
}
"""


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class Reorder(unittest.TestCase):
    def setUp(self):
        self.scope = find_function(STATEMENTS, "_ZN5Board3sumEi")

    def test_only_independent_neighbours_swap(self):
        rewrites = list(swap_statements(STATEMENTS, self.scope))
        # every neighbour pair but y and z, since z reads y
        self.assertEqual([r.description.split(":")[0] for r in rewrites], ["line 3", "line 5", "line 6", "line 7"])
        self.assertIn(b"    int y = a * 2;\n    int x = a + 1;\n", rewrites[0].source)

    def test_a_declaration_moves_up_to_its_first_use(self):
        moved = [r.source for r in move_declarations(STATEMENTS, self.scope)]
        self.assertEqual(len(moved), 4)  # before x, y, z, and after call(a)
        self.assertTrue(moved[0].startswith(b"int Board::sum(int a)\n{\n    int t;\n    int x = a + 1;"))
        self.assertIn(b"    call(a);\n    int t;\n    t = z;", moved[-1])
        for source in moved:
            self.assertEqual(source.count(b"int t;"), 1)
            self.assertEqual(len(source), len(STATEMENTS))


TEMPORARIES = b"""int Board::sum(int a)
{
    int x = a + b.c;
    call(x * 2, a && f(a));
    b.c = x[1];
    return x;
}
"""


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class NameTemporaries(unittest.TestCase):
    def test_values_are_named_but_not_written_places_or_conditional_operands(self):
        rewrites = list(name_temporaries(TEMPORARIES, find_function(TEMPORARIES, "_ZN5Board3sumEi")))
        # not the call statement itself, f(a) behind &&, or the assigned b.c
        self.assertEqual([r.description for r in rewrites], ["line 4: x * 2 named", "line 5: x[1] named", "line 3: a + b.c named", "line 3: b.c named"])
        self.assertIn(b"    __typeof__(x * 2) concordTmp0 = x * 2;\n    call(concordTmp0, a && f(a));", rewrites[0].source)


if __name__ == "__main__":
    unittest.main()
