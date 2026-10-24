"""Smoke tests for the concord entrypoints."""

import contextlib
import io
import subprocess
import sys
import unittest

from concord.cli import build_parser, main


class CliTest(unittest.TestCase):
    def test_blockers_passes_selected_methods(self):
        from unittest import mock

        with mock.patch('concord.cli.Harvest') as harvest, mock.patch('concord.cli.blockers.stubs', return_value=[]) as stubs:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['blockers', '--harvest', '/tmp/root', 'a.cpp', '--apply', '--only', 'A::f', '--only', 'A::g']), 0)
            stubs.assert_called_once_with(harvest.return_value, ['a.cpp'], apply=True, only={'A::f', 'A::g'})

    def test_destructors_reports_overlays_without_an_apply_flag(self):
        from unittest import mock

        report = {"variants": {"baseline": {"exact": 3, "worse": {}, "error": "", "destructor_sizes": {}}}}
        with mock.patch("concord.cli.Harvest") as harvest, mock.patch("concord.cli.destructors.diagnose", return_value=report) as diagnose:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["destructors", "--harvest", "/tmp/root", "--unit", "a.cpp", "--header", "a.h", "--class", "A"]), 0)
            diagnose.assert_called_once_with(harvest.return_value, "a.cpp", "a.h", "A")
            self.assertIn("3 exact, 0 worse", output.getvalue())

    def test_explicit_permute_target_respects_skip_logged(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "runs.jsonl"
            log.write_text(json.dumps({"command": "permute", "unit": "u.cpp", "symbol": "symbol", "budget": 128, "batch": 32, "depth": 3, "seed": 0}) + "\n")
            with mock.patch("concord.cli.Harvest", return_value=SimpleNamespace(source=lambda unit: unit)), mock.patch("concord.cli._permute_one") as run:
                self.assertEqual(main(["permute", "--harvest", tmp, "--unit", "u.cpp", "symbol", "--budget", "128", "--skip-logged", "--results", str(log)]), 0)
            run.assert_not_called()

    def test_rename_gives_helpers_plain_names(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = root / "src" / "d" / "A.cpp"
            unit.parent.mkdir(parents=True)
            unit.write_text("inline int _concord_helper(int n)\n{\n\treturn n;\n}\nint f() { return _concord_helper(1); }\n")
            self.assertEqual(main(["rename", "--harvest", tmp, "d/A.cpp"]), 0)
            text = unit.read_text()
            self.assertIn("inline int helper(int n)", text)
            self.assertIn("return helper(1);", text)
            self.assertNotIn("_concord_", text)

    def test_no_args_prints_help_and_succeeds(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([]), 0)
        self.assertIn("Matching recompilation", out.getvalue())

    def test_parser_lists_the_commands(self):
        help_text = build_parser().format_help()
        for command in ("seed", "match", "permute", "diff", "phase", "scores", "bench", "unguard"):
            self.assertIn(command, help_text)

    def test_module_entrypoint_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "concord", "--version"], capture_output=True, text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("concord", result.stdout)


if __name__ == "__main__":
    unittest.main()
