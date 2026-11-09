"""Search over source rewrites: every single rewrite first, then random runs of them.

The search has two stages. The first tries each single rewrite of the current
source, starting with those nearest the lines the function's differing code
comes from (the diff points at the changed line in about four of five benchmark
cases), and starts over from any candidate that scores strictly better, so a
function two or three improving rewrites from a match is reached without
chance. The second, in the manner of decomp-permuter, applies one to `depth`
random rewrites at a time and lets the walk drift across plateaus: GCC 4.4's
register choices and compare operand order follow the order values are created
in, so the rewrite that matches often only helps after another one that changes
nothing on its own.

In both stages a candidate that loses a function of the unit that was exact, or
lowers the score of any other function of the unit, is rejected. On a target
with the net rule the second holds only while the unit as a whole does not gain
by size-weighted score, and the result names the functions it lowered. The best
source seen is kept apart from the walk.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from concord.model import DiffResult
from concord.search import (
    Target,
    _diff,
    _key,
    attributed_lines,
    lowered,
    net_gain,
    other_scores,
)
from concord.transforms import ALL, find_function

# Constructor and destructor variants compiled from the same definition: searching
# the base-object one (C2, D2) searches the others too.
TWINS = {"C1E": "C2E", "D1E": "D2E", "D0E": "D2E"}


@dataclass
class PermuteResult:
    baseline: DiffResult
    best: DiffResult
    source: bytes
    steps: list[str] = field(default_factory=list)  # rewrites from the unit's source to `source`
    tried: int = 0
    reason: str = ""
    lowered: dict[str, tuple[float, float]] = field(default_factory=dict)  # other functions `source` scores lower, before and after


def _line(description: str) -> int:
    found = re.match(r"line (\d+)", description)
    return int(found[1]) if found else 0


def _distance(line: int, lines: set[int]) -> int:
    return min((abs(line - at) for at in lines), default=0)


def singles(source: bytes, symbol: str, lines: set[int], find=find_function, rewrites=ALL) -> list[tuple[bytes, str]]:
    """Every source one rewrite away, with the rewrite's description, nearest the
    given lines first and otherwise in source order. `find` locates the symbol's
    definition and `rewrites` are the transforms to apply, both the target's."""
    scope = find(source, symbol)
    found: dict[bytes, tuple[int, int, str]] = {}
    for transform in rewrites:
        for rewrite in transform(source, scope):
            line = _line(rewrite.description)
            found.setdefault(rewrite.source, (_distance(line, lines), line, f"{rewrite.transform} {rewrite.description}"))
    return [(text, key[2]) for text, key in sorted(found.items(), key=lambda item: item[1])]


def _mutate(
    source: bytes, symbol: str, rng: random.Random, depth: int, lines: frozenset[int] = frozenset(), find=find_function, rewrites=ALL
) -> tuple[bytes, list[str]]:
    """One to `depth` random rewrites; two times in three a rewrite within two lines
    of `lines` is preferred when there is one."""
    steps = []
    for _ in range(rng.randint(1, depth)):
        scope = find(source, symbol)
        found = [r for t in rewrites for r in t(source, scope)]
        if not found:
            break
        near = [r for r in found if _distance(_line(r.description), set(lines)) <= 2] if lines else []
        pool = near if near and rng.random() < 2 / 3 else found
        kinds = sorted({r.transform for r in pool})
        kind = rng.choice(kinds)  # a kind first, so one with many sites does not crowd out the rest
        rewrite = rng.choice([r for r in pool if r.transform == kind])
        source = rewrite.source
        steps.append(f"{rewrite.transform} {rewrite.description}")
    return source, steps


def permute(
    target: Target,
    unit: str,
    symbol: str,
    budget: int = 256,
    batch: int = 32,
    seed: int = 0,
    depth: int = 3,
    source: bytes | None = None,
    directed: bool = True,
) -> PermuteResult:
    """Search from the unit's source in the checkout, or from `source`. Without
    `directed`, only the random stage runs and it ignores where the diff points."""
    rng = random.Random(seed)
    source = source if source is not None else target.source_path(unit).read_bytes()
    baseline = target.evaluate(unit, {"baseline": source})["baseline"]
    if baseline.error:
        raise RuntimeError(f"the unit's own source does not compile:\n{baseline.error}")
    protected = baseline.exact_functions()
    others = other_scores(baseline, symbol)
    current = (source, _diff(target, baseline, symbol), [])
    result = PermuteResult(current[1], current[1], source)
    seen = {source}
    find, rewrites = target.find_function, target.rewrites

    def kept(lower: dict, evaluation) -> bool:
        """Whether a candidate that lowers these other functions may be moved to."""
        return not lower or (target.net_rule and net_gain(baseline, evaluation) > 0)

    def consider(candidates: dict[str, tuple[bytes, list[str]]], ties: bool) -> bool:
        """Compile the candidates and move to the best one that scores better than
        the current source, or as well when `ties`; whether the search moved."""
        nonlocal current
        evaluations = target.evaluate(unit, {name: text for name, (text, _) in candidates.items()})
        result.tried += len(candidates)
        scored = []
        for name, (text, steps) in candidates.items():
            e = evaluations[name]
            if e.error or not protected <= e.exact_functions():
                continue
            scored.append((_diff(target, e, symbol), text, steps, e))
        # the best candidates that keep every other function of the unit at least as good
        floor = _key(current[1])
        for key in sorted({_key(d) for d, _, _, _ in scored if (_key(d) >= floor if ties else _key(d) > floor)}, reverse=True):
            tied = [s for s in scored if _key(s[0]) == key]
            rng.shuffle(tied)
            choice = next((s for s in tied if kept(lowered(others, s[3], symbol), s[3])), None)
            if choice is not None:
                d, text, steps, e = choice
                current = (text, d, steps)
                if _key(d) > _key(result.best):
                    result.best, result.source, result.steps = d, text, steps
                    result.lowered = lowered(others, e, symbol)
                return True
        return False

    def located() -> set[int]:
        try:
            return attributed_lines(target, unit, symbol, current[0], current[1])
        except (OSError, KeyError, RuntimeError):
            return set()

    lines = located() if directed else set()
    moved = directed
    while moved and result.tried < budget and not result.best.exact:
        moved = False
        pending = [(text, why) for text, why in singles(current[0], symbol, lines, find, rewrites) if text not in seen]
        while pending and result.tried < budget and not moved:
            chunk, pending = pending[: min(batch, budget - result.tried)], pending[batch:]
            seen.update(text for text, _ in chunk)
            moved = consider({f"s{n}": (text, [*current[2], why]) for n, (text, why) in enumerate(chunk)}, ties=False)
        if moved:
            lines = located()
    while result.tried < budget and not result.best.exact:
        candidates = {}
        for _ in range(min(batch, budget - result.tried) * 4):
            text, steps = _mutate(current[0], symbol, rng, depth, frozenset(lines), find, rewrites)
            if text not in seen:
                seen.add(text)
                candidates[f"p{len(candidates)}"] = (text, current[2] + steps)
            if len(candidates) == min(batch, budget - result.tried):
                break
        if not candidates:
            result.reason = "no rewrite gives a new source"
            break
        if consider(candidates, ties=True) and directed:
            lines = located()
    result.reason = result.reason or ("exact" if result.best.exact else "budget spent")
    return result


def near_misses(target: Target, minimum: float) -> list[tuple[str, str, float]]:
    """(unit source, symbol, score) of every inexact function defined in its unit's
    own source that scores at least `minimum`, best first. Thunks and the
    constructor or destructor variants that share a listed twin are left out."""
    found = {}
    for verdict in target.verdicts():
        if verdict.row["exact"] or verdict.symbol.startswith(("_ZTh", "_ZTv")):
            continue
        unit = target.source(verdict.unit)
        try:
            score = target.diff(verdict).score
            target.find_function(target.source_path(unit).read_bytes(), verdict.symbol)
        except (KeyError, FileNotFoundError):
            continue
        if minimum <= score < 100:
            found[(unit, verdict.symbol)] = score
    kept = drop_twins(found)
    return sorted(((u, s, score) for (u, s), score in found.items() if (u, s) in kept), key=lambda t: -t[2])


def drop_twins(functions) -> set[tuple[str, str]]:
    """The (unit, symbol) pairs left after removing each constructor or destructor
    variant whose base-object twin in the same unit is also listed."""
    functions = set(functions)
    return {
        (unit, symbol)
        for unit, symbol in functions
        if not any(v in symbol and (unit, symbol.replace(v, base, 1)) in functions for v, base in TWINS.items())
    }
