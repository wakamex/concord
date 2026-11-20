"""Gruntz partial gains require the target's differential behavior check."""

import argparse
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from concord import cli, oracle
from concord.gruntz import Gruntz
from concord.model import EquivalenceResult


class GruntzOracle(unittest.TestCase):
    def setUp(self):
        self.target = Gruntz.__new__(Gruntz)
        self.target.root = Path.cwd()
        self.target.sources = {"unit": "src/unit.cpp"}

    def test_default_commands_are_target_specific(self):
        with patch.dict(os.environ, {}, clear=True), patch("concord.oracle.shutil.which", side_effect=lambda name: name):
            self.assertEqual(oracle.command(self.target), "gruntz-oracle")
            self.assertEqual(oracle.command(), "harvest-oracle")
            self.assertTrue(self.target.oracle)
        with patch.dict(os.environ, {}, clear=True), patch("concord.oracle.shutil.which", return_value=None):
            self.assertFalse(self.target.oracle)

    def test_override_and_disable_apply_to_gruntz(self):
        with patch.dict(os.environ, {"CONCORD_ORACLE": "custom --flag"}):
            self.assertEqual(oracle.command(self.target), "custom --flag")
            self.assertTrue(self.target.oracle)
        with patch.dict(os.environ, {"CONCORD_ORACLE": ""}):
            self.assertIsNone(oracle.command(self.target))
            self.assertFalse(self.target.oracle)

    def test_check_invokes_gruntz_command_in_checkout(self):
        with patch.dict(os.environ, {"CONCORD_ORACLE_CASES": "17"}, clear=True), patch(
            "concord.oracle.shutil.which", return_value="gruntz-oracle"
        ), patch("concord.oracle._run", return_value=SimpleNamespace(stdout='{"verdict":"agree"}')) as run:
            result = oracle.check_objects(self.target, "unit", "?f@@YAHH@Z", Path("before.obj"), Path("after.obj"))
        self.assertTrue(result.equivalent)
        self.assertEqual(run.call_args.args, (
            ["gruntz-oracle", "check", "before.obj", "after.obj", "--symbol", "?f@@YAHH@Z", "--unit", "unit", "--cases", "17"],
            self.target.root, (0, 1), "oracle check",
        ))

    def test_difference_never_uses_harvest_image_repair(self):
        different = EquivalenceResult(False, "differential-test", '{"verdict":"differ"}')
        with patch("concord.oracle.check", return_value=different), patch(
            "concord.oracle.check_objects", return_value=different
        ), patch("concord.oracle.original_comparison_objects") as original:
            self.assertFalse(oracle.gate(self.target, "unit", "f", b"before", b"after")[0])
            self.assertFalse(oracle.gate_objects(self.target, "unit", "f", Path("before"), Path("after"))[0])
            original.assert_not_called()

    def test_permute_applies_only_an_agreeing_partial_gain(self):
        for agrees in (False, True):
            with self.subTest(agrees=agrees), TemporaryDirectory() as directory:
                self.target.root = Path(directory)
                source = self.target.root / "src/unit.cpp"
                source.parent.mkdir()
                source.write_bytes(b"before")
                before = SimpleNamespace(exact=False, fuzzy=95, score=95, findings=[])
                after = SimpleNamespace(exact=False, fuzzy=96, score=96, findings=[])
                found = SimpleNamespace(baseline=before, best=after, source=b"after", steps=[],
                                        reason="budget", tried=1, lowered={})
                args = argparse.Namespace(budget=1, batch=1, seed=0, depth=1, apply=False,
                                          apply_improved=True, results=self.target.root / "results.jsonl")
                with patch.dict(os.environ, {"CONCORD_ORACLE": "gruntz-oracle"}), patch(
                    "concord.cli.permute", return_value=found
                ), patch("concord.cli.finish", return_value=b"after"), patch(
                    "concord.cli.results.commit", return_value="abc"
                ), patch("concord.cli.results.save_patch", return_value=None), patch(
                    "concord.cli.oracle.gate", return_value=(agrees, {"same": {"verdict": "agree" if agrees else "differ"}})
                ) as gate, patch("concord.cli.repair") as repair, contextlib.redirect_stdout(io.StringIO()):
                    cli._permute_one(self.target, "src/unit.cpp", "f", args)
                gate.assert_called_once_with(self.target, "src/unit.cpp", "f", b"before", b"after")
                repair.assert_not_called()
                self.assertEqual(source.read_bytes(), b"after" if agrees else b"before")
                self.assertIsNotNone(json.loads(args.results.read_text())["oracle"])

    def test_permute_refuses_disabled_gruntz_oracle(self):
        args = argparse.Namespace(results=None, apply_improved=True)
        with patch.dict(os.environ, {"CONCORD_ORACLE": ""}), patch("concord.cli._target", return_value=self.target), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli._permute(argparse.ArgumentParser(), args)


if __name__ == "__main__":
    unittest.main()
