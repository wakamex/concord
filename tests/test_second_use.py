import unittest

from tree_sitter import Parser

from concord.harvest import Evaluation
from concord.second_use import (
    HELPER,
    costs_nothing,
    extra_grant,
    pointer_sites,
    wrapped,
)
from concord.transforms import CPP

ONE_MORE = "one more round-robin grant before this one than the original"


def grant(register, retail=None, path="round-robin", instruction="mov eax, dword ptr [edx + 0x198]", reading=None):
    return {"register": register, "retail": retail, "path": path, "instruction": instruction, "reading": reading}


def function(text):
    return Parser(CPP).parse(text).root_node.children[-1]


def evaluation(**scores):
    rows = [{"symbol": name, "fuzzy": score, "exact": score == 100.0} for name, score in scores.items()]
    return Evaluation("trial", "unit", sections=[{"functions": rows}])


class ExtraGrant(unittest.TestCase):
    def test_the_load_before_the_first_grant_a_step_ahead(self):
        load = grant("eax")
        rows = [grant("edx", instruction="mov edx, dword ptr [esi + 0x10]"), load, grant("ecx", "eax", reading=ONE_MORE)]
        self.assertIs(extra_grant(rows), load)

    def test_a_preferred_grant_is_passed_over(self):
        load = grant("eax")
        rows = [load, grant("ecx", path="preference"), grant("ecx", "eax", reading=ONE_MORE)]
        self.assertIs(extra_grant(rows), load)

    def test_a_grant_a_step_behind_retail_needs_another_change(self):
        rows = [grant("eax"), grant("ecx", "edx", reading="one fewer round-robin grant before this one than the original")]
        self.assertIsNone(extra_grant(rows))

    def test_the_earlier_grant_must_load_a_pointer(self):
        for instruction in ("lea eax, [esp + 0x10]", "mov eax, dword ptr [esp + 4]", "xor eax, eax", None):
            self.assertIsNone(extra_grant([grant("eax", instruction=instruction), grant("ecx", "eax", reading=ONE_MORE)]))

    def test_no_differing_grant(self):
        self.assertIsNone(extra_grant([grant("eax"), grant("ecx")]))


class Sites(unittest.TestCase):
    def texts(self, source, lines=None):
        return [source[a:b] for a, b in pointer_sites(function(source), lines)]

    def test_what_an_arrow_reads_through(self):
        source = b"void f(S* s) {\n  s->n->a = 1;\n}\n"
        self.assertEqual(self.texts(source), [b"s", b"s->n"])

    def test_this_is_not_a_site(self):
        self.assertEqual(self.texts(b"void f() { this->a = 1; }\n"), [])

    def test_a_macro_argument_is_a_site(self):
        source = b"void f() {\n  BIG_MACRO(m_object->GetImage())\n}\n"
        self.assertIn(b"m_object->GetImage()", self.texts(source))

    def test_lines_limit_the_sites(self):
        source = b"void f(S* s, S* t) {\n  s->a = 1;\n  t->a = 2;\n}\n"
        self.assertEqual(self.texts(source, {3}), [b"t"])


class Wrapped(unittest.TestCase):
    SOURCE = b"#include <a.h>\n#include <b.h>\n\nvoid f(S* s) { s->n->a = 1; }\n"

    def test_the_helper_follows_the_includes_and_the_expression_passes_through_it(self):
        at = self.SOURCE.index(b"s->n")
        text = wrapped(self.SOURCE, (at, at + 4))
        self.assertIn(b"CheckedPointer(s->n)->a = 1;", text)
        self.assertLess(text.index(b"#include <b.h>"), text.index(b"template <class T>"))
        self.assertLess(text.index(b"template <class T>"), text.index(b"void f"))

    def test_the_helper_is_defined_once(self):
        at = self.SOURCE.index(b"s->n")
        first = wrapped(self.SOURCE, (at, at + 1))
        self.assertIn(HELPER, first)
        at = first.index(b"void f(S* s)") + len(b"void f(S* s) { ")
        self.assertEqual(wrapped(first, (at, at + 1)).count(b"template <class T>"), 1)


class Cost(unittest.TestCase):
    def test_a_lower_score_or_a_lost_match_is_a_cost(self):
        base = evaluation(a=100.0, b=90.0)
        self.assertTrue(costs_nothing(base, evaluation(a=100.0, b=95.0)))
        self.assertFalse(costs_nothing(base, evaluation(a=99.0, b=95.0)))
        self.assertFalse(costs_nothing(base, evaluation(a=100.0, b=80.0)))
        self.assertFalse(costs_nothing(base, evaluation(a=100.0)))


if __name__ == "__main__":
    unittest.main()
