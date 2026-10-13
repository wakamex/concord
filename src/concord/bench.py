"""Measure how often concord returns a perturbed function to an exact match.

A target with many exact functions is an answer key. `cases` takes functions
that match, applies one behavior-preserving change to each (a mutation), and
keeps the changes that stop the function matching while every other function of
the unit still does. `solve` then runs concord's searches from the mutated source
and records whether each gets back to exact, so recovery can be counted by kind
of change and by search.

Mutations come in two groups. The first are rewrites concord can undo with a
rewrite it has (swap a comparison's operands, swap an if's branches, swap
adjacent statements, move a declaration), so they measure whether a search
finds that rewrite among the others. The second are changes it has no rewrite
to undo (name a temporary, swap the operands of a commutative operator, write a
for loop as a while loop): they measure what the searches cannot reach.
Real near misses are harder than either, since nothing says one rewrite
separates them from a match; the benchmark is a floor to compare versions of
concord against, not a forecast of yield.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

from tree_sitter import Node

from concord.harvest import Harvest
from concord.model import Cause
from concord.permute import drop_twins, permute
from concord.search import search
from concord.transforms import (
    Rewrite,
    _descendants,
    _rewrite,
    find_function,
    move_declarations,
    name_temporaries,
    swap_branches,
    swap_operands,
    swap_statements,
)

COMMUTATIVE = {"+", "*", "&", "|", "^"}
PLAIN = {"identifier", "field_expression", "number_literal"}


def commute(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`a + b` as `b + a`, for operands with no side effects."""
    for node in _descendants(scope, "binary_expression"):
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        operator = node.child_by_field_name("operator").type
        if operator in COMMUTATIVE and left.type in PLAIN and right.type in PLAIN and left.text != right.text:
            swapped = right.text + b" " + operator.encode() + b" " + left.text
            yield _rewrite("bench-commute", Cause.OPERAND_ORDER, source, node, swapped, f"{node.text.decode()} -> {swapped.decode()}")


def while_loop(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`for (init; cond; step) { body }` as `{ init; while (cond) { body step; } }`,
    for loops whose body has no continue."""
    for node in _descendants(scope, "for_statement"):
        fields = {name: node.child_by_field_name(name) for name in ("initializer", "condition", "update", "body")}
        body = fields["body"]
        if None in fields.values() or body.type != "compound_statement" or _descendants(body, "continue_statement"):
            continue
        initializer = fields["initializer"].text
        initializer += b"" if initializer.rstrip().endswith(b";") else b";"
        inner = body.text[: body.text.rindex(b"}")].rstrip() + b"\n" + fields["update"].text + b";\n}"
        replacement = b"{ " + initializer + b" while (" + fields["condition"].text + b") " + inner + b" }"
        yield _rewrite("bench-while-loop", Cause.BLOCK_ORDER, source, node, replacement, "for loop as a while loop")


# name -> (mutation, whether concord has a rewrite that undoes it)
MUTATIONS = {
    "swap comparison operands": (swap_operands, True),
    "swap if branches": (swap_branches, True),
    "swap adjacent statements": (swap_statements, True),
    "move a declaration": (move_declarations, True),
    "name a temporary": (name_temporaries, False),  # no rewrite removes a named temporary
    "swap commutative operands": (commute, False),
    "for loop as while loop": (while_loop, False),
}


@dataclass
class Case:
    unit: str
    symbol: str
    mutation: str
    description: str
    source: bytes  # the unit's source with the one change
    score: float  # the function's objdiff score after the change


def functions(harvest: Harvest) -> list[tuple[str, str]]:
    """(unit source, symbol) of every exact function defined in its unit's own source."""
    found = set()
    sources: dict[str, bytes] = {}
    for verdict in harvest.verdicts():
        if not verdict.row["exact"] or verdict.symbol.startswith(("_ZTh", "_ZTv", "_GLOBAL__")):
            continue
        unit = harvest.source(verdict.unit)
        try:
            if unit not in sources:
                sources[unit] = (harvest.root / "src" / unit).read_bytes()
            find_function(sources[unit], verdict.symbol)
        except (KeyError, FileNotFoundError):
            continue
        found.add((unit, verdict.symbol))
    return sorted(drop_twins(found))


def cases(harvest: Harvest, sample: list[tuple[str, str]], seed: int = 0) -> list[Case]:
    """One case per function and mutation that applies and stops only that
    function matching. The rewrite used is chosen at random among those the
    mutation offers."""
    rng = random.Random(seed)
    found = []
    by_unit: dict[str, list[str]] = {}
    for unit, symbol in sample:
        by_unit.setdefault(unit, []).append(symbol)
    for unit, symbols in sorted(by_unit.items()):
        source = (harvest.root / "src" / unit).read_bytes()
        candidates = {}
        for symbol in symbols:
            scope = find_function(source, symbol)
            for name, (mutation, _) in MUTATIONS.items():
                rewrites = list(mutation(source, scope))
                if rewrites:
                    candidates[f"c{len(candidates)}"] = (symbol, name, rng.choice(rewrites))
        if not candidates:
            continue
        evaluations = harvest.evaluate(unit, {"base": source, **{n: r.source for n, (_, _, r) in candidates.items()}})
        exact = evaluations["base"].exact_functions()
        for name, (symbol, mutation, rewrite) in candidates.items():
            evaluation = evaluations[name]
            if evaluation.error or symbol not in exact or exact - evaluation.exact_functions() != {symbol}:
                continue  # no effect on the bytes, or an effect beyond the function
            found.append(Case(unit, symbol, mutation, rewrite.description, rewrite.source, evaluation.fuzzy.get(symbol, 0.0)))
    return found


@dataclass
class Outcome:
    case: Case
    match: bool  # the cause-directed search returned to exact
    match_tried: int
    permute: bool  # the randomized search returned to exact
    permute_tried: int


def solve(harvest: Harvest, case: Case, budget: int = 128, seed: int = 0) -> Outcome:
    directed = search(harvest, case.unit, case.symbol, source=case.source)
    randomized = permute(harvest, case.unit, case.symbol, budget=budget, seed=seed, source=case.source)
    return Outcome(case, directed.best.exact, directed.tried, randomized.best.exact, randomized.tried)


def table(outcomes: list[Outcome]) -> list[tuple[str, bool, int, int, int, float]]:
    """(mutation, concord has the inverse, cases, recovered by match, recovered by
    permute, mean score after the mutation), in MUTATIONS' order."""
    rows = []
    for name, (_, known) in MUTATIONS.items():
        group = [o for o in outcomes if o.case.mutation == name]
        if group:
            mean = sum(o.case.score for o in group) / len(group)
            rows.append((name, known, len(group), sum(o.match for o in group), sum(o.permute for o in group), mean))
    return rows
