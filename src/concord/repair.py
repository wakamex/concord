"""Bring a function's behavior back to the original code where its source differs.

A search keeps a function's behavior fixed while it chases bytes, so a source
that treats NaN differently from the original stays that way. After a search,
`repair` tries each NaN-sense flip of the result (`a > b` against `!(a <= b)`)
whose compiled code differs, and keeps the one that differs least often from the
original executable's own code, run in place by the oracle, repeating while that
count falls. A flip is kept only if the function still scores above where the
search started and no other function of the unit scores lower: behavior first,
without giving back what the search found.
"""

from __future__ import annotations

from concord import oracle
from concord.diff import read_function
from concord.harvest import Harvest
from concord.search import _diff, other_scores, worse_elsewhere
from concord.transforms import find_function, flip_nan_sense


def repair(harvest: Harvest, unit: str, symbol: str, source: bytes, floor: float) -> tuple[bytes, list[dict]]:
    """The source with NaN-sense flips that move it toward the original, and a
    record of each kept flip. `floor` is the objdiff score the result must keep
    beating, normally the function's score before the search."""
    if not oracle.configured():
        return source, []
    steps = []
    while True:
        base = harvest.evaluate(unit, {"base": source})["base"]
        if base.error:
            return source, steps
        others = other_scores(base, symbol)
        code = read_function(base.object, symbol).code
        flips = list(flip_nan_sense(source, find_function(source, symbol)))
        evaluations = harvest.evaluate(unit, {f"f{n}": r.source for n, r in enumerate(flips)})
        candidates = []
        for n, flip in enumerate(flips):
            e = evaluations[f"f{n}"]
            if e.error or read_function(e.object, symbol).code == code:
                continue  # an integer comparison, or one GCC compiles the same either way
            score = _diff(harvest, e, symbol).fuzzy or 0.0
            if score > floor and not worse_elsewhere(others, e, symbol) and base.exact_functions() <= e.exact_functions():
                candidates.append((flip, score))
        counts = oracle.original_differences(
            harvest, unit, symbol, {"current": source, **{f"c{n}": flip.source for n, (flip, _) in enumerate(candidates)}}
        ) if candidates else None  # fmt: skip
        best = None
        if counts and "current" in counts:
            for n, (flip, score) in enumerate(candidates):
                after = counts.get(f"c{n}")
                if after is not None and after < counts["current"] and (best is None or after < best[1]["after"]):
                    best = (flip, {"before": counts["current"], "after": after, "cases": oracle.CASES}, score)
        if best is None:
            return source, steps
        flip, distance, score = best
        source = flip.source
        steps.append({"rewrite": flip.description, "original_differences": distance, "fuzzy": score})
