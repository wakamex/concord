"""Authored source edits are scoped to one function and kept by the exact gate."""

import shutil
import unittest

from concord.authored import Edit, candidates, keep
from concord.harvest import Evaluation

SOURCE = b"""int Board::pick(int a)
{
    x.flagA = false;
    x.flagB = false;
    return a;
}
int Board::skip()
{
    x.flagA = false;
    x.flagB = false;
    return 0;
}
"""
SYMBOL = "_ZN5Board4pickEi"  # Board::pick(int)


def evaluation(exact: dict[str, bool], fuzzy: dict[str, float] | None = None, error: str = "") -> Evaluation:
    return Evaluation(
        "c",
        "u",
        error=error,
        sections=[{"functions": [{"symbol": s, "exact": e} for s, e in exact.items()]}],
        fuzzy=fuzzy or {},
    )


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class Candidates(unittest.TestCase):
    def test_the_edit_applies_inside_the_named_function_only(self):
        edits = [Edit("flag", b"x.flagA = false;\n    x.flagB = false;", b"x.flagA = false;\n    x.flagC = false;\n    x.flagB = false;")]
        found = candidates(SOURCE, SYMBOL, edits)
        self.assertEqual(len(found), 1)
        self.assertIn(b"x.flagC = false;\n    x.flagB = false;\n    return a;", found["flag"])
        self.assertNotIn(b"x.flagC", found["flag"].split(b"int Board::skip()")[1])

    def test_an_edit_needs_exactly_one_anchor_in_the_body(self):
        edits = [
            Edit("once", b"x.flagA = false;\n    x.flagB = false;", b"x.flagA = false;\n    x.flagC = false;\n    x.flagB = false;"),
            Edit("gone", b"return 9;", b"return 8;"),
            Edit("ambiguous", b"false;", b"true;"),
        ]
        self.assertEqual(list(candidates(SOURCE, SYMBOL, edits)), ["once"])


class Keep(unittest.TestCase):
    def test_keeps_exact_with_no_function_worse(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        after = evaluation({SYMBOL: True, "other": True}, {SYMBOL: 100.0, "other": 100.0})
        self.assertTrue(keep(before, after, SYMBOL))

    def test_rejects_errors_inexact_lost_exact_and_worse_scores(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": True}, error="boom"), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 99.0, "other": 100.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": False}, {SYMBOL: 100.0, "other": 50.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": True}, {SYMBOL: 100.0, "other": 99.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({"other": True}, {"other": 100.0}), SYMBOL))


if __name__ == "__main__":
    unittest.main()
