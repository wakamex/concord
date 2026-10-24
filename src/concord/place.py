"""Refresh references and place called template sections without re-seeding source."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from tree_sitter import Node

from concord.harvest import BUILD, Harvest
from concord.irrlicht import translate
from concord.search import other_scores, worse_elsewhere
from concord.seed import IRRLICHT, _match, _place_called_sections, _register
from concord.transforms import find_function


def _strings(node: Node) -> list[Node]:
    found, stack = [], [node]
    while stack:
        item = stack.pop()
        if item.type == "string_literal":
            found.append(item)
        else:
            stack.extend(item.named_children)
    return sorted(found, key=lambda n: n.start_byte)


def literal_candidate(current: bytes, ancestor: bytes, symbol: str) -> bytes | None:
    """Restore corresponding ancestor literals in one function. The caller must
    prove the whole repaired function exact before retaining this source."""
    try:
        ours, theirs = find_function(current, symbol), find_function(ancestor, symbol)
    except KeyError:
        return None
    old, new = _strings(ours), _strings(theirs)
    if len(old) != len(new):
        return None
    edits = [(a.start_byte, a.end_byte, b.text) for a, b in zip(old, new, strict=True) if a.text != b.text]
    if not edits:
        return None
    source = current
    for start, end, text in reversed(edits):
        source = source[:start] + text + source[end:]
    return source


def restore_literals(harvest: Harvest, unit: str) -> list[str]:
    """Keep an ancestor-literal repair only when it makes the target function
    exact and does not lower any other compiled function of the unit."""
    ancestor = harvest.root / IRRLICHT / "source" / "Irrlicht" / Path(unit).name
    if not ancestor.exists():
        return []
    path = harvest.root / "src" / unit
    source = path.read_bytes()
    original = translate(ancestor.read_text(encoding="latin-1")).encode()
    candidates = {}
    for verdict in harvest.verdicts(unit):
        if not verdict.row["exact"] and (candidate := literal_candidate(source, original, verdict.symbol)) is not None:
            candidates[verdict.symbol] = candidate
    if not candidates:
        return []
    baseline = harvest.evaluate(unit, {"baseline": source})["baseline"]
    if baseline.error:
        raise RuntimeError(baseline.error)
    # One repair per pass keeps acceptance tied to the exact source compiled.
    for symbol, candidate in candidates.items():
        evaluation = harvest.evaluate(unit, {"literal": candidate})["literal"]
        verdict = evaluation.verdict(symbol)
        others = other_scores(baseline, symbol)
        if evaluation.error or verdict is None or not verdict.row["exact"]:
            continue
        if not baseline.exact_functions() <= evaluation.exact_functions() or worse_elsewhere(others, evaluation, symbol):
            continue
        if path.read_bytes() != source:
            raise RuntimeError(f"{unit}: source changed during literal verification")
        path.write_bytes(candidate)
        _match(harvest.root, unit, learn=True)
        return [symbol]
    return []


def place(harvest: Harvest, unit: str, literals: bool = False) -> dict:
    """Learn existing exact references, then verify temporary section placements.
    Preserve all existing placements. Whole-build scoring is a separate gate."""
    root = harvest.root
    config = root / "config" / BUILD / "units.toml"
    placements = dict(tomllib.loads(config.read_text()).get(unit, {}))
    report = harvest.reports / (harvest.slug(unit) + ".json")
    output = _match(root, unit, learn=True)
    sections = {}
    for _ in range(2):
        called = _place_called_sections(root, unit)
        if not called:
            break
        _register(root, unit, {**placements, **called})
        output = _match(root, unit, learn=True)
        verified = {s["name"] for s in json.loads(report.read_text())["sections"] if s.get("exact")}
        sections.update({name: address for name, address in called.items() if name in verified})
        # Learning can name the section without an explicit placement. Retain an
        # override only if the canonical comparison still leaves it unplaced.
        _register(root, unit, placements)
        output = _match(root, unit, learn=True)
        unplaced = set(json.loads(report.read_text()).get("unplaced_sections", []))
        placements.update({name: address for name, address in sections.items() if name in unplaced})
        _register(root, unit, placements)
    repaired = restore_literals(harvest, unit) if literals else []
    output = _match(root, unit, learn=True)
    return {"unit": unit, "sections": sections, "literals": repaired, "output": output}
