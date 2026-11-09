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

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from tree_sitter import Node

from concord.harvest import Evaluation, FunctionVerdict, function_key
from concord.model import DiffResult
from concord.transforms import TRANSFORMS, Rewrite


class Target(Protocol):
    """What a search needs from a target project's checkout: its units' sources, a
    compile of candidate sources compared by the project's own matcher, and the
    place of a symbol's definition in a source text. concord.harvest.Harvest and
    concord.gruntz.Gruntz provide it."""

    root: Path
    compiler: str  # the knowledge base's name for the project's compiler
    log: str  # the file under results/ its searches are logged to
    oracle: bool  # whether an inexact rewrite's behavior can be checked against its previous form
    net_rule: bool  # whether a search may lower another function's score while the unit gains (Goal 2's net rule)
    rewrites: list[Callable]  # the transforms its compiler accepts

    def source(self, unit: str) -> str: ...
    def source_path(self, unit: str) -> Path: ...
    def verdicts(self, unit: str | None = None) -> Iterator[FunctionVerdict]: ...
    def evaluate(self, unit: str, sources: dict[str, bytes]) -> dict[str, Evaluation]: ...
    def diff(self, verdict: FunctionVerdict, candidate_object: Path | None = None) -> DiffResult: ...
    def find_function(self, source: bytes, symbol: str) -> Node: ...
    def debug_objects(self, units: list[str], sources: dict[str, bytes] | None = None) -> dict[str, Path]: ...
    def source_lines(self, unit: str, symbol: str, source: bytes, offsets: list[int]) -> set[int]: ...


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


def search(target: Target, unit: str, symbol: str, rounds: int = 4, source: bytes | None = None) -> SearchResult:
    """Search from the unit's source in the checkout, or from `source`."""
    source = source if source is not None else target.source_path(unit).read_bytes()
    baseline = target.evaluate(unit, {"baseline": source})["baseline"]
    if baseline.error:
        raise RuntimeError(f"the unit's own source does not compile:\n{baseline.error}")
    protected = baseline.exact_functions()
    others = other_scores(baseline, symbol)
    current = _diff(target, baseline, symbol)
    result = SearchResult(current, current, source)
    for _ in range(rounds):
        if current.exact:
            result.reason = "exact"
            break
        causes = {f.cause for f in current.findings}
        try:
            scope = target.find_function(source, symbol)
            rewrites = [r for cause in causes for t in TRANSFORMS.get(cause, []) if t in target.rewrites for r in t(source, scope)]
        except KeyError:
            result.reason = "the function is not defined in the unit's own source (an inline copy from a header?)"
            break
        if not rewrites:
            result.reason = "no rewrite applies to the function's own source for the remaining causes"
            break
        evaluations = target.evaluate(unit, {f"r{n}": r.source for n, r in enumerate(rewrites)})
        result.tried += len(rewrites)
        result.transforms |= {r.transform for r in rewrites}
        improving = []
        for n, rewrite in enumerate(rewrites):
            e = evaluations[f"r{n}"]
            if e.error or not protected <= e.exact_functions():
                continue
            d = _diff(target, e, symbol)
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
    verdict = evaluation.verdict(symbol)
    excluded = function_key(verdict.row) if verdict is not None else symbol
    return {key: score for key, score in evaluation.fuzzy_by_key().items() if key != excluded}


def lowered(before: dict[str, float], evaluation: Evaluation, symbol: str) -> dict[str, tuple[float, float]]:
    """The other functions of the unit whose objdiff score a candidate lowers, each
    with its score before and after."""
    after = other_scores(evaluation, symbol)
    return {name: (score, after.get(name, 0.0)) for name, score in before.items() if after.get(name, 0.0) < score}


def worse_elsewhere(before: dict[str, float], evaluation: Evaluation, symbol: str) -> bool:
    """Whether a candidate lowers objdiff's score of any other function of the unit."""
    return bool(lowered(before, evaluation, symbol))


def net_gain(before: Evaluation, after: Evaluation) -> float:
    """The unit's size-weighted change in objdiff score, in target bytes, between two
    compiles: the measure Goal 2's net rule weighs a lower score against a gain by."""
    sizes = {function_key(row): row.get("size", 0) for section in before.sections for row in section.get("functions", [])}
    after_scores = after.fuzzy_by_key()
    return sum(sizes.get(key, 0) * (after_scores.get(key, 0.0) - score) / 100 for key, score in before.fuzzy_by_key().items())


def _diff(target: Target, evaluation: Evaluation, symbol: str) -> DiffResult:
    verdict = evaluation.verdict(symbol)
    if verdict is None:
        raise KeyError(f"{symbol} is not in the compiled unit")
    diff = target.diff(verdict, evaluation.object)
    diff.fuzzy = evaluation.fuzzy.get(symbol)
    return diff


def attributed_lines(target: Target, unit: str, symbol: str, source: bytes, diff: DiffResult) -> set[int]:
    """Lines of the unit's source that the differing code of `symbol` comes from:
    the unit is compiled from `source` with line information and each finding of
    the diff is traced to its lines. Empty when no finding has a place in the
    candidate or the compile fails."""
    offsets = [finding.candidate for finding in diff.findings if finding.candidate is not None]
    return target.source_lines(unit, symbol, source, offsets) if offsets else set()
