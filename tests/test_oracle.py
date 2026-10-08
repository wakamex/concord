"""The differential oracle, against the real external check on a Harvest checkout."""

import os
import subprocess
import unittest
from pathlib import Path

from concord import oracle
from concord.harvest import Harvest

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


if __name__ == "__main__":
    unittest.main()
