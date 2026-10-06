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

    def test_unbuilt_subcommands_are_stubs(self):
        # Each wired subcommand returns the stub code until implemented.
        self.assertEqual(main(["status"]), 2)
        self.assertEqual(main(["seed", "some_function"]), 2)

    def test_parser_knows_the_pipeline_stages(self):
        help_text = build_parser().format_help()
        for stage in ("init", "seed", "types", "flags", "match", "diff", "status"):
            self.assertIn(stage, help_text)

    def test_module_entrypoint_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "concord", "--version"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("concord", result.stdout)


if __name__ == "__main__":
    unittest.main()
