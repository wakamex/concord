"""Smoke tests for the concord entrypoints."""

import subprocess
import sys

import pytest

from concord.cli import build_parser, main


def test_no_args_prints_help_and_succeeds(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Matching recompilation" in out


def test_subcommands_are_stubs():
    # Each wired subcommand returns the stub code until implemented.
    assert main(["status"]) == 2
    assert main(["match", "some_function"]) == 2


def test_parser_knows_the_pipeline_stages():
    parser = build_parser()
    help_text = parser.format_help()
    for stage in ("init", "seed", "types", "flags", "match", "diff", "status"):
        assert stage in help_text


@pytest.mark.parametrize("entry", [[sys.executable, "-m", "concord"]])
def test_module_entrypoint_runs(entry):
    result = subprocess.run([*entry, "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "concord" in result.stdout
