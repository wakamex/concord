"""Random multi-step search over source rewrites, in the manner of decomp-permuter.

The greedy search (search.py) takes one rewrite at a time, and only one that
improves the function. GCC 4.4's register choices and compare operand order
respond to the source chaotically: they follow the order values are created in
and the SSA names recycled before them, so the rewrite that matches often only
helps after another one that changes nothing on its own.

Each candidate here applies one to `depth` random rewrites, from every transform,
to the current source. The best candidate of a batch replaces the current source
when it scores at least as well, so the walk drifts across plateaus instead of
stopping on them; a candidate that loses a function of the unit that was exact
is rejected. The best source seen is kept apart from the walk.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from concord.harvest import Harvest
from concord.model import DiffResult
from concord.search import _diff, _key
from concord.transforms import ALL, find_function


@dataclass
class PermuteResult:
    baseline: DiffResult
    best: DiffResult
    source: bytes
    steps: list[str] = field(default_factory=list)  # rewrites from the unit's source to `source`
    tried: int = 0
    reason: str = ""


def _mutate(source: bytes, symbol: str, rng: random.Random, depth: int) -> tuple[bytes, list[str]]:
    steps = []
    for _ in range(rng.randint(1, depth)):
        scope = find_function(source, symbol)
        choices = [rewrites for t in ALL if (rewrites := list(t(source, scope)))]
        if not choices:
            break
        rewrite = rng.choice(rng.choice(choices))
        source = rewrite.source
        steps.append(f"{rewrite.transform} {rewrite.description}")
    return source, steps


def permute(
    harvest: Harvest, unit: str, symbol: str, budget: int = 256, batch: int = 32, seed: int = 0, depth: int = 3
) -> PermuteResult:
    rng = random.Random(seed)
    source = (harvest.root / "src" / harvest.source(unit)).read_bytes()
    baseline = harvest.evaluate(unit, {"baseline": source})["baseline"]
    if baseline.error:
        raise RuntimeError(f"the unit's own source does not compile:\n{baseline.error}")
    protected = baseline.exact_functions()
    current = (source, _diff(harvest, baseline, symbol), [])
    result = PermuteResult(current[1], current[1], source)
    seen = {source}
    while result.tried < budget and not result.best.exact:
        candidates = {}
        for _ in range(min(batch, budget - result.tried) * 4):
            text, steps = _mutate(current[0], symbol, rng, depth)
            if text not in seen:
                seen.add(text)
                candidates[f"p{len(candidates)}"] = (text, current[2] + steps)
            if len(candidates) == min(batch, budget - result.tried):
                break
        if not candidates:
            result.reason = "no rewrite gives a new source"
            break
        evaluations = harvest.evaluate(unit, {name: text for name, (text, _) in candidates.items()})
        result.tried += len(candidates)
        scored = []
        for name, (text, steps) in candidates.items():
            e = evaluations[name]
            if e.error or not protected <= e.exact_functions():
                continue
            scored.append((_diff(harvest, e, symbol), text, steps))
        if not scored:
            continue
        top = max(_key(d) for d, _, _ in scored)
        if top >= _key(current[1]):
            d, text, steps = rng.choice([s for s in scored if _key(s[0]) == top])
            current = (text, d, steps)
            if _key(d) > _key(result.best):
                result.best, result.source, result.steps = d, text, steps
    result.reason = result.reason or ("exact" if result.best.exact else "budget spent")
    return result
