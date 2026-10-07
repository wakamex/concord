"""The search loop: converge a function's source toward a byte match.

Each round diffs the current source's compile against the target, takes the
transforms for the causes the diff reports, compiles every rewrite they produce
in one batch, and moves to the best rewrite that improves the function without
losing any function of the unit that was already exact. It stops at an exact
match, when no rewrite improves, or when the rounds run out.

Only rewrites that cannot change behavior are used so far (swapping comparison
operands; C++ leaves the evaluation order of those operands unspecified), so no
candidate needs the equivalence oracle yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from concord.harvest import Evaluation, Harvest
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
    return (diff.exact, diff.score, -len(diff.findings))


def search(harvest: Harvest, unit: str, symbol: str, rounds: int = 4) -> SearchResult:
    source = (harvest.root / "src" / harvest.source(unit)).read_bytes()
    baseline = harvest.evaluate(unit, {"baseline": source})["baseline"]
    if baseline.error:
        raise RuntimeError(f"the unit's own source does not compile:\n{baseline.error}")
    protected = baseline.exact_functions()
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
        best = None
        for n, rewrite in enumerate(rewrites):
            e = evaluations[f"r{n}"]
            if e.error or not protected <= e.exact_functions():
                continue
            d = _diff(harvest, e, symbol)
            if _key(d) > _key(current) and (best is None or _key(d) > _key(best[1])):
                best = (rewrite, d)
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


def _diff(harvest: Harvest, evaluation: Evaluation, symbol: str) -> DiffResult:
    verdict = evaluation.verdict(symbol)
    if verdict is None:
        raise KeyError(f"{symbol} is not in the compiled unit")
    return harvest.diff(verdict, evaluation.object)
