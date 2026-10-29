"""The search loop: converge a function's source toward a byte match.

Each round diffs the current source's compile against the target, takes the
transforms for the causes the diff reports, compiles every rewrite they produce
in one batch, and moves to the best rewrite that improves the function without
losing any function of the unit that was already exact or lowering objdiff's
score of any other. It stops at an exact
match, when no rewrite improves, or when the rounds run out.

Only rewrites that cannot change behavior are used so far (swapping comparison
operands; C++ leaves the evaluation order of those operands unspecified), so no
candidate needs the equivalence oracle yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from concord.diff import read_function
from concord.harvest import Evaluation, Harvest
from concord.lines import inline_chains
from concord.model import DiffResult
from concord.transforms import TRANSFORMS, Rewrite, find_function


@dataclass
class Step:
    rewrite: Rewrite
    diff: DiffResult


@dataclass
class SearchResult:
    baseline: DiffResult
    best: DiffResult
    source: bytes
    steps: list[Step] = field(default_factory=list)
    tried: int = 0
    reason: str = ""
    transforms: set[str] = field(default_factory=set)  # idiom ids of every rewrite compiled


def _key(diff: DiffResult) -> tuple:
    """Exact first, then objdiff's score (what upstream reports), then concord's."""
    return (diff.exact, diff.fuzzy or 0.0, diff.score, -len(diff.findings))


def search(harvest: Harvest, unit: str, symbol: str, rounds: int = 4, source: bytes | None = None) -> SearchResult:
    """Search from the unit's source in the checkout, or from `source`."""
    source = source if source is not None else (harvest.root / "src" / harvest.source(unit)).read_bytes()
    baseline = harvest.evaluate(unit, {"baseline": source})["baseline"]
    if baseline.error:
        raise RuntimeError(f"the unit's own source does not compile:\n{baseline.error}")
    protected = baseline.exact_functions()
    others = other_scores(baseline, symbol)
    current = _diff(harvest, baseline, symbol)
    result = SearchResult(current, current, source)
    for _ in range(rounds):
        if current.exact:
            result.reason = "exact"
            break
        causes = {f.cause for f in current.findings}
        try:
            rewrites = [r for cause in causes for t in TRANSFORMS.get(cause, []) for r in t(source, find_function(source, symbol))]
        except KeyError:
            result.reason = "the function is not defined in the unit's own source (an inline copy from a header?)"
            break
        if not rewrites:
            result.reason = "no rewrite applies to the function's own source for the remaining causes"
            break
        evaluations = harvest.evaluate(unit, {f"r{n}": r.source for n, r in enumerate(rewrites)})
        result.tried += len(rewrites)
        result.transforms |= {r.transform for r in rewrites}
        improving = []
        for n, rewrite in enumerate(rewrites):
            e = evaluations[f"r{n}"]
            if e.error or not protected <= e.exact_functions():
                continue
            d = _diff(harvest, e, symbol)
            if _key(d) > _key(current):
                improving.append((rewrite, d, e))
        improving.sort(key=lambda c: _key(c[1]), reverse=True)
        best = next(((r, d) for r, d, e in improving if not worse_elsewhere(others, e, symbol)), None)
        if best is None:
            result.reason = "no rewrite improves the function"
            break
        source, current = best[0].source, best[1]
        result.steps.append(Step(*best))
        result.best, result.source = current, source
    else:
        result.reason = result.reason or "round limit"
    if current.exact:
        result.reason = "exact"
    return result


def other_scores(evaluation: Evaluation, symbol: str) -> dict[str, float]:
    """objdiff scores of the unit's functions other than the one searched."""
    return {name: score for name, score in evaluation.fuzzy.items() if name != symbol}


def worse_elsewhere(before: dict[str, float], evaluation: Evaluation, symbol: str) -> bool:
    """Whether a candidate lowers objdiff's score of any other function of the unit."""
    after = other_scores(evaluation, symbol)
    return any(after.get(name, 0.0) < score for name, score in before.items())


def net_gain(before: Evaluation, after: Evaluation) -> float:
    """The unit's size-weighted change in objdiff score, in target bytes, between two
    compiles: the measure Goal 2's net rule weighs a lower score against a gain by."""
    sizes = {row["symbol"]: row.get("size", 0) for section in before.sections for row in section.get("functions", [])}
    return sum(sizes.get(name, 0) * (after.fuzzy.get(name, 0.0) - score) / 100 for name, score in before.fuzzy.items())


def _diff(harvest: Harvest, evaluation: Evaluation, symbol: str) -> DiffResult:
    verdict = evaluation.verdict(symbol)
    if verdict is None:
        raise KeyError(f"{symbol} is not in the compiled unit")
    diff = harvest.diff(verdict, evaluation.object)
    diff.fuzzy = evaluation.fuzzy.get(symbol)
    return diff


def attributed_lines(harvest: Harvest, unit: str, symbol: str, source: bytes, diff: DiffResult) -> set[int]:
    """Lines of the unit's source that the differing code of `symbol` comes from:
    the unit is compiled from `source` with debug information and each finding of
    the diff is traced through its inline chain. Empty when no finding has a place
    in the candidate or the debug compile fails."""
    unit = harvest.source(unit)
    located = [finding for finding in diff.findings if finding.candidate is not None]
    obj = harvest.debug_objects([unit], {unit: source}).get(unit) if located else None
    if obj is None:
        return set()
    function = read_function(obj, symbol)
    lines = set()
    for chain in inline_chains(obj, function.section, [function.start + finding.candidate for finding in located]):
        lines |= {frame.line for frame in chain if frame.path == f"src/{unit}"}
    return lines
