"""The differential oracle, against the real external check on a Harvest checkout."""

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from concord import oracle
from concord.harvest import Harvest
from concord.model import EquivalenceResult

HARVEST = Path(os.environ.get("CONCORD_HARVEST", "../harvest"))
UNIT = "HarvestFull/harvest/entity/CDropshipEntity.cpp"
SYMBOL = "_ZN7harvest6entity15CDropshipEntity11updateLogicEf"


@unittest.skipUnless(oracle.configured() and (HARVEST / "build" / "match").is_dir(), "needs CONCORD_ORACLE and Harvest")
class Differential(unittest.TestCase):
    def test_a_nan_only_comparison_change_is_caught(self):
        harvest = Harvest(HARVEST)
        before = subprocess.run(
            ["git", "-C", str(HARVEST), "show", f"4bc99eb:src/{UNIT}"], capture_output=True, check=True
        ).stdout
        nan = before.replace(b"if (BulletCooldown <= 0)", b"if (!(BulletCooldown > 0))", 1)  # differs only for NaN
        self.assertNotEqual(nan, before)
        self.assertFalse(oracle.check(harvest, UNIT, SYMBOL, before, nan).equivalent)
        self.assertTrue(oracle.check(harvest, UNIT, SYMBOL, before, before + b"\n").equivalent)


class OverlayGate(unittest.TestCase):
    def test_changed_behavior_must_reduce_original_differences(self):
        outcome = {"original": {"crash": 5000}, "before": {"crash": 5000}, "after": {"crash": 5000}}
        report = {"comparison": {"original vs before": {"differ": 52}, "original vs after": {"differ": 0}},
                  "outcomes": outcome}
        different = EquivalenceResult(False, "differential-test", '{"verdict": "differ"}')
        with patch("concord.oracle.check_objects", return_value=different), patch(
            "concord.oracle.original_comparison_objects", return_value=report
        ):
            accepted, evidence = oracle.gate_objects(None, UNIT, SYMBOL, Path("before.o"), Path("after.o"))
            self.assertTrue(accepted)
            self.assertEqual(evidence["original"], {"before": 52, "after": 0, "cases": 5000, "outcomes": outcome})
            report["comparison"]["original vs after"]["differ"] = 52
            self.assertFalse(oracle.gate_objects(None, UNIT, SYMBOL, Path("before.o"), Path("after.o"))[0])


if __name__ == "__main__":
    unittest.main()
