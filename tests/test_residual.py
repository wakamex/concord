"""The residual census classifies real Harvest object differences."""

import os
import unittest
from pathlib import Path

HARVEST = Path(os.environ.get("CONCORD_HARVEST", "../harvest"))
BUILD = "1.18-linux-amd64"


class CallRows(unittest.TestCase):
    def test_call_rows_use_multiset_evidence(self):
        from concord.model import Cause, DiffFinding, DiffResult
        from concord.residual import _kind_rows, _pattern_counts

        result = DiffResult(
            score=90.0,
            exact=False,
            findings=[DiffFinding(Cause.INLINING, 0, "unaligned calls", 0, 2, (0, 4), (0, 4))],
        )
        cases = (
            ([], {"block-order": 2}, False),
            (
                [{"kind": "call", "classification": "called-out-of-line", "inline_distribution": True}],
                {"inline-distribution": 2},
                False,
            ),
            (
                [{"kind": "call", "classification": "different-callees", "ours": [{}], "target": [{}]}],
                {"calls": 2},
                True,
            ),
            (
                [{"kind": "call", "classification": "called-out-of-line", "count_difference": True}],
                {"calls": 1, "block-order": 1},
                True,
            ),
        )
        for differences, expected, expected_source_pointing in cases:
            with self.subTest(expected=expected, differences=differences):
                counts, source_pointing = _pattern_counts(differences)
                rows, _ = _kind_rows(result, set(), counts)
                self.assertEqual(dict(rows), expected)
                self.assertEqual(source_pointing, expected_source_pointing)


class ProgressReconciliation(unittest.TestCase):
    def test_reconciliation_removes_exact_overlap_and_adds_missing_and_unclaimed_bytes(self):
        from concord.residual import _reconcile_progress

        def function_unit(name, address, size, matched):
            return {
                "name": name,
                "measures": {
                    "total_code": str(size),
                    "matched_code": str(size if matched else 0),
                    "total_functions": 1,
                    "matched_functions": int(matched),
                },
                "functions": [
                    {
                        "name": name,
                        "size": str(size),
                        "fuzzy_match_percent": 100 if matched else 0,
                        "metadata": {"virtual_address": str(address)},
                    }
                ],
                "metadata": {"progress_categories": ["functions"]},
            }

        progress = {
            "version": 2,
            "measures": {
                "total_code": "60",
                "matched_code": "20",
                "total_functions": 2,
                "matched_functions": 1,
            },
            "units": [
                function_unit("already_exact", 0x1000, 20, True),
                function_unit("missing", 0x2000, 30, False),
                {
                    "name": "Unclaimed .text bytes (includes padding/stubs)",
                    "measures": {"total_code": "10", "matched_code": "0", "total_functions": 0, "matched_functions": 0},
                    "metadata": {"progress_categories": ["unclaimed"]},
                },
            ],
        }
        residual_functions = [{"address": "0x1000", "symbol": "already_exact", "size": 20}]

        result = _reconcile_progress(progress, residual_functions)

        summary = result["summary"]
        self.assertEqual(summary["progress_inexact_functions"], 1)
        self.assertEqual(summary["progress_inexact_function_bytes"], 30)
        self.assertEqual(summary["residual_progress_exact_functions"], 1)
        self.assertEqual(summary["residual_progress_exact_bytes"], 20)
        self.assertEqual(summary["missing_inexact_functions"], 1)
        self.assertEqual(summary["missing_inexact_function_bytes"], 30)
        self.assertEqual(summary["unclaimed_code_bytes"], 10)
        self.assertEqual(summary["inexact_code_bytes"], 40)
        self.assertEqual(summary["reconciled_code_bytes"], 40)
        self.assertEqual(result["groups"]["not-compiled"]["functions"][0]["symbol"], "missing")

    def test_missing_causes_use_symbol_and_placement_evidence(self):
        from concord.residual import _classify_missing

        candidates = {
            "unplaced": [{"unit": "unit.cpp", "section": ".text.unplaced", "unplaced": True}],
            "stub": [{"unit": "unit.cpp", "section": ".text.stub", "unplaced": False, "stubbed": True}],
            "placed": [{"unit": "unit.cpp", "section": ".text.placed", "unplaced": False}],
        }
        placed = {0x4000: [{"unit": "other.cpp", "symbol": "renamed"}]}
        cases = (
            ("absent", 0x1000, "not-compiled"),
            ("unplaced", 0x2000, "compiled-but-unplaced"),
            ("stub", 0x3000, "stubbed"),
            ("placed", 0x3500, "placed-in-unit-residual-skips"),
            ("different", 0x4000, "matched-under-another-name"),
            ("sub_5000", 0x5000, "unmapped-target-extent"),
        )
        for symbol, address, expected in cases:
            with self.subTest(symbol=symbol):
                result = _classify_missing(
                    {"symbol": symbol, "address": hex(address)}, candidates, placed
                )
                self.assertEqual(result["cause"], expected)


# The census covers the whole build, so these checks require an explicitly named checkout.
@unittest.skipUnless(
    os.environ.get("CONCORD_HARVEST") and (HARVEST / "build" / "match" / BUILD).is_dir(),
    "needs CONCORD_HARVEST naming a Harvest checkout after `hv match`",
)
class HarvestResidual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from concord.harvest import Harvest
        from concord.residual import report

        cls.census = report(Harvest(HARVEST))
        cls.functions = {row["symbol"]: row for row in cls.census["functions"]}

    def test_known_residuals_and_source_patterns_are_classified(self):
        expected = {
            "_ZN5daisy5scene17CXAnimationPlayer20prepareAnimationDataEv": "instruction-order",
            "_ZN7harvest6states10CPlayState17initializeNewGameEv": "instruction-selection",
            "_ZN5daisy5scene20CCameraMayaSceneNode7animateEv": "register-choice",
            "_ZN5daisy5scene17COctTreeSceneNode6renderEv": "block-order",
            "_ZN2ox4core16CStringFunctions18millisecondsToWideEfb": "comparison-flip",
            "_ZN7harvest6states11CIntroState7OnEventERKN2ox5event6SEventE": "enum-check",
            "_ZN7harvest6entity12CAlienEntity25replaceWithMagnetoSpritesEv": "data",
            "_ZN7harvest6entity19CDefenseTowerEntityC2Eff": "inline-distribution",
        }
        for symbol, kind in expected.items():
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, self.functions)
                self.assertIn(kind, {item["kind"] for item in self.functions[symbol]["kinds"]})

        self.assertIn(
            "padding",
            {item["kind"] for item in self.functions["_ZN7harvest6states10CPlayState17initializeNewGameEv"]["kinds"]},
        )
        self.assertIn(
            "size",
            {item["kind"] for item in self.functions["_ZN7harvest6entity19CDefenseTowerEntityC2Eff"]["kinds"]},
        )
        summary = self.census["summary"]
        self.assertGreater(summary["groups"]["no-source-change"]["functions"], 0)
        self.assertGreater(summary["groups"]["source-pointing"]["functions"], 0)
        signedness = self.functions["_ZN5daisy3gui14CGUIStaticText9breakTextEv"]
        self.assertIn("signedness", signedness["source_signals"])
        self.assertEqual(len(self.census["top_source_pointing"]), 20)
        on_event = self.functions["_ZN7harvest6states10CPlayState7OnEventERKN2ox5event6SEventE"]
        on_event_kinds = {item["kind"]: item["rows"] for item in on_event["kinds"]}
        self.assertEqual(on_event_kinds["calls"], 7)
        self.assertEqual(on_event["group"], "source-pointing")
        self.assertIn(on_event, self.census["top_source_pointing"])

    def test_progress_reconciliation_names_every_missing_function_and_byte_range(self):
        reconciliation = self.census["progress_reconciliation"]
        self.assertIsNotNone(reconciliation)
        summary = reconciliation["summary"]
        self.assertEqual(summary["progress_functions"], 5240)
        self.assertEqual(summary["progress_matched_functions"], 4807)
        self.assertEqual(summary["progress_inexact_functions"], 433)
        self.assertEqual(summary["progress_inexact_function_bytes"], 601913)
        self.assertEqual(summary["residual_raw_functions"], 381)
        self.assertEqual(summary["residual_raw_bytes"], 593743)
        self.assertEqual(summary["residual_progress_inexact_functions"], 351)
        self.assertEqual(summary["residual_progress_exact_functions"], 30)
        self.assertEqual(summary["missing_inexact_functions"], 82)
        self.assertEqual(summary["missing_inexact_function_bytes"], 25396)
        self.assertEqual(summary["unclaimed_code_bytes"], 49485)
        self.assertEqual(summary["inexact_code_bytes"], 651398)
        self.assertEqual(summary["reconciled_code_bytes"], 651398)
        self.assertEqual(self.census["summary"]["functions"], 433)
        self.assertEqual(self.census["summary"]["bytes"], 601913)
        self.assertEqual(self.census["summary"]["inexact_code_bytes"], 651398)

        groups = reconciliation["groups"]
        self.assertEqual(len(groups["not-compiled"]["functions"]), 20)
        self.assertEqual(groups["not-compiled"]["bytes"], 4387)
        self.assertEqual(len(groups["unmapped-target-extent"]["functions"]), 62)
        self.assertEqual(groups["unmapped-target-extent"]["bytes"], 21009)
        self.assertEqual(len(reconciliation["exact_overlaps"]), 30)
        self.assertEqual(groups["unclaimed-code"]["bytes"], 49485)
        self.assertEqual(self.census["summary"]["groups"]["progress-missing"], {"functions": 82, "bytes": 25396})
        self.assertEqual(len(self.census["functions"]), 433)
        self.assertEqual(
            sum(row["bytes"] for row in reconciliation["unclaimed_code"]),
            49485,
        )
        self.assertTrue(
            all(row["size"] > 0 and row["evidence"] for row in reconciliation["missing_functions"])
        )


@unittest.skipUnless(
    os.environ.get("CONCORD_HARVEST") and (HARVEST / "build" / "match" / BUILD).is_dir(),
    "needs CONCORD_HARVEST naming a Harvest checkout after `hv match`",
)
class HarvestResidualCallRows(unittest.TestCase):
    CASES = (
        (
            "printVariables",
            "daisy/video/Null/CCGMaterialRenderer.cpp",
            "_ZN5daisy5video19CCGMaterialRenderer14printVariablesEv",
        ),
        (
            "loadSprite",
            "HarvestFull/harvest/entity/CDropshipEntity.cpp",
            "_ZN7harvest6entity15CDropshipEntity10loadSpriteEb",
        ),
        (
            "on_event",
            "HarvestFull/harvest/states/CPlayState.cpp",
            "_ZN7harvest6states10CPlayState7OnEventERKN2ox5event6SEventE",
        ),
    )

    @classmethod
    def setUpClass(cls):
        from concord.calls_constants import gather
        from concord.diff import diff_code, read_functions
        from concord.harvest import Harvest
        from concord.residual import _group, _kind_rows, _pattern_counts

        cls.harvest = Harvest(HARVEST)
        references = gather(cls.harvest, [unit for _, unit, _ in cls.CASES])
        by_function = {(row["unit"], row["symbol"]): row for row in references["functions"]}
        cls.rows = {}
        for name, unit, symbol in cls.CASES:
            verdict = next(v for v in cls.harvest.verdicts(unit) if v.symbol == symbol)
            target = read_functions(cls.harvest.objects / "target" / f"{verdict.unit}.o", {symbol})[symbol]
            candidate = read_functions(cls.harvest.objects / "base" / f"{verdict.unit}.o", {symbol})[symbol]
            call_report = by_function.get((cls.harvest.source(verdict.unit), symbol), {})
            call_differences = call_report.get("differences", [])
            pattern_counts, source_pointing = _pattern_counts(call_differences)
            result = diff_code(target, candidate)
            rows, _ = _kind_rows(result, set(), pattern_counts)
            kinds = set(rows) | set(pattern_counts)
            signals = {"calls-constants"} if source_pointing else set()
            cls.rows[name] = {
                "counts": rows,
                "group": _group(kinds, signals),
            }

    def test_print_variables_calls_only_move_between_blocks(self):
        row = self.rows["printVariables"]

        self.assertGreaterEqual(row["counts"]["block-order"], 12)
        self.assertNotIn("calls", row["counts"])
        self.assertEqual(row["group"], "no-source-change")

    def test_load_sprite_calls_are_inline_distribution(self):
        row = self.rows["loadSprite"]

        self.assertEqual(row["counts"]["inline-distribution"], 32)
        self.assertNotIn("calls", row["counts"])

    def test_on_event_counts_only_call_multiset_differences(self):
        row = self.rows["on_event"]

        self.assertEqual(row["counts"]["calls"], 7)
        self.assertGreater(row["counts"]["block-order"], 0)
        self.assertEqual(row["group"], "source-pointing")


if __name__ == "__main__":
    unittest.main()
