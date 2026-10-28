import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from concord.cli import main
from concord.flag_audit import audit, executable_code
from concord.harvest import Evaluation, Harvest


def evaluation(name, unit, exact, fuzzy):
    rows = [{"symbol": symbol, "exact": is_exact} for symbol, is_exact in exact.items()]
    return Evaluation(name, unit, sections=[{"functions": rows}], fuzzy=fuzzy)


class FlagAudit(unittest.TestCase):
    def test_executable_comparison_includes_relocation_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for target in ("foo", "bar"):
                source = root / f"{target}.s"
                source.write_text(f".text\n.globl f\n.type f,@function\nf:\n call {target}\n ret\n.size f, .-f\n")
                subprocess.run(["as", "-o", str(root / f"{target}.o"), str(source)], check=True)
            self.assertNotEqual(executable_code(root / "foo.o"), executable_code(root / "bar.o"))

    def test_debug_sections_do_not_change_the_executable_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "f.cpp"
            source.write_text("extern int g(); int f() { return g() + 1; }\n")
            for name, flags in (("normal", []), ("debug", ["-g"])):
                subprocess.run(["g++", "-O2", *flags, "-c", str(source), "-o", str(root / f"{name}.o")], check=True)
            self.assertNotEqual((root / "normal.o").read_bytes(), (root / "debug.o").read_bytes())
            self.assertEqual(executable_code(root / "normal.o"), executable_code(root / "debug.o"))

    def test_compares_exact_identities_even_when_the_total_is_unchanged(self):
        class Checkout:
            def evaluate_overlays(self, items, *, baseline_target_fallback):
                assert baseline_target_fallback
                self.items = items
                return {
                    "v0": evaluation("v0", "a", {"a": True, "b": True, "c": False}, {"c": 80.0}),
                    "v1": evaluation("v1", "a", {"a": True, "b": False, "c": True}, {"c": 100.0}),
                }

        checkout = Checkout()
        report = audit(checkout, ["a.cpp"], {"debug": ("-g",)})
        self.assertEqual(checkout.items, {"v0": ("a.cpp", {}, ()), "v1": ("a.cpp", {}, ("-g",))})
        baseline, variant = report["units"][0]["variants"]
        self.assertEqual(baseline["exact"], 2)
        self.assertEqual(baseline["exact_functions"], ["a", "b"])
        self.assertEqual(variant["exact"], 2)
        self.assertEqual(variant["lost_exact"], ["b"])
        self.assertEqual(variant["gained_exact"], ["c"])
        self.assertEqual(variant["scores"]["c"], 100.0)

    def test_compile_failure_does_not_look_like_zero_exact(self):
        class Checkout:
            def evaluate_overlays(self, items, *, baseline_target_fallback):
                assert baseline_target_fallback
                return {
                    "v0": evaluation("v0", "a", {"a": True}, {"a": 100.0}),
                    "v1": Evaluation("v1", "a", error="bad flag"),
                }

        report = audit(Checkout(), ["a.cpp"], {"bad": ("-fno-example",)})
        self.assertEqual(report["units"][0]["variants"][1], {"name": "bad", "flags": ["-fno-example"], "error": "bad flag"})

    def test_cli_reports_a_variant_without_scores(self):
        report = {"units": [{"unit": "a.cpp", "variants": [
            {"name": "baseline", "exact": 1, "lost_exact": [], "gained_exact": [], "scores": {"a": 100}},
            {"name": "alternate", "exact": 1, "lost_exact": [], "gained_exact": [], "scores": {}},
        ]}]}
        with tempfile.TemporaryDirectory() as tmp, patch("concord.cli.Harvest"), patch("concord.cli.flag_audit.audit", return_value=report):
            output = StringIO()
            with redirect_stdout(output):
                result = main(["flag-audit", "--harvest", tmp, "--unit", "a.cpp", "--variant", "alternate=-O3", "--save", f"{tmp}/out.json"])
        self.assertEqual(result, 1)
        self.assertIn("scores unavailable", output.getvalue())


@unittest.skipUnless(os.environ.get("CONCORD_HARVEST"), "CONCORD_HARVEST not set")
class HarvestFlagAudit(unittest.TestCase):
    def test_debug_and_fortify_variations_compile_and_are_scored(self):
        checkout = Harvest(Path(os.environ["CONCORD_HARVEST"]))
        report = audit(checkout, ["ox/io/CMemReadFile.cpp"], {"debug": ("-g",), "unfortified": ("-U_FORTIFY_SOURCE",)})
        for variant in report["units"][0]["variants"]:
            self.assertNotIn("error", variant)
            self.assertGreater(variant["exact"], 0)
            self.assertTrue(variant["scores"])
        self.assertTrue(report["units"][0]["variants"][1]["same_executable_as_baseline"])

    def test_unplaceable_variant_uses_the_baseline_target_for_scores(self):
        checkout = Harvest(Path(os.environ["CONCORD_HARVEST"]))
        report = audit(checkout, ["daisy/scene/CCameraMayaSceneNode.cpp"], {"opteron": ("-mtune=opteron",)})
        baseline, variant = report["units"][0]["variants"]
        self.assertEqual(baseline["score_basis"], "candidate-target")
        self.assertEqual(variant["score_basis"], "baseline-target")
        self.assertIn("_ZN5daisy5scene20CCameraMayaSceneNode7animateEv", variant["scores"])


if __name__ == "__main__":
    unittest.main()
