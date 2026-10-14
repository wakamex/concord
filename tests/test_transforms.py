"""Source rewrites on C++ parsed with tree-sitter."""

import shutil
import unittest

from tree_sitter import Parser

from concord.transforms import (
    CPP,
    _negate,
    comparisons,
    compress_assignment,
    expand_assignment,
    find_function,
    flip_nan_sense,
    for_loop,
    inline_temporaries,
    merge_conditions,
    move_declarations,
    name_temporaries,
    nest_condition,
    swap_branches,
    swap_operands,
    swap_statements,
    while_loop,
)

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


LAYOUTS = b"""int Board::lay(int a, int b)
{
    if (!DrawBack)
    {
        drawChildren();
    }
    else
        IGUIElement::draw();

    if (has(a) == true &&
        has(b))
    {
        a = b;
    }
    else
        reset(a);

    if (a > 0) {
        a--;
    } else {
        a++;
    }
    return a;
}
"""


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class SwapBranches(unittest.TestCase):
    def rewrites(self, source, symbol):
        return [r.source for r in swap_branches(source, find_function(source, symbol))]

    def test_one_line_ifs_stay_on_one_line(self):
        rewrites = self.rewrites(BRANCHES, "_ZN5Board4pickEi")
        self.assertEqual(len(rewrites), 3)  # the if without an else is left alone
        self.assertIn(b"    if (a) { a++; } else return 1;\n", rewrites[0])

    def test_a_moved_else_if_chain_becomes_a_block(self):
        outer = self.rewrites(BRANCHES, "_ZN5Board4pickEi")[1]
        self.assertIn(b"    if (!(a < 2)) { if (a > 9) return 3; else return 4; } else { return 2; }\n", outer)

    def test_branches_keep_the_files_layout(self):
        first, second, third = self.rewrites(LAYOUTS, "_ZN5Board3layEii")
        self.assertIn(b"    if (DrawBack)\n        IGUIElement::draw();\n    else\n    {\n        drawChildren();\n    }\n", first)
        self.assertIn(b"    if (has(a) != true ||\n        !has(b))\n        reset(a);\n    else\n    {\n        a = b;\n    }\n", second)
        self.assertIn(b"    if (!(a > 0)) {\n        a++;\n    } else {\n        a--;\n    }\n", third)

    def test_an_else_if_keeps_its_elses_indent(self):
        source = b"""int Board::chain(int a)
{
    if (a == 1)
        a = 2;
    else if (a == 3)
        a = 4;
    else if (a == 5)
    {
        a = 6;
    }
    return a;
}
"""
        swapped = self.rewrites(source, "_ZN5Board5chainEi")[1]  # the inner if: a == 3
        self.assertIn(
            b"    else if (a != 3)\n    {\n        if (a == 5)\n        {\n            a = 6;\n        }\n    }\n    else\n        a = 4;\n",
            swapped,
        )


class Negate(unittest.TestCase):
    def negated(self, condition: bytes) -> bytes:
        tree = Parser(CPP).parse(b"bool f() { return " + condition + b"; }")
        node = tree.root_node.child(0).child_by_field_name("body").named_children[0].named_children[0]
        return _negate(node, tree.root_node.text)

    def test_negations_read_as_written_by_hand(self):
        cases = {
            b"x": b"!x",
            b"!x": b"x",
            b"!(a && b)": b"a && b",
            b"a.size() < n": b"!(a.size() < n)",  # inverted only where GCC proves it the same
            b"a != b": b"a == b",
            b"a == b && c": b"a != b || !c",
            b"a || b && c": b"!a && (!b || !c)",
            b"(a || b) && c": b"!a && !b || !c",
            b"a + b": b"!(a + b)",
        }
        for condition, expected in cases.items():
            self.assertEqual(self.negated(condition), expected, condition)


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


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class FlipNanSense(unittest.TestCase):
    def test_a_single_use_temporary_is_inlined_into_the_next_statement(self):
        source = (
            b"int f(int a, int b)\n{\n    int kept = a - b;\n    use(kept);\n    use(kept);\n"
            b"    int sum = a + b;\n    int twice = sum * 2;\n"
            b"    int out = twice;\n    out++;\n"
            b"    int flag = a > b;\n    if (b && flag)\n        return 0;\n"
            b"    int last = g(a);\n    return last;\n}\n"
        )
        scope = Parser(CPP).parse(source).root_node
        rewrites = {r.description: r.source for r in inline_temporaries(source, scope)}
        self.assertEqual(
            sorted(rewrites),
            ["line 13: last inlined into its one use", "line 6: sum inlined into its one use", "line 7: twice inlined into its one use"],
        )
        self.assertIn(b"    use(kept);\n    int twice = (a + b) * 2;\n", rewrites["line 6: sum inlined into its one use"])
        self.assertIn(b"    int out = sum * 2;\n    out++;", rewrites["line 7: twice inlined into its one use"])
        self.assertIn(b"        return 0;\n    return g(a);\n}", rewrites["line 13: last inlined into its one use"])

    def test_inlining_undoes_naming(self):
        source = b"void f(int a, int b)\n{\n    use(a + b);\n}\n"
        scope = Parser(CPP).parse(source).root_node
        named = next(r.source for r in name_temporaries(source, scope) if b"= a + b;" in r.source)
        [inlined] = inline_temporaries(named, Parser(CPP).parse(named).root_node)
        self.assertEqual(inlined.source, source)

    def test_a_while_loop_becomes_a_for_loop_and_back(self):
        source = b"int f(int n)\n{\n    int sum = 0;\n    int i = 0;\n    while (i < n)\n    {\n        sum += i;\n        ++i;\n    }\n    return sum;\n}\n"
        scope = Parser(CPP).parse(source).root_node
        [rewrite] = for_loop(source, scope)
        self.assertEqual(rewrite.description, "line 4: while loop as a for loop")
        self.assertIn(b"    for (int i = 0; i < n; ++i) {\n        sum += i;\n    }\n    return sum;", rewrite.source)
        [back] = while_loop(rewrite.source, Parser(CPP).parse(rewrite.source).root_node)
        self.assertIn(b"{ int i = 0; while (i < n) {\n        sum += i;\n++i;\n} }", back.source)

    def test_a_loop_whose_body_continues_or_does_not_end_in_a_step_stays_a_while(self):
        for body in (b"if (i) continue; ++i;", b"if (i) return 1;"):
            source = b"int f(int n)\n{\n    int i = 0;\n    while (i < n)\n    {\n        " + body + b"\n    }\n    return 0;\n}\n"
            self.assertEqual(list(for_loop(source, Parser(CPP).parse(source).root_node)), [])

    def test_compound_assignments_expand_and_compress(self):
        source = b"void f(int a, int b)\n{\n    a += b - 1;\n    b = b * a;\n    a = b + a;\n}\n"
        scope = Parser(CPP).parse(source).root_node
        self.assertEqual([r.description for r in expand_assignment(source, scope)], ["line 3: a += b - 1 spelled out"])
        self.assertIn(b"    a = a + (b - 1);\n", next(expand_assignment(source, scope)).source)
        [compressed] = compress_assignment(source, scope)  # a = b + a does not start with its target
        self.assertIn(b"    b *= a;\n", compressed.source)

    def test_nested_ifs_merge_and_an_and_condition_nests(self):
        source = b"void f(int a, int b, int c)\n{\n    if (a)\n    {\n        if (b || c)\n            g();\n    }\n    if (a && b)\n        h();\n    if (a)\n    {\n        if (b)\n            g();\n        else\n            h();\n    }\n}\n"
        scope = Parser(CPP).parse(source).root_node
        [merged] = merge_conditions(source, scope)  # the last pair has an else
        self.assertIn(b"    if (a && (b || c)) g();\n", merged.source)
        [nested] = nest_condition(source, scope)
        self.assertIn(b"    if (a) { if (b) h(); }\n", nested.source)

    def test_relational_comparisons_change_their_nan_sense_and_equality_is_left_alone(self):
        source = b"int Board::f(float t)\n{\n    if (!(t > 4.0f)) return 1;\n    if (t <= 2.0f) return 2;\n    if (t == 3.0f) return 3;\n    return 0;\n}\n"
        rewrites = [r.source for r in flip_nan_sense(source, find_function(source, "_ZN5Board1fEf"))]
        self.assertEqual(len(rewrites), 2)
        self.assertIn(b"    if (t <= 4.0f) return 1;\n", rewrites[0])
        self.assertIn(b"    if (!(t > 2.0f)) return 2;\n", rewrites[1])


if __name__ == "__main__":
    unittest.main()
