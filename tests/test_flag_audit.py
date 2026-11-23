import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from concord.cli import main
from concord.flag_audit import (
    Variant,
    audit,
    default_variants,
    effective_flags,
    executable_code,
    gruntz_audit,
    gruntz_unit,
    parse_variant,
    summary,
)
from concord.gruntz import Gruntz
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


CONFIGURED = ["/nologo", "/c", "/O2", "/MT", "/GX", "/GR"]
UNITS_TOML = """[flags]
cpp = ["/nologo", "/c", "/O2", "/MT", "/GX"]
cpp-rtti = ["/nologo", "/c", "/O2", "/MT", "/GX", "/GR"]

[[unit]]
unit = "a"
source = "src/a.cpp"
flags = "cpp"

[[unit]]
unit = "b"
source = "src/b.cpp"
flags = "cpp-rtti"
"""
BINDINGS = "rva\tsize\tkind\tspace\tname\tunit\tchannel\talso_units\taliases\n0x00001000\t0x5\t\ttext\t?f@@YAXXZ\ta\tsrc\t\t\n"


def make_checkout(root: Path, report: dict) -> None:
    """The files Gruntz reads from a built gruntz-decomp checkout, with the given report."""
    (root / "build/objdiff/compare-new").mkdir(parents=True)
    (root / "build/objdiff/compare-new/report.json").write_text(json.dumps(report))
    (root / "build/gen").mkdir(parents=True)
    (root / "build/gen/bindings.tsv").write_text(BINDINGS)
    (root / "config").mkdir()
    (root / "config/units.toml").write_text(UNITS_TOML)
    (root / "config/match_baseline.tsv").write_text("# gruntz match baseline\n")


def scripted_compile(name: str, flags: list[str]):
    """Symbol -> (exact, fuzzy) for a candidate, or a compiler error. The baseline has a exact and b, c open; ox makes b exact
    and loses a; ob0 fails to compile; every other variant compiles as the baseline does."""
    if name == "baseline":
        return {"a": (True, 100.0), "b": (False, 50.0), "c": (False, 20.0)}
    if name == "ox":
        return {"a": (False, 99.0), "b": (True, 100.0), "c": (False, 30.0)}
    if name == "ob0":
        return "cl: bad option"
    return {"a": (True, 100.0), "b": (False, 50.0), "c": (False, 20.0)}


def scripted_replay(text: str):
    """The c2.exe result for a flags file: -G6 gives c one point more score than the baseline's, and -G5 is the baseline."""
    if "-G6" in text:
        return {"a": (True, 100.0), "b": (False, 50.0), "c": (False, 25.0)}
    return {"a": (True, 100.0), "b": (False, 50.0), "c": (False, 20.0)}


class ScriptedGruntz:
    """A gruntz-decomp target whose compiles and replays are scripted, with no checkout behind them."""

    def __init__(self, root: Path, compile=scripted_compile, replay=scripted_replay, report=("a",), capture_ok=True):
        self.root = root
        self.compile, self.replay, self.capture_ok = compile, replay, capture_ok
        self.report = set(report)
        self.evaluated: list[tuple[str, tuple[str, ...]]] = []
        self.discarded: list[Path] = []
        root.mkdir(parents=True, exist_ok=True)
        (root / "unit.cpp").write_bytes(b"int f;\n")
        make_checkout(root, {"units": [{"name": "a", "functions": []}]})

    def slug(self, unit):
        return unit

    def configured_flags(self, unit):
        return list(CONFIGURED)

    def source_path(self, unit):
        return self.root / "unit.cpp"

    def report_exact(self, unit):
        return set(self.report)

    def open_units(self):
        return ["a"]

    def evaluate(self, unit, sources, flags=None, jobs=None):
        found = {}
        for name in sources:
            self.evaluated.append((name, tuple(flags[name])))
            found[name] = self._evaluation(self.compile(name, flags[name]))
        return found

    def _evaluation(self, result):
        work = self.root / "work"
        if isinstance(result, str):
            return SimpleNamespace(error=result, sections=[{"functions": [], "retail": str(work / "retail.obj")}])
        rows = [{"symbol": s, "exact": exact, "fuzzy": fuzzy} for s, (exact, fuzzy) in result.items()]
        return SimpleNamespace(error="", sections=[{"functions": rows, "retail": str(work / "retail.obj")}])

    def capture_il(self, unit, source):
        il = self.root / "capture/il"
        il.mkdir(parents=True, exist_ok=True)
        (il / "flags.txt").write_text("-il C:\\a -W 1 -G5 -Gy -MT -EHs -Fo x.obj")
        if not self.capture_ok:
            return None, SimpleNamespace(error="cl failed")
        return il, SimpleNamespace(error="")

    def edited_il(self, unit, il, replacements):
        copy = self.root / f"edit{len(self.discarded)}-{len(list(self.root.iterdir()))}/il"
        shutil.copytree(il, copy)
        tokens = (copy / "flags.txt").read_text().split(" ")
        for old, new in replacements:
            tokens[tokens.index(old)] = new
        (copy / "flags.txt").write_text(" ".join(tokens))
        return copy

    def replay_many(self, unit, il_dirs, jobs):
        return {name: self._evaluation(self.replay((il / "flags.txt").read_text())) for name, il in il_dirs.items()}

    def discard(self, path):
        self.discarded.append(path)


class ParseAndDefaults(unittest.TestCase):
    def test_variant_words_add_remove_and_replace_c2_options(self):
        self.assertEqual(parse_variant("ox=!/O2 /Ox"), Variant("ox", remove=("/O2",), add=("/Ox",)))
        self.assertEqual(parse_variant("c2-g6=c2:-G5=-G6"), Variant("c2-g6", codegen=(("-G5", "-G6"),)))

    def test_malformed_variants_are_refused(self):
        for spec in ("ox", "baseline=/Ox", "ox=", "a b=/Ox", "x=c2:-G5=-G6 /Ox", "x=c2:-G5", "x=!"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_variant(spec)

    def test_removed_option_must_be_configured(self):
        self.assertEqual(effective_flags(CONFIGURED, parse_variant("ox=!/O2 /Ox")), ["/nologo", "/c", "/MT", "/GX", "/GR", "/Ox"])
        with self.assertRaisesRegex(ValueError, "/O1 is not among"):
            effective_flags(CONFIGURED, parse_variant("x=!/O1"))

    def test_default_set_toggles_only_the_switches_the_unit_lacks(self):
        names = {v.name: v for v in default_variants(["/nologo", "/c", "/O2", "/MT", "/GX"])}
        self.assertIn("no-gx", names)
        self.assertEqual(names["gr"].add, ("/GR",))
        self.assertEqual(names["c2-g6"].codegen, (("-G5", "-G6"),))
        self.assertEqual(len({v.name for v in default_variants(CONFIGURED)}), len(default_variants(CONFIGURED)))
        for variant in default_variants(CONFIGURED):
            self.assertTrue(set(variant.remove) <= set(CONFIGURED))


class GruntzUnitAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_unit(self, specs, **kwargs):
        target = ScriptedGruntz(self.root, **kwargs)
        variants = [parse_variant(spec) for spec in specs]
        return target, gruntz_unit(target, "a", variants, jobs=4)

    def test_gains_losses_and_raises_come_from_the_configured_compile(self):
        _, entry = self.run_unit(["ox=!/O2 /Ox", "gy=/Gy"])
        self.assertEqual(entry["baseline"]["exact"], 1)
        self.assertTrue(entry["baseline"]["matches_report"])
        ox, gy = entry["variants"]
        self.assertEqual(ox["gained_exact"], ["b"])
        self.assertEqual(ox["lost_exact"], ["a"])
        self.assertEqual(ox["raised_by_1"], 2)
        self.assertEqual(ox["largest_gains"][0], {"symbol": "b", "from": 50.0, "to": 100.0})
        self.assertEqual((gy["gained_exact"], gy["lost_exact"], gy["raised_by_1"]), ([], [], 0))

    def test_a_failed_compile_is_recorded_once_and_a_bad_removal_never_compiles(self):
        target, entry = self.run_unit(["ob0=/Ob0", "bad=!/nope /Ox"])
        ob0, bad = entry["variants"]
        self.assertEqual(ob0["error"], "cl: bad option")
        self.assertIn("/nope is not among", bad["error"])
        self.assertEqual([name for name, _ in target.evaluated].count("ob0"), 1)
        self.assertNotIn("bad", [name for name, _ in target.evaluated])

    def test_a_c2_variant_replays_the_capture_with_its_option_replaced(self):
        _, entry = self.run_unit(["c2-g6=c2:-G5=-G6"])
        self.assertEqual(entry["replay_control"], {"matches_configured": True})
        (variant,) = entry["variants"]
        self.assertEqual(variant["codegen"], [["-G5", "-G6"]])
        self.assertEqual(variant["flags"], CONFIGURED)
        self.assertEqual(variant["raised_by_1"], 1)
        self.assertEqual(variant["largest_gains"], [{"symbol": "c", "from": 20.0, "to": 25.0}])

    def test_a_failed_capture_is_recorded_for_each_c2_variant(self):
        _, entry = self.run_unit(["c2-g6=c2:-G5=-G6"], capture_ok=False)
        error = entry["variants"][0]["error"]
        self.assertTrue(error.startswith("the capture wrote no intermediate language: cl failed"), error)

    def test_work_directories_are_discarded(self):
        target, _ = self.run_unit(["gy=/Gy", "c2-g6=c2:-G5=-G6"])
        self.assertIn(self.root / "work", target.discarded)
        self.assertIn(self.root / "capture", target.discarded)

    def test_summary_names_each_gain_loss_raise_and_failure(self):
        _, entry = self.run_unit(["ox=!/O2 /Ox", "ob0=/Ob0"])
        self.assertEqual(
            summary(entry),
            "a: 1/3 exact at the configured flags; gains ox +1; losses ox -1; raised ox (2); failed ob0",
        )

    def test_units_with_finished_entries_are_not_compiled_again(self):
        target = ScriptedGruntz(self.root)
        variants_for = lambda configured: [parse_variant("gy=/Gy")]
        first = gruntz_audit(target, ["a"], variants_for, jobs=4)
        compiled = len(target.evaluated)
        recorded = []
        again = gruntz_audit(target, ["a"], variants_for, jobs=4, finished={"a": first["units"][0]}, record=recorded.append)
        self.assertEqual(len(target.evaluated), compiled)
        self.assertEqual(recorded, [])
        self.assertEqual(again["units"], first["units"])

    def test_no_unit_starts_after_the_budget(self):
        target = ScriptedGruntz(self.root)
        result = gruntz_audit(target, ["a", "a2"], lambda configured: [parse_variant("gy=/Gy")], jobs=4, budget=0)
        self.assertEqual(result["units"], [])
        self.assertEqual(result["remaining"], ["a", "a2"])
        self.assertEqual(target.evaluated, [])


class GruntzCheckoutReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_checkout(self.root, {"units": [
            {"name": "a", "functions": [{"name": "x", "fuzzy_match_percent": 100.0}, {"name": "__ehreg$f", "fuzzy_match_percent": 0.0}]},
            {"name": "src/b", "functions": [{"name": "y", "fuzzy_match_percent": 99.5}]},
        ]})
        self.target = Gruntz(self.root)

    def test_open_units_are_those_with_a_function_below_100_and_funclets_do_not_count(self):
        self.assertEqual(self.target.open_units(), ["b"])

    def test_configured_flags_are_the_units_profile(self):
        self.assertEqual(self.target.configured_flags("b"), ["/nologo", "/c", "/O2", "/MT", "/GX", "/GR"])

    def test_edited_il_replaces_each_option_once_and_leaves_the_capture_alone(self):
        il = self.root / "capture/il"
        il.mkdir(parents=True)
        (il / "flags.txt").write_text("-W 1 -G5 -Gy -MT")
        copy = self.target.edited_il("b", il, [("-G5", "-G6")])
        self.assertEqual((copy / "flags.txt").read_text(), "-W 1 -G6 -Gy -MT")
        self.assertEqual((il / "flags.txt").read_text(), "-W 1 -G5 -Gy -MT")
        with self.assertRaisesRegex(ValueError, "not once"):
            self.target.edited_il("b", il, [("-G9", "-G6")])
        self.target.discard(copy.parent)
        self.assertFalse(copy.parent.exists())

    def test_discard_refuses_paths_outside_build_concord(self):
        with self.assertRaises(ValueError):
            self.target.discard(self.root / "src")


class GruntzFlagAuditCli(unittest.TestCase):
    def test_harvest_and_gruntz_are_exclusive_and_all_open_needs_gruntz(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                main(["flag-audit", "--harvest", tmp, "--gruntz", tmp, "--unit", "a", "--variant", "x=/Ox", "--save", f"{tmp}/o.json"])
            with self.assertRaises(SystemExit):
                main(["flag-audit", "--harvest", tmp, "--all-open", "--variant", "x=/Ox", "--save", f"{tmp}/o.json"])

    def test_a_later_run_resumes_from_the_save_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "checkout"
            target = ScriptedGruntz(root)
            save = Path(tmp) / "audit.json"
            argv = ["flag-audit", "--gruntz", str(root), "--unit", "a", "--variant", "ox=!/O2 /Ox", "--save", str(save)]
            with patch("concord.cli.Gruntz", return_value=target), redirect_stdout(StringIO()):
                self.assertEqual(main(argv), 0)
            compiled = len(target.evaluated)
            with patch("concord.cli.Gruntz", return_value=target), redirect_stdout(StringIO()) as output:
                self.assertEqual(main(argv), 0)
            self.assertEqual(len(target.evaluated), compiled)
            self.assertIn("1 of 1 units", output.getvalue())
            self.assertEqual([entry["unit"] for entry in json.loads(save.read_text())["units"]], ["a"])


@unittest.skipUnless(os.environ.get("CONCORD_GRUNTZ"), "CONCORD_GRUNTZ not set")
class GruntzCheckoutFlagAudit(unittest.TestCase):
    def test_one_variant_compiles_the_smallest_open_unit(self):
        target = Gruntz(Path(os.environ["CONCORD_GRUNTZ"]))
        unit = min(target.open_units(), key=lambda name: target.source_path(name).stat().st_size)
        started = time.monotonic()
        entry = gruntz_unit(target, unit, [parse_variant("ox=!/O2 /Ox")], jobs=2)
        self.assertNotIn("error", entry, unit)
        self.assertTrue(entry["baseline"]["matches_report"], unit)
        (variant,) = entry["variants"]
        self.assertNotIn("error", variant)
        self.assertTrue(variant["scores"])
        self.assertLess(time.monotonic() - started, 600)


if __name__ == "__main__":
    unittest.main()
