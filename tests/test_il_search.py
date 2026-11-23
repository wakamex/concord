"""concord il-search: the capture files, the splice of an edited body, and the search itself.

The capture tests need nothing but a temporary directory. The checkout tests run when CONCORD_GRUNTZ names a
gruntz-decomp checkout after `gruntz build` (or one is at ../gruntz-decomp) and skip otherwise: they check that every
body of tileswitchlogic, brickz and namerecord gives back its own bytes, and that SwitchDown's two-edit fix is found
with the command's defaults.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from concord import il_search
from concord.gruntz import Gruntz
from concord.il import Statement, emit_body, parse

REPORT = Path("build/objdiff/compare-new/report.json")
SWITCH_DOWN = "?SwitchDown@CCheckpointTriggerSwitchLogic@@EAEHXZ"


def checkout() -> Path | None:
    root = Path(os.environ.get("CONCORD_GRUNTZ", Path(__file__).resolve().parents[2] / "gruntz-decomp")).resolve()
    return root if (root / REPORT).is_file() else None


def write_capture(directory: Path, bodies: dict[str, bytes]) -> None:
    """A capture of the given bodies, laid out as gruntz.capture_il writes it: ex holds the bodies in order after four
    bytes of padding, and gl holds each name with its body's offset as a 0x80-flagged 32-bit value."""
    ex = bytearray(b"\x00" * 4)
    gl = bytearray()
    for name, body in bodies.items():
        gl += name.encode() + b"\x00" + b"\x80" + len(ex).to_bytes(4, "little") + b"\x00"
        ex += body
    ex += b"\x00" * 4
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a01960ex").write_bytes(bytes(ex))
    (directory / "a01960gl").write_bytes(bytes(gl))
    (directory / "a01960sy").write_bytes(b"")
    (directory / "a01960in").write_bytes(b"")
    (directory / "flags.txt").write_text("-il x\n")


class CaptureTests(unittest.TestCase):
    def test_a_variant_moves_each_later_body_by_the_length_change(self) -> None:
        first, second = b"\x01" * 6, b"\x02" * 10
        with tempfile.TemporaryDirectory() as tmp:
            capture = il_search.Capture(Path(tmp) / "capture")
            write_capture(capture.directory, {"?First@@YAXXZ": first, "?Second@@YAXXZ": second})
            self.assertEqual(capture.function("?First@@YAXXZ"), (4, 10))
            self.assertEqual(capture.function("?Second@@YAXXZ"), (10, 24))  # to the end of ex, after 4 bytes of padding
            variant = capture.variant(Path(tmp) / "variant", 4, 10, b"\x01" * 9)
            moved = il_search.Capture(variant)
            self.assertEqual(moved.function("?First@@YAXXZ"), (4, 13))
            start, _ = moved.function("?Second@@YAXXZ")
            self.assertEqual(start, 13)
            self.assertEqual(moved.read("ex")[start : start + len(second)], second)

    def test_the_variant_keeps_every_other_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            capture = il_search.Capture(Path(tmp) / "capture")
            write_capture(capture.directory, {"?First@@YAXXZ": b"\x01" * 4})
            variant = capture.variant(Path(tmp) / "variant", 4, 8, b"\x03")
            self.assertEqual((variant / "flags.txt").read_text(), "-il x\n")
            self.assertEqual(sorted(p.name for p in variant.iterdir()), sorted(p.name for p in capture.directory.iterdir()))


@unittest.skipUnless(checkout(), "needs a gruntz-decomp checkout after `gruntz build` (CONCORD_GRUNTZ)")
class CheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = checkout()
        cls.gruntz = Gruntz(cls.root)

    def capture(self, unit: str) -> il_search.Capture:
        il, evaluation = self.gruntz.capture_il(unit, self.gruntz.source_path(unit).read_bytes())
        self.assertIsNotNone(il, evaluation.error[-500:])
        return il_search.Capture(il)

    def test_every_body_of_three_units_gives_back_its_bytes(self) -> None:
        accessors = il_search.trivial_accessors(self.root)
        for unit in ("tileswitchlogic", "brickz", "namerecord"):
            capture = self.capture(unit)
            symbols = capture.symbols(accessors)
            ex = capture.read("ex")
            bodies = capture.bodies()
            for name, start, end in bodies:
                body = ex[start:end]
                with self.subTest(unit=unit, symbol=name):
                    self.assertEqual(emit_body(parse(body, symbols)), body)
            self.assertGreater(sum(isinstance(item, Statement) for name, s, e in bodies for item in parse(ex[s:e], symbols)), 0)

    def test_the_checkout_has_the_accessors_the_fix_calls(self) -> None:
        accessors = il_search.trivial_accessors(self.root)
        self.assertTrue({"World", "GetLevel", "GetTileGrid"} <= accessors)

    def test_switchdown_is_fixed_by_two_edits_with_the_defaults(self) -> None:
        report = il_search.run(self.root, "tileswitchlogic", SWITCH_DOWN, depth=2, budget=400, beam=8, save=None, log=lambda line: None)
        self.assertTrue(report["found_exact"], report["best"])
        edits = report["exact"][0]["edits"]
        self.assertEqual(sorted(edit["kind"] for edit in edits), ["move", "replace"])
        replace = next(edit for edit in edits if edit["kind"] == "replace")
        move = next(edit for edit in edits if edit["kind"] == "move")
        self.assertIn("the use of `tileY` (line 1013)", replace["text"])
        self.assertIn("move `tileY = *((this + 12))` (line 1008) after `layer = ", move["text"])
        self.assertIn("(line 1010)", move["text"])
        self.assertLessEqual(report["replays"], 400)


if __name__ == "__main__":
    unittest.main()
