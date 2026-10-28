"""Semantics oracle: reject rewrites that change what a function does.

An exact byte match is its own proof at the machine level. A partial gain is
not: a rewrite can score closer to the target and still behave differently, for
example a float comparison turned around, which differs only for NaN. The oracle
compiles the function before and after the rewrite and runs both on the same
generated inputs (differential testing), comparing return values, calls out of
the function and memory written. Agreement is evidence, not proof.

The check is an external command, harvest-oracle when it is installed or the
command the CONCORD_ORACLE environment variable names (empty turns it off), run from the Harvest checkout as
`$CONCORD_ORACLE check BEFORE.o AFTER.o --symbol SYMBOL --unit UNIT --cases N`. It prints
one JSON line with "verdict" ("agree" or "differ") and exits 0 on agreement and
1 on a difference. harvest-oracle implements it.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from concord.harvest import Harvest
from concord.model import EquivalenceResult

VARIABLE = "CONCORD_ORACLE"
TIMEOUT_VARIABLE = "CONCORD_ORACLE_TIMEOUT"
DEFAULT_TIMEOUT = 60.0
# A float comparison changed so it differs only for NaN can show in about 0.2% of
# generated cases (CDropshipEntity::updateLogic: 4 of 2,000); 5,000 cases catch
# it with near certainty in a few seconds per function.
CASES = 5000


class OracleError(RuntimeError):
    """An oracle setup or execution error that the command line can report cleanly."""


class OracleInvocationError(OracleError):
    """An oracle process timed out, failed, or returned an invalid report."""


class OracleSetupError(OracleError):
    """The oracle command or its configuration could not be used."""


class OracleBaselineError(OracleSetupError):
    """The oracle could not establish a usable baseline for a candidate check."""


def command() -> str | None:
    """The check to run: CONCORD_ORACLE, or harvest-oracle when it is installed.
    Setting CONCORD_ORACLE to an empty string turns the check off."""
    if VARIABLE in os.environ:
        return os.environ[VARIABLE] or None
    return "harvest-oracle" if shutil.which("harvest-oracle") else None


def configured() -> bool:
    return command() is not None


def timeout_seconds() -> float:
    """The positive per-invocation timeout, in seconds."""
    value = os.environ.get(TIMEOUT_VARIABLE)
    if value is None:
        return DEFAULT_TIMEOUT
    try:
        seconds = float(value)
    except ValueError as error:
        raise OracleSetupError(f"{TIMEOUT_VARIABLE} must be a positive number of seconds") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise OracleSetupError(f"{TIMEOUT_VARIABLE} must be a positive number of seconds")
    return seconds


def _run(argv: list[str], cwd: Path, allowed: tuple[int, ...], label: str) -> subprocess.CompletedProcess:
    seconds = timeout_seconds()
    try:
        done = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=seconds,
        )
    except subprocess.TimeoutExpired as error:
        detail = _output_text(error.stderr)
        message = f"{label} timed out after {seconds:g} seconds"
        if detail:
            message += f":\n{detail[-2000:]}"
        raise OracleInvocationError(message) from error
    except (OSError, ValueError) as error:
        raise OracleSetupError(f"could not start {label}: {error}") from error
    if done.returncode not in allowed:
        raise OracleInvocationError(f"{label} failed ({done.returncode}):\n{done.stderr[-2000:]}")
    return done


def run_harvest_oracle(arguments: list[str], cwd: Path, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    """Run a harvest-oracle command with the configured time limit."""
    return _run(["harvest-oracle", *arguments], cwd, allowed, "harvest-oracle")


def _output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _command_args(prefix: str, label: str) -> list[str]:
    try:
        argv = shlex.split(prefix)
    except ValueError as error:
        raise OracleSetupError(f"invalid {label} command: {error}") from error
    if not argv:
        raise OracleSetupError(f"{label} command is empty")
    return argv


def _report(stdout: str, label: str) -> dict:
    try:
        report = json.loads(stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise OracleInvocationError(f"{label} returned no valid JSON report") from error
    if not isinstance(report, dict):
        raise OracleInvocationError(f"{label} returned an invalid JSON report")
    return report


def _check_baseline(harvest: Harvest, unit: str, symbol: str, before: Path) -> None:
    try:
        verdict = check_objects(harvest, unit, symbol, before, before)
    except OracleInvocationError as error:
        raise OracleBaselineError(f"oracle failed on the baseline for {symbol}: {error}") from error
    if verdict is not None and not verdict.equivalent:
        raise OracleBaselineError(f"oracle disagrees with the baseline for {symbol}: {verdict.detail}")


def _original_baseline(harvest: Harvest, symbol: str, before: Path) -> None:
    try:
        original_comparison_objects(harvest, symbol, {"baseline": before})
    except OracleInvocationError as error:
        raise OracleBaselineError(f"oracle failed on the baseline for {symbol}: {error}") from error


def check(harvest: Harvest, unit: str, symbol: str, before: bytes, after: bytes) -> EquivalenceResult | None:
    """Whether the function behaves the same compiled from `before` and from `after`,
    or None when no oracle is configured."""
    prefix = command()
    if prefix is None:
        return None
    evaluations = harvest.evaluate(unit, {"before": before, "after": after})
    baseline = evaluations["before"]
    if baseline.error:
        raise OracleBaselineError(f"the oracle baseline does not compile for {symbol}:\n{baseline.error}")
    candidate = evaluations["after"]
    if candidate.error:
        detail = json.dumps({"verdict": "differ", "reason": "candidate does not compile"})
        return EquivalenceResult(False, "differential-test", detail)
    try:
        return check_objects(harvest, unit, symbol, baseline.object, candidate.object)
    except OracleInvocationError:
        _check_baseline(harvest, unit, symbol, baseline.object)
        raise


def check_objects(harvest: Harvest, unit: str, symbol: str, before: Path, after: Path) -> EquivalenceResult | None:
    """Compare already compiled objects, including objects from header overlays."""
    prefix = command()
    if prefix is None:
        return None
    argv = [*_command_args(prefix, "oracle"), "check", str(before), str(after),
            "--symbol", symbol, "--unit", unit, "--cases", str(CASES)]
    done = _run(argv, harvest.root, (0, 1), "oracle check")
    report = _report(done.stdout, "oracle check")
    verdict = report.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"agree", "differ"}:
        raise OracleInvocationError("oracle check returned an invalid verdict")
    return EquivalenceResult(verdict == "agree", "differential-test", json.dumps(report))


def original_differences(harvest: Harvest, unit: str, symbol: str, sources: dict[str, bytes]) -> dict[str, int] | None:
    """For each named source, the cases in which the function compiled from it
    differs from the original executable's own code, from one oracle run that
    generates the cases and runs the original once. None when no oracle is
    configured; sources that do not compile are left out."""
    prefix = command()
    if prefix is None:
        return None
    evaluations = harvest.evaluate(unit, sources)
    built = {name: e.object for name, e in evaluations.items() if not e.error}
    if not built:
        return {}
    try:
        report = original_comparison_objects(harvest, symbol, built)
    except OracleInvocationError:
        baseline_name = next(iter(built))
        _original_baseline(harvest, symbol, built[baseline_name])
        raise
    return {name: report["comparison"][f"original vs {name}"]["differ"] for name in built}


def original_comparison_objects(harvest: Harvest, symbol: str, objects: dict[str, Path]) -> dict:
    """Compare the original and compiled objects on the same generated cases."""
    prefix = command()
    if prefix is None:
        raise ValueError("an oracle is required for an original comparison")
    argv = [*_command_args(prefix, "oracle"), "compare", symbol, "original=image",
            *(f"{name}={obj}" for name, obj in objects.items()), "--cases", str(CASES)]
    done = _run(argv, harvest.root, (0, 1), "oracle comparison")
    try:
        report = json.loads(done.stdout)
    except json.JSONDecodeError as error:
        raise OracleInvocationError("oracle comparison returned no valid JSON report") from error
    if not isinstance(report, dict) or not isinstance(report.get("comparison"), dict):
        raise OracleInvocationError("oracle comparison returned an invalid JSON report")
    for name in objects:
        row = report["comparison"].get(f"original vs {name}")
        if not isinstance(row, dict) or not isinstance(row.get("differ"), int):
            raise OracleInvocationError(f"oracle comparison omitted the result for {name}")
    return report


def toward_original(harvest: Harvest, unit: str, symbol: str, before: bytes, after: bytes) -> dict | None:
    """Cases in which the function differs from the original code, compiled from
    `before` and from `after`: {"before": n, "after": m, "cases": N}. A rewrite that
    changes behavior is still an improvement when it differs from the original in
    fewer cases, as when it restores how the original treats NaN."""
    counts = original_differences(harvest, unit, symbol, {"before": before, "after": after})
    if not counts or len(counts) < 2:
        return None
    return {**counts, "cases": CASES}


def gate(harvest: Harvest, unit: str, symbol: str, before: bytes, after: bytes) -> tuple[bool, dict | None]:
    """Whether a partial gain may be kept, with the evidence: it behaves like
    `before`, or it changes behavior and differs from the original code in fewer
    cases than `before` does. (True, None) when no oracle is configured."""
    try:
        same = check(harvest, unit, symbol, before, after)
    except OracleInvocationError as error:
        return False, {"error": str(error)}
    if same is None:
        return True, None
    record = {"same": json.loads(same.detail)}
    if same.equivalent:
        return True, record
    try:
        record["original"] = toward_original(harvest, unit, symbol, before, after)
    except OracleInvocationError as error:
        return False, {"error": str(error)}
    closer = record["original"] is not None and record["original"]["after"] < record["original"]["before"]
    return closer, record


def gate_objects(harvest: Harvest, unit: str, symbol: str, before: Path, after: Path) -> tuple[bool, dict | None]:
    """Apply the behavior gate to compiled overlay objects."""
    try:
        same = check_objects(harvest, unit, symbol, before, after)
    except OracleInvocationError as error:
        _check_baseline(harvest, unit, symbol, before)
        return False, {"error": str(error)}
    if same is None:
        return True, None
    record = {"same": json.loads(same.detail)}
    if same.equivalent:
        return True, record
    try:
        report = original_comparison_objects(harvest, symbol, {"before": before, "after": after})
    except OracleInvocationError as error:
        _original_baseline(harvest, symbol, before)
        return False, {"error": str(error)}
    comparison = report["comparison"]
    record["original"] = {
        "before": comparison["original vs before"]["differ"],
        "after": comparison["original vs after"]["differ"],
        "cases": CASES,
        "outcomes": report.get("outcomes", {}),
    }
    return record["original"]["after"] < record["original"]["before"], record
