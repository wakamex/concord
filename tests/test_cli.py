"""Smoke tests for the concord entrypoints."""

import contextlib
import io
import subprocess
import sys
import unittest

from concord.cli import build_parser, main


class CliTest(unittest.TestCase):
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
