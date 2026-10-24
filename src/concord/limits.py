"""Compile a unit under lowered inliner limits to test whether the original's
inliner stopped where ours keeps going.

GCC 4.4 caps inlining by estimated unit growth: once the estimate passes
`--param inline-unit-growth` percent of the unit's size (floored at
`large-unit-insns`), and each large function's growth passes
`large-function-growth` percent (large meaning `large-function-insns`), the
inliner starts rejecting calls. A unit whose original source was larger, or
whose inline bodies the original estimated larger, keeps callees out of line
that a smaller reconstruction inlines, which shows up as call differences in
`concord inlines` and inexact functions whose bodies are right.

`scan` compiles the unchanged source once per setting in `SETTINGS` and
records under which lowered limits each function is exact. That sorts the
unit's inexact functions into:

- throttled: exact under a lowered limit but not as compiled. The body is
  right and the unit's context differs from the original's, so the fix is
  whatever makes the unit as large as the original's (the target-only
  extents and stubs `concord inlines` reports), not the limit itself.
- untouched: inexact at every setting. Inliner limits do not explain it.

The limits are a measurement, never a fix: the target build used the
canonical flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from concord.harvest import Harvest, function_key

# One name per flag set; "baseline" is the canonical compile. The sets lower
# the unit-growth cap, the large-unit floor under it, the large-function
# thresholds, and both together, in the steps that showed the mechanism.
SETTINGS = {
    "baseline": [],
    "unit-30": ["--param", "inline-unit-growth=30"],
    "unit-20": ["--param", "inline-unit-growth=20"],
    "unit-10": ["--param", "inline-unit-growth=10"],
    "unit-0": ["--param", "inline-unit-growth=0"],
    "large-8000": ["--param", "large-unit-insns=8000"],
    "large-4000": ["--param", "large-unit-insns=4000"],
    "large-2000": ["--param", "large-unit-insns=2000"],
    "fn-50": ["--param", "large-function-insns=500", "--param", "large-function-growth=50"],
    "fn-10": ["--param", "large-function-insns=500", "--param", "large-function-growth=10"],
    "all-low": [
        "--param", "inline-unit-growth=0",
        "--param", "large-unit-insns=2000",
        "--param", "large-function-insns=200",
        "--param", "large-function-growth=0",
    ],
}


@dataclass
class Scan:
    unit: str
    settings: list[str]
    exact_at: dict[str, set[str]] = field(default_factory=dict)  # symbol -> settings at which it is exact
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def throttled(self) -> dict[str, set[str]]:
        return {s: p for s, p in self.exact_at.items() if p and "baseline" not in p}

    def regressed(self, setting: str) -> list[str]:
        """Functions exact as compiled that a lowered limit loses."""
        return sorted(s for s, p in self.exact_at.items() if "baseline" in p and setting not in p)

    def exact(self, setting: str) -> int:
        return sum(1 for p in self.exact_at.values() if setting in p)


def scan(harvest: Harvest, unit: str, settings: dict[str, list[str]] | None = None) -> Scan:
    settings = settings or SETTINGS
    unit = harvest.source(unit)
    evaluations = harvest.evaluate_overlays(
        {name: (unit, {}, flags) for name, flags in settings.items()}
    )
    result = Scan(unit, list(settings))
    for name in settings:
        evaluation = evaluations[name]
        if evaluation.error:
            result.errors[name] = evaluation.error
            continue
        for section in evaluation.sections:
            for row in section.get("functions", []):
                key = function_key(row)
                result.exact_at.setdefault(key, set())
                if row["exact"]:
                    result.exact_at[key].add(name)
    return result
