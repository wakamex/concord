"""Measure how often concord returns a perturbed function to an exact match.

A target with many exact functions is an answer key. `cases` takes functions
that match, applies one behavior-preserving change to each (a mutation), and
keeps the changes that stop the function matching while every other function of
the unit still does. `solve` then runs concord's searches from the mutated source
and records whether each gets back to exact, so recovery can be counted by kind
of change and by search.

Each mutation is a rewrite concord can undo with a rewrite it has (swap a
comparison's operands or an if's branches, swap adjacent statements, move a
declaration, split a declaration from its initializer, name or inline a
temporary, change a loop between for and while, spell out or compress a
compound assignment, nest or merge the halves of an `&&`, swap the operands
of a commutative operator), so they measure whether a search finds that
rewrite among the others. Each kind is capped
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

from concord.model import Cause
from concord.permute import drop_twins, permute
from concord.search import Target, _diff, attributed_lines, search
from concord.transforms import (
    Rewrite,
    _descendants,
    _rewrite,
    address_arithmetic,
    compress_assignment,
    expand_assignment,
    for_loop,
    inline_temporaries,
    merge_conditions,
    move_declarations,
    name_temporaries,
    nest_condition,
    split_declaration,
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
    "swap commutative operands": (commute, True),
    "split a declaration": (split_declaration, True),
}


@dataclass
class Case:
    unit: str
    symbol: str
    mutation: str
    description: str
    source: bytes  # the unit's source with the one change
    score: float  # the function's objdiff score after the change


def _own(harvest: Target, mutation):
    """The target's form of a mutation, or None when its compiler accepts none:
    the mutation itself where the target lists it, or the rewrite the target says
    stands for it."""
    if mutation is commute:
        return mutation
    return next((r for r in harvest.rewrites if r is mutation or getattr(r, "stands_for", None) is mutation), None)


def functions(harvest: Target) -> list[tuple[str, str]]:
    """(unit source, symbol) of every exact function defined in its unit's own source."""
    found = set()
    sources: dict[str, bytes] = {}
    for verdict in harvest.verdicts():
        if not verdict.row["exact"] or verdict.symbol.startswith(("_ZTh", "_ZTv", "_GLOBAL__")):
            continue
        unit = harvest.source(verdict.unit)
        try:
            if unit not in sources:
                sources[unit] = harvest.source_path(unit).read_bytes()
            harvest.find_function(sources[unit], verdict.symbol)
        except (KeyError, FileNotFoundError):
            continue
        found.add((unit, verdict.symbol))
    return sorted(drop_twins(found))


# What a mutation did to the bytes, in the order `concord bench` prints them.
EFFECTS = ("does not compile", "no effect", "beyond the function", "case")


def cases(
    harvest: Target, sample: list[tuple[str, str]], seed: int = 0, per_kind: int | None = None, tally: dict | None = None
) -> list[Case]:
    """One case per function and mutation that applies and stops only that
    function matching, at most `per_kind` of each mutation, chosen at random. The
    rewrite used is chosen at random among those the mutation offers. `tally`
    receives, per mutation, how many applied rewrites had each of EFFECTS, before
    the cap: a rewrite the compiler turns into the same bytes is no case, and
    says the rewrite cannot move this compiler's output either."""
    rng = random.Random(seed)
    found = []
    by_unit: dict[str, list[str]] = {}
    for unit, symbol in sample:
        by_unit.setdefault(unit, []).append(symbol)
    for unit, symbols in sorted(by_unit.items()):
        source = harvest.source_path(unit).read_bytes()
        candidates = {}
        for symbol in symbols:
            scope = harvest.find_function(source, symbol)
            for name, (mutation, _) in MUTATIONS.items():
                mutation = _own(harvest, mutation)
                rewrites = list(mutation(source, scope)) if mutation else []
                if rewrites:
                    candidates[f"c{len(candidates)}"] = (symbol, name, rng.choice(rewrites))
        if not candidates:
            continue
        evaluations = harvest.evaluate(unit, {"base": source, **{n: r.source for n, (_, _, r) in candidates.items()}})
        exact = evaluations["base"].exact_functions()
        for name, (symbol, mutation, rewrite) in candidates.items():
            evaluation = evaluations[name]
            if symbol not in exact:
                continue  # banked as exact, inexact in this build
            lost = exact - evaluation.exact_functions()
            effect = "does not compile" if evaluation.error else "no effect" if not lost else "case" if lost == {symbol} else "beyond the function"
            if tally is not None:
                tally.setdefault(mutation, dict.fromkeys(EFFECTS, 0))[effect] += 1
            if effect == "case":
                found.append(Case(unit, symbol, mutation, rewrite.description, rewrite.source, evaluation.fuzzy.get(symbol, 0.0)))
    if per_kind is None:
        return found
    kept = []
    for name in MUTATIONS:
        group = [case for case in found if case.mutation == name]
        kept += rng.sample(group, min(per_kind, len(group)))
    return kept


HISTORY = "history"


def _git(root, *args: str) -> str:
    import subprocess

    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def _show(root, commit: str, path: str) -> bytes | None:
    import subprocess

    done = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True, check=False)
    return done.stdout if done.returncode == 0 else None


def history(harvest: Target, commits: int = 300, count: int | None = None, seed: int = 0) -> list[Case]:
    """Cases from the target's own history: for each exact function whose body one
    of the last `commits` commits changed, and that no later commit changed, the
    unit's present source with that function's body from before the commit. The
    changed lines are the fix a person made, so --causes measures localization on
    real differences. Kept, as for mutations, when only that function stops
    matching. A fix made in a header, or spread over several functions, is
    replayed only in part, and a body that no longer compiles against the
    present headers is dropped. At most `count` cases, chosen at random."""
    root = harvest.root
    exact: dict[str, set[str]] = {}
    for unit, symbol in functions(harvest):
        exact.setdefault(unit, set()).add(symbol)
    sources = {unit: harvest.source_path(unit) for unit in exact}
    paths = {str(path.relative_to(root)): unit for unit, path in sources.items()}
    found: dict[str, dict[str, tuple[str, bytes]]] = {}  # unit -> symbol -> (commit, mutated source)
    current = {unit: path.read_bytes() for unit, path in sources.items()}
    log = _git(root, "log", f"-{commits}", "--format=%H", "--", *sorted(paths)).split()
    for commit in log:  # newest first, so a function's latest change is the one kept
        for path in _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).split():
            unit = paths.get(path)
            if unit is None:
                continue
            before, after = _show(root, f"{commit}^", path), _show(root, commit, path)
            if before is None or after is None:
                continue
            for symbol in exact[unit]:
                if symbol in found.get(unit, {}):
                    continue
                try:
                    now = harvest.find_function(current[unit], symbol)
                    old, new = harvest.find_function(before, symbol), harvest.find_function(after, symbol)
                except (KeyError, ValueError):
                    continue
                if new.text != now.text or old.text == new.text:
                    continue  # changed since, or not by this commit
                mutated = current[unit][: now.start_byte] + old.text + current[unit][now.end_byte :]
                found.setdefault(unit, {})[symbol] = (commit[:9], mutated)
    cases_found = []
    for unit, items in sorted(found.items()):
        evaluations = harvest.evaluate(unit, {"base": current[unit], **{f"c{n}": text for n, (_, (_, text)) in enumerate(sorted(items.items()))}})
        base = evaluations["base"].exact_functions()
        for n, (symbol, (commit, text)) in enumerate(sorted(items.items())):
            evaluation = evaluations[f"c{n}"]
            if evaluation.error or symbol not in base or base - evaluation.exact_functions() != {symbol}:
                continue
            cases_found.append(Case(unit, symbol, HISTORY, f"before {commit}", text, evaluation.fuzzy.get(symbol, 0.0)))
    if count is not None and len(cases_found) > count:
        cases_found = random.Random(seed).sample(cases_found, count)
    return cases_found


TWO_EDITS = "two edits"


def pairs(harvest: Target, sample: list[tuple[str, str]], seed: int = 0, count: int | None = None) -> list[Case]:
    """Cases two changes away from a match: for each function, two mutations of
    kinds concord can undo, the second applied to the result of the first, kept
    when together they stop only that function matching. At most `count` cases."""
    rng = random.Random(seed)
    known = [own for mutation, undoable in MUTATIONS.values() if undoable and (own := _own(harvest, mutation))]
    found = []
    by_unit: dict[str, list[str]] = {}
    for unit, symbol in sample:
        by_unit.setdefault(unit, []).append(symbol)
    for unit, symbols in sorted(by_unit.items()):
        source = harvest.source_path(unit).read_bytes()
        candidates = {}
        for symbol in symbols:
            text, steps = source, []
            for mutation in rng.sample(known, 2):
                rewrites = list(mutation(text, harvest.find_function(text, symbol)))
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


def solve(harvest: Target, case: Case, budget: int = 128, seed: int = 0, directed: bool = True, objective: str = "score") -> Outcome:
    """Run both searches on the case; `directed` and `objective` are permute's own settings."""
    by_cause = search(harvest, case.unit, case.symbol, source=case.source)
    found = permute(harvest, case.unit, case.symbol, budget=budget, seed=seed, source=case.source, directed=directed, objective=objective)
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


def locate(harvest: Target, case: Case) -> Located:
    """Compile the case with debug information and map each finding of its diff to
    the source line its code comes from."""
    result = Located(case)
    scope = harvest.find_function(case.source, case.symbol)
    first, last = scope.start_point[0] + 1, scope.end_point[0] + 1
    result.length = last - first + 1
    original = harvest.source_path(case.unit).read_bytes()
    result.changed = {line for line in changed_lines(original, case.source) if first <= line <= last}
    evaluation = harvest.evaluate(case.unit, {"case": case.source})["case"]
    diff = _diff(harvest, evaluation, case.symbol)
    result.causes = [finding.cause.value for finding in diff.findings]
    lines = attributed_lines(harvest, case.unit, case.symbol, case.source, diff)
    result.attributed = {line for line in lines if first <= line <= last}
    return result


@dataclass
class CauseCase:
    """The primary causes concord names for a case, against the lines that were changed."""

    case: Case
    kinds: list[str] = field(default_factory=list)  # each primary cause's kind, in the order concord gives them
    lines: list[set[int]] = field(default_factory=list)  # each primary cause's source lines, empty when it has none
    changed: set[int] = field(default_factory=set)

    def near(self, index: int, within: int = 2) -> bool:
        return any(abs(line - c) <= within for line in self.lines[index] for c in self.changed)

    @property
    def hit(self) -> bool:
        """Some primary cause is within two lines of the change."""
        return any(self.near(i) for i in range(len(self.kinds)))

    @property
    def first_hit(self) -> bool:
        """A cause of the site concord lists first, the one to fix first, is within two lines of the change."""
        from concord.causes import sites

        found = sites([{"line": min(lines) if lines else None} for lines in self.lines])
        return bool(found) and any(self.near(i) for i in found[0])

    @property
    def sites(self) -> int:
        """The source sites the primary causes fall into, as concord causes groups them."""
        from concord.causes import sites

        return len(sites([{"line": min(lines) if lines else None} for lines in self.lines]))

    @property
    def few(self) -> bool:
        """No more sites than the case has edits."""
        return self.sites <= (2 if self.case.mutation == TWO_EDITS else 1)

    @property
    def precision(self) -> float | None:
        """The share of primary causes with a line that are within two lines of the change."""
        placed = [i for i in range(len(self.kinds)) if self.lines[i]]
        return sum(self.near(i) for i in placed) / len(placed) if placed else None


def locate_causes(harvest: Target, case: Case) -> CauseCase:
    """Compile the case, separate its diff's primary causes from their fallout, and
    give each primary cause the source lines of its code in the case's own compile."""
    from concord.causes import analyse
    from concord.diff import read_functions

    scope = harvest.find_function(case.source, case.symbol)
    first, last = scope.start_point[0] + 1, scope.end_point[0] + 1
    original = harvest.source_path(case.unit).read_bytes()
    result = CauseCase(case, changed={line for line in changed_lines(original, case.source) if first <= line <= last})
    evaluation = harvest.evaluate(case.unit, {"case": case.source})["case"]
    diff = _diff(harvest, evaluation, case.symbol)
    verdict = evaluation.verdict(case.symbol)
    retail = read_functions(harvest.object_pair(verdict)[0], {case.symbol}).get(case.symbol)
    ours = read_functions(evaluation.object, {case.symbol}).get(case.symbol)
    if retail is None or ours is None:
        raise KeyError(f"{case.symbol} is not a function in both objects")
    analysis = analyse(retail, ours, diff.findings)
    offsets = sorted({p.candidate_offset for p in analysis.primaries if p.candidate_offset is not None})
    lines = harvest.line_map(case.unit, case.source, {case.symbol: offsets}).get(case.symbol, {}) if offsets else {}
    result.kinds = [p.kind for p in analysis.primaries]
    result.lines = [{n for n in lines.get(p.candidate_offset, set()) if first <= n <= last} for p in analysis.primaries]
    return result


def cause_table(found: list[CauseCase]) -> list[tuple[str, int, float, float, int, int, int, float | None]]:
    """(mutation, cases, mean primary causes, mean source sites, cases with no more
    sites than edits, cases where the first primary cause is within two lines of the
    change, cases where any is, mean precision of the placed primary causes), in
    MUTATIONS' order with two-edit cases last."""
    rows = []
    for name in [*MUTATIONS, TWO_EDITS, HISTORY]:
        group = [item for item in found if item.case.mutation == name]
        if group:
            precisions = [item.precision for item in group if item.precision is not None]
            rows.append((
                name,
                len(group),
                sum(len(item.kinds) for item in group) / len(group),
                sum(item.sites for item in group) / len(group),
                sum(item.few for item in group),
                sum(item.first_hit for item in group),
                sum(item.hit for item in group),
                sum(precisions) / len(precisions) if precisions else None,
            ))  # fmt: skip
    return rows


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
