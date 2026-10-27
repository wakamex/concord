"""Authored source edits are measured before writing."""

import shutil
import tempfile
import unittest
from pathlib import Path

from concord import authored
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

    def test_every_matching_edit_also_applies_together(self):
        edits = [
            Edit("one", b"x.flagA = false;", b"x.flagA = true;"),
            Edit("two", b"x.flagB = false;", b"x.flagB = true;"),
            Edit("gone", b"return 9;", b"return 8;"),
        ]
        found = candidates(SOURCE, SYMBOL, edits)
        self.assertIn("one+two", found)
        self.assertIn(b"x.flagA = true;\n    x.flagB = true;", found["one+two"])
        self.assertNotIn(b"x.flagA = true", found["two"])
        self.assertNotIn("one+two+gone", found)


class Keep(unittest.TestCase):
    def test_keeps_exact_with_no_function_worse(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        after = evaluation({SYMBOL: True, "other": True}, {SYMBOL: 100.0, "other": 100.0})
        self.assertTrue(keep(before, after, SYMBOL))

    def test_keeps_an_improved_inexact_candidate(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        after = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 95.0, "other": 100.0})
        self.assertTrue(keep(before, after, SYMBOL))

    def test_rejects_errors_unimproved_lost_exact_and_worse_scores(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": True}, error="boom"), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 89.0, "other": 100.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": False}, {SYMBOL: 95.0, "other": 100.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 95.0, "other": 99.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": False}, {SYMBOL: 100.0, "other": 50.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": True}, {SYMBOL: 100.0, "other": 99.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({"other": True}, {"other": 100.0}), SYMBOL))


class HeaderConstant(unittest.TestCase):
    def test_replaces_only_the_named_enumerator_value(self):
        source = b'''// CG_PROGRAM = 4125
enum CGenum { CG_SOURCE = 4112, CG_PROGRAM = 4125, CG_OBJECT = 4113 };
const char* example = "CG_PROGRAM = 4125";
'''
        edit = authored.ConstantEdit("namespace", "daisy/video/CgApi.h", "CG_PROGRAM", b"4125", b"4109")
        result = authored.constant_candidate(source, edit)
        self.assertEqual(result, source.replace(b"CG_PROGRAM = 4125, CG_OBJECT", b"CG_PROGRAM = 4109, CG_OBJECT"))
        self.assertIsNone(authored.constant_candidate(source.replace(b"CG_PROGRAM = 4125,", b"CG_PROGRAM = 1,"), edit))
        self.assertIsNone(authored.constant_candidate(source + b"enum Other { CG_PROGRAM = 4125 };\n", edit))
        self.assertIsNone(authored.constant_candidate(b"const int CG_PROGRAM = 4125;\n", edit))

    def test_partial_gain_requires_no_lost_or_lower_function(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        improved = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 91.0, "other": 100.0})
        self.assertTrue(authored.keep_improvement(before, improved, SYMBOL))
        self.assertTrue(authored.keep_improvement(before, evaluation({SYMBOL: True, "other": True}, {SYMBOL: 90.0, "other": 100.0}), SYMBOL))
        self.assertFalse(authored.keep_improvement(before, before, SYMBOL))
        self.assertFalse(authored.keep_improvement(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 91.0, "other": 99.0}), SYMBOL))
        self.assertFalse(authored.keep_improvement(before, evaluation({SYMBOL: False, "other": False}, {SYMBOL: 91.0, "other": 100.0}), SYMBOL))
        self.assertFalse(authored.keep_improvement(before, evaluation({SYMBOL: False}, {SYMBOL: 91.0}), SYMBOL))
        self.assertFalse(authored.keep_improvement(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 91.0, "other": 100.0}, error="boom"), SYMBOL))

    def test_header_write_requires_all_dependent_units_to_preserve_scores(self):
        unit, dependent = "d/a.cpp", "d/b.cpp"
        edit = authored.ConstantEdit("namespace", "d/constants.h", "CG_PROGRAM", b"4125", b"4109")

        class Checkout:
            def __init__(self, root, regress):
                self.root = root
                self.regress = regress
                self.items = {}

            def dependents(self, path):
                self_path = "src/d/constants.h"
                if path != self_path:
                    raise AssertionError(path)
                return [unit, dependent]

            def evaluate_overlays(self, items):
                self.items = items
                result = {}
                for name, (source_unit, overlays) in items.items():
                    if source_unit == unit:
                        result[name] = evaluation({SYMBOL: False}, {SYMBOL: 91.0 if overlays else 90.0})
                    else:
                        score = 99.0 if overlays and self.regress else 100.0
                        result[name] = evaluation({"other": True}, {"other": score})
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "src/d/constants.h"
            path.parent.mkdir(parents=True)
            source = b"enum CGenum { CG_PROGRAM = 4125 };\n"
            path.write_bytes(source)
            checkout = Checkout(root, regress=True)
            report = authored.apply_header(checkout, unit, SYMBOL, edit, write=True)
            self.assertFalse(report["applied"])
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(len(checkout.items), 4)

            checkout.regress = False
            report = authored.apply_header(checkout, unit, SYMBOL, edit, write=False)
            self.assertEqual(report["chosen"], "namespace")
            self.assertEqual(path.read_bytes(), source)
            report = authored.apply_header(checkout, unit, SYMBOL, edit, write=True)
            self.assertTrue(report["applied"])
            self.assertEqual(path.read_bytes(), b"enum CGenum { CG_PROGRAM = 4109 };\n")


if __name__ == "__main__":
    unittest.main()
