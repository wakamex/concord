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
import os
import shlex
import shutil
import subprocess

from concord.harvest import Harvest
from concord.model import EquivalenceResult

VARIABLE = "CONCORD_ORACLE"
# A float comparison changed so it differs only for NaN can show in about 0.2% of
# generated cases (CDropshipEntity::updateLogic: 4 of 2,000); 5,000 cases catch
# it with near certainty in a few seconds per function.
CASES = 5000


def command() -> str | None:
    """The check to run: CONCORD_ORACLE, or harvest-oracle when it is installed.
    Setting CONCORD_ORACLE to an empty string turns the check off."""
    if VARIABLE in os.environ:
        return os.environ[VARIABLE] or None
    return "harvest-oracle" if shutil.which("harvest-oracle") else None


def configured() -> bool:
    return command() is not None


def check(harvest: Harvest, unit: str, symbol: str, before: bytes, after: bytes) -> EquivalenceResult | None:
    """Whether the function behaves the same compiled from `before` and from `after`,
    or None when no oracle is configured."""
    prefix = command()
    if prefix is None:
        return None
    evaluations = harvest.evaluate(unit, {"before": before, "after": after})
    for name, e in evaluations.items():
        if e.error:
            return EquivalenceResult(False, "differential-test", f"{name} does not compile")
    done = subprocess.run(
        [*shlex.split(prefix), "check", str(evaluations["before"].object), str(evaluations["after"].object),
         "--symbol", symbol, "--unit", unit, "--cases", str(CASES)],
        cwd=harvest.root, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if done.returncode not in (0, 1):
        raise RuntimeError(f"oracle failed ({done.returncode}):\n{done.stderr[-2000:]}")
    report = json.loads(done.stdout.strip().splitlines()[-1])
    return EquivalenceResult(report["verdict"] == "agree", "differential-test", json.dumps(report))


def toward_original(harvest: Harvest, unit: str, symbol: str, before: bytes, after: bytes) -> dict | None:
    """Cases in which the function differs from the original executable's own code,
    compiled from `before` and from `after`: {"before": n, "after": m, "cases": N},
    or None when no oracle is configured. A rewrite that changes behavior is still
    an improvement when it differs from the original in fewer cases, as when it
    restores how the original treats NaN."""
    prefix = command()
    if prefix is None:
        return None
    evaluations = harvest.evaluate(unit, {"before": before, "after": after})
    if any(e.error for e in evaluations.values()):
        return None
    done = subprocess.run(
        [*shlex.split(prefix), "compare", symbol, "original=image",
         f"before={evaluations['before'].object}", f"after={evaluations['after'].object}", "--cases", str(CASES)],
        cwd=harvest.root, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if done.returncode not in (0, 1):
        raise RuntimeError(f"oracle failed ({done.returncode}):\n{done.stderr[-2000:]}")
    comparison = json.loads(done.stdout)["comparison"]
    return {
        "before": comparison["original vs before"]["differ"],
        "after": comparison["original vs after"]["differ"],
        "cases": CASES,
    }


def gate(harvest: Harvest, unit: str, symbol: str, before: bytes, after: bytes) -> tuple[bool, dict | None]:
    """Whether a partial gain may be kept, with the evidence: it behaves like
    `before`, or it changes behavior and differs from the original code in fewer
    cases than `before` does. (True, None) when no oracle is configured."""
    same = check(harvest, unit, symbol, before, after)
    if same is None:
        return True, None
    record = {"same": json.loads(same.detail)}
    if same.equivalent:
        return True, record
    record["original"] = toward_original(harvest, unit, symbol, before, after)
    closer = record["original"] is not None and record["original"]["after"] < record["original"]["before"]
    return closer, record
