"""The differential oracle, against the real external check on a Harvest checkout."""

import os
import subprocess
import sys
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from concord import cli, oracle
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


class OracleProcess(unittest.TestCase):
    class CompiledHarvest:
        root = Path.cwd()

        def evaluate(self, unit, sources):
            return {
                "before": SimpleNamespace(error=None, object=Path("before.o")),
                "after": SimpleNamespace(error=None, object=Path("after.o")),
            }

    @contextmanager
    def stand_in(self, body, timeout="0.25"):
        with TemporaryDirectory() as directory:
            script = Path(directory) / "oracle.py"
            script.write_text(body)
            command = f"{sys.executable} {script}"
            with patch.dict(os.environ, {
                oracle.VARIABLE: command,
                oracle.TIMEOUT_VARIABLE: timeout,
            }):
                yield

    def test_hanging_candidate_is_rejected_with_its_timeout(self):
        body = """import json, sys, time
if sys.argv[2] == sys.argv[3]:
    print(json.dumps({"verdict": "agree"}))
else:
    time.sleep(30)
"""
        with self.stand_in(body):
            kept, evidence = oracle.gate(self.CompiledHarvest(), UNIT, SYMBOL, b"before", b"after")
        self.assertFalse(kept)
        self.assertIn("timed out after 0.25 seconds", evidence["error"])

    def test_failed_candidate_is_rejected_with_its_error(self):
        body = """import json, sys
if sys.argv[2] == sys.argv[3]:
    print(json.dumps({"verdict": "agree"}))
else:
    print("candidate failed", file=sys.stderr)
    raise SystemExit(7)
"""
        with self.stand_in(body):
            kept, evidence = oracle.gate_objects(
                self.CompiledHarvest(), UNIT, SYMBOL, Path("before.o"), Path("after.o")
            )
        self.assertFalse(kept)
        self.assertIn("oracle check failed (7)", evidence["error"])
        self.assertIn("candidate failed", evidence["error"])

    def test_baseline_failure_remains_fatal(self):
        body = """import sys
print("baseline failed", file=sys.stderr)
raise SystemExit(8)
"""
        with self.stand_in(body), self.assertRaisesRegex(oracle.OracleBaselineError, "failed on the baseline"):
            oracle.gate_objects(self.CompiledHarvest(), UNIT, SYMBOL, Path("before.o"), Path("after.o"))

    def test_invalid_timeout_remains_a_setup_error(self):
        body = "print('{}')\n"
        with self.stand_in(body, timeout="soon"), self.assertRaisesRegex(oracle.OracleSetupError, "CONCORD_ORACLE_TIMEOUT"):
            oracle.gate_objects(self.CompiledHarvest(), UNIT, SYMBOL, Path("before.o"), Path("after.o"))

    def test_cli_reports_fatal_oracle_errors_without_a_traceback(self):
        stderr = StringIO()
        with patch("concord.cli._dispatch", side_effect=oracle.OracleSetupError("bad timeout")), patch("sys.stderr", stderr):
            status = cli.main([])
        self.assertEqual(status, 1)
        self.assertEqual(stderr.getvalue(), "concord: bad timeout\n")

    def test_default_timeout_is_sixty_seconds_and_can_be_overridden(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(oracle.timeout_seconds(), 60.0)
        with patch.dict(os.environ, {oracle.TIMEOUT_VARIABLE: "12.5"}):
            self.assertEqual(oracle.timeout_seconds(), 12.5)


if __name__ == "__main__":
    unittest.main()
