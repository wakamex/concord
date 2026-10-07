"""Definition-order search for a header shared by many units.

Inline functions and templates defined in a header are emitted in every unit
that uses them, and GCC 4.4 compiles those copies differently depending on the
order the header defines them in. A candidate reorders the header's top-level
definitions; it is judged by compiling every unit that depends on the header,
and accepted only when the total of exact functions rises and no unit loses a
function that was exact.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import tree_sitter_cpp
from tree_sitter import Language, Parser

from concord.harvest import Evaluation, Harvest

CPP = Language(tree_sitter_cpp.language())


@dataclass(frozen=True)
class Blocks:
    """Top-level definitions of a source, each with the comment lines above it.
    A permutation moves the definitions between their slots and keeps every
    other byte in place."""

    source: bytes
    names: tuple[str, ...]
    spans: tuple[tuple[int, int], ...]

    def render(self, order: tuple[int, ...]) -> bytes:
        parts, previous = [], 0
        for (start, end), index in zip(self.spans, order, strict=True):
            a, b = self.spans[index]
            parts += [self.source[previous:start], self.source[a:b]]
            previous = end
        return b"".join(parts) + self.source[previous:]


def definition_blocks(source: bytes) -> Blocks:
    tree = Parser(CPP).parse(source)
    found = []

    def visit(node):
        children = node.children
        for k, child in enumerate(children):
            if child.type in ("namespace_definition", "declaration_list", "linkage_specification", "preproc_ifdef", "preproc_if"):
                visit(child)
            elif child.type == "function_definition" or (
                child.type == "template_declaration" and any(c.type == "function_definition" for c in child.children)
            ):
                first = child
                while k > 0 and children[k - 1].type == "comment" and children[k - 1].end_point[0] + 1 >= first.start_point[0]:
                    k -= 1
                    first = children[k]
                start = source.rfind(b"\n", 0, first.start_byte) + 1
                newline = source.find(b"\n", child.end_byte)
                end = len(source) if newline < 0 else newline + 1
                found.append((start, end, child.text.split(b"{", 1)[0].strip().decode(errors="replace")))

    visit(tree.root_node)
    found.sort()
    spans = []
    for start, end, name in found:
        if spans and start < spans[-1][1]:
            continue
        spans.append((start, end, name))
    blocks = Blocks(source, tuple(n for _, _, n in spans), tuple((s, e) for s, e, _ in spans))
    if blocks.render(tuple(range(len(spans)))) != source:
        raise ValueError("the identity order does not reproduce the source")
    return blocks


@dataclass
class HeaderResult:
    header: str
    units: list[str]
    baseline: dict[str, set[str]]
    best: dict[str, set[str]]
    source: bytes
    tried: int = 0
    moves: list[str] = field(default_factory=list)

    @property
    def gained(self) -> dict[str, set[str]]:
        return {u: self.best[u] - self.baseline[u] for u in self.units if self.best[u] - self.baseline[u]}


def _move(order: tuple[int, ...], i: int, j: int) -> tuple[int, ...]:
    rest = list(order)
    block = rest.pop(i)
    rest.insert(j, block)
    return tuple(rest)


def search_header(
    harvest: Harvest, header: str, budget: int = 96, per_round: int = 16, seed: int = 0
) -> HeaderResult:
    """Hill-climb one-block moves of the header's definitions. `header` is a path
    under the checkout, such as src/ox/gui/IGUIElementInline.h."""
    units = harvest.dependents(header)
    source = (harvest.root / header).read_bytes()
    blocks = definition_blocks(source)
    baseline = _exact(harvest.evaluate_overlays({f"u{n}": (u, {}) for n, u in enumerate(units)}), units, "")
    result = HeaderResult(header, units, baseline, dict(baseline), source)
    current = tuple(range(len(blocks.names)))
    seen = {current}
    rng = random.Random(seed)
    while result.tried < budget:
        moves = [(i, j) for i in range(len(current)) for j in range(len(current)) if i != j]
        rng.shuffle(moves)
        candidates = []
        for i, j in moves:
            order = _move(current, i, j)
            if order not in seen:
                seen.add(order)
                candidates.append((order, f"{blocks.names[current[i]]} to slot {j}"))
            if len(candidates) == min(per_round, budget - result.tried):
                break
        if not candidates:
            break
        items = {
            f"c{k}u{n}": (unit, {header: blocks.render(order)})
            for k, (order, _) in enumerate(candidates)
            for n, unit in enumerate(units)
        }
        evaluations = harvest.evaluate_overlays(items)
        result.tried += len(candidates)
        best = None
        for k, (order, move) in enumerate(candidates):
            exact = _exact(evaluations, units, f"c{k}")
            if exact is None or any(not result.best[u] <= exact[u] for u in units):
                continue
            total = sum(len(s) for s in exact.values())
            if total > sum(len(s) for s in result.best.values()) and (best is None or total > best[0]):
                best = (total, order, move, exact)
        if best is not None:
            _, current, move, result.best = best
            result.moves.append(move)
            result.source = blocks.render(current)
    return result


def _exact(evaluations: dict[str, Evaluation], units: list[str], prefix: str) -> dict[str, set[str]] | None:
    exact = {}
    for n, unit in enumerate(units):
        e = evaluations[f"{prefix}u{n}"]
        if e.error:
            return None
        exact[unit] = e.exact_functions()
    return exact
