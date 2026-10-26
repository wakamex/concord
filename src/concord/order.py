"""Definition-order search, through Harvest's `hv search`.

GCC 4.4 processes a unit's functions in an order that depends on where they are
defined, and that order changes inlining and register allocation, also in
header code whose own source is fixed. `hv search` searches top-level definition
orders of one unit and verifies an improved winner with a fresh canonical
compile; this module runs it, reads its evidence, and applies a verified winner
only when it is a pure reorder of the unchanged source.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from concord.harvest import Harvest


@dataclass
class OrderResult:
    unit: str  # source path under src/
    reason: str
    run: Path | None = None
    baseline: set[str] = field(default_factory=set)
    verified: set[str] | None = None  # exact functions of the verified winner

    @property
    def gained(self) -> set[str]:
        return (self.verified or set()) - self.baseline

    @property
    def lost(self) -> set[str]:
        return self.baseline - self.verified if self.verified is not None else set()


def _exact(path: Path) -> set[str]:
    data = json.loads(path.read_text())
    data = data.get("result", data)
    return {f["symbol"] for s in data["sections"] for f in s.get("functions", []) if f["exact"]}


def search_order(
    harvest: Harvest, unit: str, budget: int = 128, restarts: int = 2, seed: int = 0, batch: int = 32
) -> OrderResult:
    source = harvest.source(unit)
    command = [
        "uv", "--no-config", "run", "--locked", "hv", "search", source,
        "--budget", str(budget), "--restarts", str(restarts), "--seed", str(seed), "--batch", str(batch),
    ]  # fmt: skip
    done = subprocess.run(command, cwd=harvest.root, capture_output=True, text=True, check=False)
    output = done.stdout + done.stderr
    if "at least two explicit blocks" in output:
        return OrderResult(source, "a single definition, nothing to reorder")
    match = re.search(r"^evidence\s+(\S+)", output, re.MULTILINE)
    if done.returncode or not match:
        return OrderResult(source, f"hv search failed: {output.strip()[-300:]}")
    run = Path(match.group(1))
    summary = json.loads((run / "summary.json").read_text())
    result = OrderResult(source, summary["reason"], run, _exact(run / "baseline.json"))
    if (run / "verification.json").exists():
        result.verified = _exact(run / "verification.json")
    return result


def apply_order(harvest: Harvest, result: OrderResult) -> None:
    """Write a verified winner over the unit's source, refusing anything but a
    reorder of the exact source the search started from."""
    if not result.gained or result.lost:
        raise ValueError(f"{result.unit}: no verified gain to apply")
    path = harvest.root / "src" / result.unit
    current = path.read_bytes()
    context = json.loads((result.run / "context.json").read_text())
    if hashlib.sha256(current).hexdigest() != context["blocks"]["source_sha256"]:
        raise ValueError(f"{result.unit} changed since the search")
    candidate = (result.run / "candidate.cpp").read_bytes()
    if sorted(candidate.splitlines()) != sorted(current.splitlines()):
        raise ValueError(f"{result.unit}: the winner is not a pure reorder")
    path.write_bytes(candidate)


def definition_placements(source: bytes, method: str) -> dict[str, bytes]:
    """Move one definition before or after sibling definitions, preserving its body."""
    from concord.blockers import _definitions

    definitions = _definitions(source)
    if method not in definitions:
        raise ValueError(f"definition not found: {method}")
    node = definitions[method]
    candidates = {}
    for name, sibling in definitions.items():
        if sibling == node or sibling.parent != node.parent:
            continue
        for edge, position in [('before', sibling.start_byte), ('after', sibling.end_byte)]:
            start, end = node.start_byte, node.end_byte
            if position < start:
                text = source[:position] + b'\n' + source[start:end] + b'\n' + source[position:start] + source[end:]
            else:
                text = source[:start] + source[end:position] + b'\n' + source[start:end] + b'\n' + source[position:]
            if text != source and text not in candidates.values():
                candidates[f'{edge} {name}'] = text
    return candidates


def measure_placements(harvest: Harvest, unit: str, method: str, reference: Path,
                       output: Path, budget: int = 72) -> dict:
    """Measure one definition's placements against separately supplied reference source.

    The best measured source is saved for review, never applied. A fresh compile
    verifies its unit scores; a normalized whole-build gate decides acceptance.
    """
    if budget < 1:
        raise ValueError('budget must be positive')
    path = harvest.root / 'src' / unit
    original, baseline_source = path.read_bytes(), reference.read_bytes()
    candidates = dict(list(definition_placements(original, method).items())[:budget])
    output.parent.mkdir(parents=True, exist_ok=True)
    evaluations = harvest.evaluate(unit, {'reference': baseline_source, 'current': original})
    baseline, current = evaluations['reference'], evaluations['current']
    if baseline.error or current.error:
        raise ValueError('reference and current sources must compile')

    def details(value):
        worse = {s: [old, value.fuzzy.get(s, 0)] for s, old in baseline.fuzzy.items()
                 if value.fuzzy.get(s, 0) < old}
        return {'error': value.error, 'object': str(value.object),
                'object_sha256': hashlib.sha256(value.object.read_bytes()).hexdigest() if value.object else None,
                'fuzzy': value.fuzzy,
                'worse': worse, 'lost_exact': sorted(baseline.exact_functions() - value.exact_functions()),
                'exact': len(value.exact_functions())}

    def rank(value):
        row = details(value)
        return len(set(row['worse']) | set(row['lost_exact'])), sum(a - b for a, b in row['worse'].values()), -row['exact']

    best, selected, winner = current, 'current', original
    rows = {'current': details(current)}
    # A batch of two bounds memory even when a caller uses a larger driver pool.
    names = list(candidates)
    for offset in range(0, len(names), 2):
        batch = names[offset:offset + 2]
        values = harvest.evaluate(unit, {str(i): candidates[name] for i, name in enumerate(batch)})
        for i, name in enumerate(batch):
            value = values[str(i)]
            rows[name] = details(value)
            if not value.error and rank(value) < rank(best):
                best, selected, winner = value, name, candidates[name]
    verified = harvest.evaluate(unit, {'verification': winner})['verification']
    if verified.error or verified.fuzzy != best.fuzzy or verified.exact_functions() != best.exact_functions():
        raise ValueError('fresh canonical compile disagrees with selected placement')
    if path.read_bytes() != original:
        raise ValueError('source changed during placement measurement')
    winner_path = output.with_suffix('.cpp')
    winner_path.write_bytes(winner)
    report = {'schema': 'concord.definition-placement.v1', 'unit': unit, 'method': method,
              'source_sha256': hashlib.sha256(original).hexdigest(),
              'reference_sha256': hashlib.sha256(baseline_source).hexdigest(),
              'budget': budget, 'evaluated': len(candidates), 'selected': selected,
              'candidate': str(winner_path), 'reference': details(baseline),
              'verification': details(verified), 'trials': rows,
              'acceptance': 'normalized-whole-build-gate-required'}
    output.write_text(json.dumps(report, indent=2) + '\n')
    return report
