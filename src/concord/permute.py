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
lowers the score of any other function of the unit, is rejected. The best source
seen is kept apart from the walk.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from concord.harvest import Harvest
from concord.model import DiffResult
from concord.search import _diff, _key, attributed_lines, other_scores, worse_elsewhere
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


def _line(description: str) -> int:
    found = re.match(r"line (\d+)", description)
    return int(found[1]) if found else 0


def _distance(line: int, lines: set[int]) -> int:
    return min((abs(line - at) for at in lines), default=0)


def singles(source: bytes, symbol: str, lines: set[int]) -> list[tuple[bytes, str]]:
    """Every source one rewrite away, with the rewrite's description, nearest the
    given lines first and otherwise in source order."""
    scope = find_function(source, symbol)
    found: dict[bytes, tuple[int, int, str]] = {}
    for transform in ALL:
        for rewrite in transform(source, scope):
            line = _line(rewrite.description)
            found.setdefault(rewrite.source, (_distance(line, lines), line, f"{rewrite.transform} {rewrite.description}"))
    return [(text, key[2]) for text, key in sorted(found.items(), key=lambda item: item[1])]


def _mutate(source: bytes, symbol: str, rng: random.Random, depth: int, lines: frozenset[int] = frozenset()) -> tuple[bytes, list[str]]:
    """One to `depth` random rewrites; two times in three a rewrite within two lines
    of `lines` is preferred when there is one."""
    steps = []
    for _ in range(rng.randint(1, depth)):
        scope = find_function(source, symbol)
        rewrites = [r for t in ALL for r in t(source, scope)]
        if not rewrites:
            break
        near = [r for r in rewrites if _distance(_line(r.description), set(lines)) <= 2] if lines else []
        pool = near if near and rng.random() < 2 / 3 else rewrites
        kinds = sorted({r.transform for r in pool})
        kind = rng.choice(kinds)  # a kind first, so one with many sites does not crowd out the rest
        rewrite = rng.choice([r for r in pool if r.transform == kind])
        source = rewrite.source
        steps.append(f"{rewrite.transform} {rewrite.description}")
    return source, steps


def permute(
    harvest: Harvest,
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
    source = source if source is not None else (harvest.root / "src" / harvest.source(unit)).read_bytes()
    baseline = harvest.evaluate(unit, {"baseline": source})["baseline"]
    if baseline.error:
        raise RuntimeError(f"the unit's own source does not compile:\n{baseline.error}")
    protected = baseline.exact_functions()
    others = other_scores(baseline, symbol)
    current = (source, _diff(harvest, baseline, symbol), [])
    result = PermuteResult(current[1], current[1], source)
    seen = {source}

    def consider(candidates: dict[str, tuple[bytes, list[str]]], ties: bool) -> bool:
        """Compile the candidates and move to the best one that scores better than
        the current source, or as well when `ties`; whether the search moved."""
        nonlocal current
        evaluations = harvest.evaluate(unit, {name: text for name, (text, _) in candidates.items()})
        result.tried += len(candidates)
        scored = []
        for name, (text, steps) in candidates.items():
            e = evaluations[name]
            if e.error or not protected <= e.exact_functions():
                continue
            scored.append((_diff(harvest, e, symbol), text, steps, e))
        # the best candidates that keep every other function of the unit at least as good
        floor = _key(current[1])
        for key in sorted({_key(d) for d, _, _, _ in scored if (_key(d) >= floor if ties else _key(d) > floor)}, reverse=True):
            tied = [s for s in scored if _key(s[0]) == key]
            rng.shuffle(tied)
            choice = next((s for s in tied if not worse_elsewhere(others, s[3], symbol)), None)
            if choice is not None:
                d, text, steps, _ = choice
                current = (text, d, steps)
                if _key(d) > _key(result.best):
                    result.best, result.source, result.steps = d, text, steps
                return True
        return False

    def located() -> set[int]:
        try:
            return attributed_lines(harvest, unit, symbol, current[0], current[1])
        except (OSError, KeyError, RuntimeError):
            return set()

    lines = located() if directed else set()
    moved = directed
    while moved and result.tried < budget and not result.best.exact:
        moved = False
        pending = [(text, why) for text, why in singles(current[0], symbol, lines) if text not in seen]
        while pending and result.tried < budget and not moved:
            chunk, pending = pending[: min(batch, budget - result.tried)], pending[batch:]
            seen.update(text for text, _ in chunk)
            moved = consider({f"s{n}": (text, [*current[2], why]) for n, (text, why) in enumerate(chunk)}, ties=False)
        if moved:
            lines = located()
    while result.tried < budget and not result.best.exact:
        candidates = {}
        for _ in range(min(batch, budget - result.tried) * 4):
            text, steps = _mutate(current[0], symbol, rng, depth, frozenset(lines))
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


def near_misses(harvest: Harvest, minimum: float) -> list[tuple[str, str, float]]:
    """(unit source, symbol, score) of every inexact function defined in its unit's
    own source that scores at least `minimum`, best first. Thunks and the
    constructor or destructor variants that share a listed twin are left out."""
    found = {}
    for verdict in harvest.verdicts():
        if verdict.row["exact"] or verdict.symbol.startswith(("_ZTh", "_ZTv")):
            continue
        unit = harvest.source(verdict.unit)
        try:
            score = harvest.diff(verdict).score
            find_function((harvest.root / "src" / unit).read_bytes(), verdict.symbol)
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
