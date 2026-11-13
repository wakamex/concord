import tempfile
import unittest
from pathlib import Path

from concord.split_lever import closure, holds, moved, replace, users


def row(symbol, fuzzy, exact=None):
    return {"symbol": symbol, "address": "0x1", "fuzzy": fuzzy, "exact": fuzzy == 100.0 if exact is None else exact}


class Includes(unittest.TestCase):
    def test_closure_follows_angle_includes_through_headers_once(self):
        headers = {"A.h": b"#include <B.h>\n", "B.h": b"  #  include <A.h>\n#include <C.h>", "C.h": b""}
        found = closure(headers.get, b'#include <A.h>\n#include "Local.h"\n#include <windows.h>\n')
        self.assertEqual(found, {"A.h", "B.h", "C.h"})

    def test_users_are_the_units_whose_sources_reach_the_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "include/Gruntz").mkdir(parents=True)
            (root / "src").mkdir()
            (root / "include/Gruntz/State.h").write_bytes(b"")
            (root / "include/Gruntz/Attract.h").write_bytes(b"#include <Gruntz/State.h>\n")
            (root / "src/a.cpp").write_bytes(b"#include <Gruntz/Attract.h>\n")
            (root / "src/b.cpp").write_bytes(b"#include <windows.h>\n")
            self.assertEqual(users(root, {"a": "src/a.cpp", "b": "src/b.cpp"}, "include/Gruntz/State.h"), ["a"])


class Changes(unittest.TestCase):
    def test_replace_rewrites_every_occurrence_and_rejects_a_missing_one(self):
        self.assertEqual(replace(b"World()->a; World()->b;", [("World()->", "m_world->")], "f"), b"m_world->a; m_world->b;")
        with self.assertRaises(ValueError):
            replace(b"x", [("World()", "m_world")], "f")

    def test_moved_names_score_and_exactness_changes_only(self):
        base = [row("a", 99.5), row("b", 100.0), row("c", 90.0)]
        variant = [row("a", 100.0), row("b", 100.0), row("c", 90.00001)]
        self.assertEqual([(r["symbol"], r["base"], r["variant"], r["variant_exact"]) for r in moved(base, variant)], [("a", 99.5, 100.0, True)])

    def test_a_local_rewrite_holds_when_it_raises_its_function_and_breaks_no_exact_one(self):
        base = [row("a", 99.5), row("b", 100.0), row("c", 90.0)]
        self.assertTrue(holds(base, [row("a", 100.0), row("b", 100.0), row("c", 89.0)], "a")["holds"])
        broke = holds(base, [row("a", 100.0), row("b", 99.0), row("c", 90.0)], "a")
        self.assertEqual((broke["holds"], broke["broke"]), (False, ["b"]))
        self.assertFalse(holds(base, [row("a", 99.5), row("b", 100.0), row("c", 90.0)], "a")["holds"])


if __name__ == "__main__":
    unittest.main()
