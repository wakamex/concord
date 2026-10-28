"""The residual census classifies real Harvest object differences."""

import os
import unittest
from pathlib import Path

HARVEST = Path(os.environ.get("CONCORD_HARVEST", "../harvest"))
BUILD = "1.18-linux-amd64"


@unittest.skipUnless((HARVEST / "build" / "match" / BUILD).is_dir(), "needs a Harvest checkout after `hv match`")
class HarvestResidual(unittest.TestCase):
    def test_known_residuals_and_source_patterns_are_classified(self):
        from concord.harvest import Harvest
        from concord.residual import report

        census = report(Harvest(HARVEST))
        functions = {row["symbol"]: row for row in census["functions"]}

        expected = {
            "_ZN5daisy5scene17CXAnimationPlayer20prepareAnimationDataEv": "instruction-order",
            "_ZN7harvest6states10CPlayState17initializeNewGameEv": "instruction-selection",
            "_ZN5daisy5scene20CCameraMayaSceneNode7animateEv": "register-choice",
            "_ZN5daisy5scene17COctTreeSceneNode6renderEv": "block-order",
            "_ZN2ox4core16CStringFunctions18millisecondsToWideEfb": "comparison-flip",
            "_ZN7harvest6states11CIntroState7OnEventERKN2ox5event6SEventE": "enum-check",
            "_ZN7harvest6entity12CAlienEntity25replaceWithMagnetoSpritesEv": "data",
            "_ZN7harvest6entity19CDefenseTowerEntityC2Eff": "calls",
        }
        for symbol, kind in expected.items():
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, functions)
                self.assertIn(kind, {item["kind"] for item in functions[symbol]["kinds"]})

        self.assertIn(
            "padding",
            {item["kind"] for item in functions["_ZN7harvest6states10CPlayState17initializeNewGameEv"]["kinds"]},
        )
        self.assertIn(
            "size",
            {item["kind"] for item in functions["_ZN7harvest6entity19CDefenseTowerEntityC2Eff"]["kinds"]},
        )
        summary = census["summary"]
        self.assertGreater(summary["groups"]["no-source-change"]["functions"], 0)
        self.assertGreater(summary["groups"]["source-pointing"]["functions"], 0)
        signedness = functions["_ZN5daisy3gui14CGUIStaticText9breakTextEv"]
        self.assertIn("signedness", signedness["source_signals"])
        self.assertEqual(len(census["top_source_pointing"]), 20)


if __name__ == "__main__":
    unittest.main()
