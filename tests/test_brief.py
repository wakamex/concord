import tempfile
import unittest
from pathlib import Path

from concord.brief import excerpt, render


class Render(unittest.TestCase):
    def test_sites_come_with_their_causes_source_and_check_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "u.cpp").write_text("\n".join(f"line {n}" for n in range(1, 21)) + "\n")
            row = {
                "unit": "u", "symbol": "f", "bytes": 100, "score": 95.0, "differences": 10, "fallout": {"register choices": 6},
                "primaries": [
                    {"kind": "operation", "detail": "retail has `and` where ours has `xor`", "file": "src/u.cpp", "line": 10},
                    {"kind": "constant", "detail": "retail has `8` where ours has `4`", "file": "src/u.cpp", "line": 11},
                    {"kind": "calls", "detail": "1 more calls to g than retail", "file": None, "line": None},
                ],
                "sites": [[0, 1], [2]],
            }
            text = render(row, root, "msvc-5.0", "--gruntz ROOT", context=1)
        self.assertIn("3 primary causes at 2 sites; 10 differing rows, 6 of them fallout", text)
        self.assertIn("site 1: src/u.cpp line 10 to 11\n  operation: retail has `and`", text)
        self.assertIn(">    10  line 10\n  >    11  line 11\n       12  line 12", text)
        self.assertIn("site 2: no source line\n  calls: 1 more calls to g than retail", text)
        self.assertIn("concord causes --gruntz ROOT --unit u 'f'", text)

    def test_a_register_only_function_is_flagged(self):
        row = {"unit": "u", "symbol": "f", "bytes": 1, "score": 99.0, "differences": 2, "fallout": {},
               "primaries": [{"kind": "register-allocation", "detail": "", "file": None, "line": None}], "sites": [[0]]}
        text = render(row, Path("."), "msvc-5.0", "--gruntz ROOT")
        self.assertIn("only register allocation differs", text)
        self.assertIn("known msvc-5.0 idioms for these kinds of cause:\n  statement-order", text)

    def test_excerpt_stays_inside_the_file(self):
        self.assertEqual(excerpt(["a", "b"], {1}, 3), [">     1  a", "      2  b"])


if __name__ == "__main__":
    unittest.main()
