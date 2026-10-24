"""Measure how often concord returns a perturbed function to an exact match.

A target with many exact functions is an answer key. `cases` takes functions
that match, applies one behavior-preserving change to each (a mutation), and
keeps the changes that stop the function matching while every other function of
the unit still does. `solve` then runs concord's searches from the mutated source
and records whether each gets back to exact, so recovery can be counted by kind
of change and by search.

Mutations come in two groups. Most are rewrites concord can undo with a rewrite
it has (swap a comparison's operands or an if's branches, swap adjacent
statements, move a declaration, name or inline a temporary, change a loop
between for and while, spell out or compress a compound assignment, nest or
merge the halves of an `&&`), so they measure whether a search finds that
rewrite among the others. The rest are changes it has no rewrite to undo (swap
the operands of a commutative operator, split a declaration from its
initializer): they measure what the searches cannot reach. Each kind is capped
at the same number of cases, so a common kind does not decide the totals.
Real near misses are harder than either, since nothing says one rewrite
separates them from a match; the benchmark is a floor to compare versions of
concord against, not a forecast of yield.
"""

from __future__ import annotations

import difflib
import random
from collections.abc import Iterator
from dataclasses import dataclass, field

from tree_sitter import Node

from concord.harvest import Harvest
from concord.model import Cause
from concord.permute import drop_twins, permute
from concord.search import _diff, attributed_lines, search
from concord.transforms import (
    Rewrite,
    _descendants,
    _rewrite,
    address_arithmetic,
    compress_assignment,
    expand_assignment,
    find_function,
    for_loop,
    inline_temporaries,
    merge_conditions,
    move_declarations,
    name_temporaries,
    nest_condition,
    swap_branches,
    swap_operands,
    swap_statements,
    while_loop,
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


def split_declaration(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`int x = e;` as `int x; x = e;`, for a non-const local of a built-in type."""
    for node in _descendants(scope, "declaration"):
        kinds = [c.type for c in node.named_children]
        if kinds != ["primitive_type", "init_declarator"] or node.parent.type != "compound_statement":
            continue
        declarator = node.named_children[1]
        name, value = declarator.child_by_field_name("declarator"), declarator.child_by_field_name("value")
        if name is None or value is None or name.type != "identifier" or value.type in ("initializer_list", "argument_list"):
            continue
        indent = source[source.rfind(b"\n", 0, node.start_byte) + 1 : node.start_byte]
        if indent.strip():
            continue
        split = node.named_children[0].text + b" " + name.text + b";\n" + indent + name.text + b" = " + value.text + b";"
        yield _rewrite("bench-split-declaration", Cause.REGISTER_ALLOCATION, source, node, split, f"{name.text.decode()} declared, then assigned")


# name -> (mutation, whether concord has a rewrite that undoes it)
MUTATIONS = {
    "swap comparison operands": (swap_operands, True),
    "swap if branches": (swap_branches, True),
    "swap adjacent statements": (swap_statements, True),
    "move a declaration": (move_declarations, True),
    "name a temporary": (name_temporaries, True),
    "inline a temporary": (inline_temporaries, True),
    "for loop as while loop": (while_loop, True),
    "while loop as for loop": (for_loop, True),
    "spell out an assignment": (expand_assignment, True),
    "compress an assignment": (compress_assignment, True),
    "nest an && condition": (nest_condition, True),
    "merge nested ifs": (merge_conditions, True),
    "recast an address sum": (address_arithmetic, True),
    "swap commutative operands": (commute, False),
    "split a declaration": (split_declaration, False),
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


def cases(harvest: Harvest, sample: list[tuple[str, str]], seed: int = 0, per_kind: int | None = None) -> list[Case]:
    """One case per function and mutation that applies and stops only that
    function matching, at most `per_kind` of each mutation, chosen at random. The
    rewrite used is chosen at random among those the mutation offers."""
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
    if per_kind is None:
        return found
    kept = []
    for name in MUTATIONS:
        group = [case for case in found if case.mutation == name]
        kept += rng.sample(group, min(per_kind, len(group)))
    return kept


TWO_EDITS = "two edits"


def pairs(harvest: Harvest, sample: list[tuple[str, str]], seed: int = 0, count: int | None = None) -> list[Case]:
    """Cases two changes away from a match: for each function, two mutations of
    kinds concord can undo, the second applied to the result of the first, kept
    when together they stop only that function matching. At most `count` cases."""
    rng = random.Random(seed)
    known = [mutation for mutation, undoable in MUTATIONS.values() if undoable]
    found = []
    by_unit: dict[str, list[str]] = {}
    for unit, symbol in sample:
        by_unit.setdefault(unit, []).append(symbol)
    for unit, symbols in sorted(by_unit.items()):
        source = (harvest.root / "src" / unit).read_bytes()
        candidates = {}
        for symbol in symbols:
            text, steps = source, []
            for mutation in rng.sample(known, 2):
                rewrites = list(mutation(text, find_function(text, symbol)))
                if not rewrites:
                    break
                rewrite = rng.choice(rewrites)
                text, steps = rewrite.source, [*steps, rewrite.description]
            if len(steps) == 2:
                candidates[f"c{len(candidates)}"] = (symbol, "; ".join(steps), text)
        if not candidates:
            continue
        evaluations = harvest.evaluate(unit, {"base": source, **{n: text for n, (_, _, text) in candidates.items()}})
        exact = evaluations["base"].exact_functions()
        for name, (symbol, description, text) in candidates.items():
            evaluation = evaluations[name]
            if evaluation.error or symbol not in exact or exact - evaluation.exact_functions() != {symbol}:
                continue
            found.append(Case(unit, symbol, TWO_EDITS, description, text, evaluation.fuzzy.get(symbol, 0.0)))
    return found if count is None else rng.sample(found, min(count, len(found)))


@dataclass
class Outcome:
    case: Case
    match: bool  # the cause-directed search returned to exact
    match_tried: int
    permute: bool  # the randomized search returned to exact
    permute_tried: int


def solve(harvest: Harvest, case: Case, budget: int = 128, seed: int = 0, directed: bool = True) -> Outcome:
    """Run both searches on the case; `directed` is permute's own setting."""
    by_cause = search(harvest, case.unit, case.symbol, source=case.source)
    found = permute(harvest, case.unit, case.symbol, budget=budget, seed=seed, source=case.source, directed=directed)
    return Outcome(case, by_cause.best.exact, by_cause.tried, found.best.exact, found.tried)


def table(outcomes: list[Outcome]) -> list[tuple[str, bool, int, int, int, float, float]]:
    """(mutation, concord has the inverse, cases, recovered by match, recovered by
    permute, mean score after the mutation, mean candidates permute compiled per
    case it recovered), in MUTATIONS' order with two-edit cases last."""
    rows = []
    for name, known in [*((name, known) for name, (_, known) in MUTATIONS.items()), (TWO_EDITS, True)]:
        group = [o for o in outcomes if o.case.mutation == name]
        if group:
            mean = sum(o.case.score for o in group) / len(group)
            tried = [o.permute_tried for o in group if o.permute]
            cost = sum(tried) / len(tried) if tried else 0.0
            rows.append((name, known, len(group), sum(o.match for o in group), sum(o.permute for o in group), mean, cost))
    return rows


@dataclass
class Located:
    """Where concord's diff points for a case, against where the change is."""

    case: Case
    causes: list[str] = field(default_factory=list)  # of every finding, in order
    attributed: set[int] = field(default_factory=set)  # lines of the function the differing code comes from
    changed: set[int] = field(default_factory=set)  # lines the mutation changed, in the mutated source
    length: int = 0  # lines of the function

    @property
    def distance(self) -> int | None:
        """Lines between the change and the nearest attributed line; None when the
        diff points at no line of the function."""
        if not self.attributed or not self.changed:
            return None
        return min(abs(a - c) for a in self.attributed for c in self.changed)

    @property
    def share(self) -> float:
        """The part of the function's lines the diff points at: the chance a line
        picked at random among them is one, to compare a hit rate against."""
        return len(self.attributed) / self.length if self.length else 0.0


def changed_lines(original: bytes, mutated: bytes) -> set[int]:
    """Line numbers of the mutated source that differ from the original; a pure
    deletion counts as the line that now stands where the text was."""
    before, after = original.split(b"\n"), mutated.split(b"\n")
    lines = set()
    for tag, _, _, start, end in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            lines |= set(range(start + 1, max(end, start + 1) + 1))
    return lines


def locate(harvest: Harvest, case: Case) -> Located:
    """Compile the case with debug information and map each finding of its diff to
    the source line its code comes from."""
    result = Located(case)
    scope = find_function(case.source, case.symbol)
    first, last = scope.start_point[0] + 1, scope.end_point[0] + 1
    result.length = last - first + 1
    original = (harvest.root / "src" / case.unit).read_bytes()
    result.changed = {line for line in changed_lines(original, case.source) if first <= line <= last}
    evaluation = harvest.evaluate(case.unit, {"case": case.source})["case"]
    diff = _diff(harvest, evaluation, case.symbol)
    result.causes = [finding.cause.value for finding in diff.findings]
    lines = attributed_lines(harvest, case.unit, case.symbol, case.source, diff)
    result.attributed = {line for line in lines if first <= line <= last}
    return result


def location_table(found: list[Located]) -> list[tuple[str, int, int, int, int, float]]:
    """(mutation, cases, cases whose diff points at a line of the function, cases
    where it points at the changed line, within two lines of it, mean share of the
    function's lines pointed at), in MUTATIONS' order."""
    rows = []
    for name in MUTATIONS:
        group = [item for item in found if item.case.mutation == name]
        if group:
            pointed = [item for item in group if item.distance is not None]
            rows.append((
                name,
                len(group),
                len(pointed),
                sum(item.distance == 0 for item in pointed),
                sum(item.distance <= 2 for item in pointed),
                sum(item.share for item in group) / len(group),
            ))  # fmt: skip
    return rows
