"""Find enum dispatches whose target keeps a comparison the build omits."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from tree_sitter import Node, Parser

from concord import authored
from concord.diff import ObjectFunction, disassemble, read_function, shape
from concord.harvest import FunctionVerdict, Harvest
from concord.transforms import (
    CPP,
    _arity,
    _declarator_name,
    _declarator_parts,
    _demangled_params,
    _enclosing_path,
    _name_and_arity,
    _param_types,
    demangle,
)


@dataclass(frozen=True)
class SwitchEdit:
    line: int
    kind: str
    expression: str
    source: bytes


@lru_cache(maxsize=4)
def enum_members(root: Path) -> dict[str, frozenset[str]]:
    """Map each enum member in Harvest source to the enum that declares it."""
    source_root = root.resolve() / "src"
    members: dict[str, set[str]] = defaultdict(set)
    parser = Parser(CPP)
    for path in source_root.rglob("*"):
        if path.suffix not in {".h", ".hpp", ".cpp"}:
            continue
        try:
            source = path.read_bytes()
        except OSError:
            continue
        tree = parser.parse(source)
        for node in _walk(tree.root_node):
            if node.type != "enum_specifier":
                continue
            body = node.child_by_field_name("body")
            if body is None:
                continue
            name = node.child_by_field_name("name")
            scope = _enclosing_path(node)
            key = "::".join([*scope, name.text.decode()]) if name else f"{path.relative_to(source_root)}@{node.start_byte}"
            for child in _walk(body):
                if child.type != "enumerator":
                    continue
                member = child.child_by_field_name("name")
                if member is not None:
                    members[_text(source, member).decode()].add(key)
    return {name: frozenset(types) for name, types in members.items()}


def _walk(node: Node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(source: bytes, node: Node) -> bytes:
    return source[node.start_byte : node.end_byte]


def _enum_label(
    source: bytes, node: Node, members: dict[str, frozenset[str]]
) -> tuple[str, frozenset[str]] | None:
    match = re.search(rb"([A-Za-z_]\w*)\s*$", _text(source, node))
    if match is None:
        return None
    name = match.group(1).decode()
    return (name, members[name]) if name in members else None


def _enum_type(labels: list[tuple[str, frozenset[str]]]) -> str | None:
    counts = Counter(enum for name, enums in labels for enum in enums)
    matches = [enum for enum, count in counts.items() if count >= 2]
    return matches[0] if len(matches) == 1 else None


def _is_int_cast(source: bytes, node: Node) -> bool:
    if node.type == "cast_expression":
        cast_type = node.child_by_field_name("type")
        if cast_type is not None and re.search(rb"\bint\b", _text(source, cast_type)):
            return True
    return bool(re.match(rb"\s*static_cast\s*<\s*int\s*>", _text(source, node)))


def _int_cast(source: bytes, expression: Node) -> bytes:
    text = _text(source, expression)
    if expression.type in {"identifier", "field_expression", "call_expression", "subscript_expression"}:
        return b"(int)" + text
    return b"(int)(" + text + b")"


def _condition_value(node: Node) -> Node | None:
    condition = node.child_by_field_name("condition")
    if condition is not None and condition.type == "condition_clause":
        return condition.child_by_field_name("value")
    return condition


def _switch_edit(source: bytes, switch: Node, members: dict[str, frozenset[str]]) -> SwitchEdit | None:
    expression = _condition_value(switch)
    if expression is None or _is_int_cast(source, expression):
        return None
    body = switch.child_by_field_name("body")
    if body is None:
        return None
    labels = []
    stack = [body]
    while stack:
        node = stack.pop()
        if node is not body and node.type == "switch_statement":
            continue
        if node.type == "case_statement":
            value = node.child_by_field_name("value")
            label = _enum_label(source, value, members) if value is not None else None
            if label is not None:
                labels.append(label)
            continue
        stack.extend(reversed(node.children))
    if _enum_type(labels) is None:
        return None
    expression_text = _text(source, expression)
    replacement = _int_cast(source, expression)
    candidate = source[: expression.start_byte] + replacement + source[expression.end_byte :]
    return SwitchEdit(switch.start_point[0] + 1, "switch", expression_text.decode(), candidate)


def _if_chain(root: Node) -> list[Node]:
    if root.parent is not None and root.parent.type == "else_clause":
        return []
    chain = []
    current = root
    while current.type == "if_statement":
        chain.append(current)
        alternative = current.child_by_field_name("alternative")
        following = next((node for node in alternative.named_children if node.type == "if_statement"), None) if alternative else None
        if following is None:
            break
        current = following
    return chain


def _comparison_edits(source: bytes, root: Node, members: dict[str, frozenset[str]]) -> SwitchEdit | None:
    chain = _if_chain(root)
    if not chain:
        return None
    comparisons: dict[tuple[str, bytes], list[tuple[Node, str]]] = defaultdict(list)
    for branch in chain:
        condition = _condition_value(branch)
        if condition is None:
            continue
        for node in _walk(condition):
            if node.type != "binary_expression":
                continue
            operator = node.child_by_field_name("operator")
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if operator is None or _text(source, operator) not in (b"==", b"!=") or left is None or right is None:
                continue
            left_label = _enum_label(source, left, members)
            right_label = _enum_label(source, right, members)
            if left_label is not None and right_label is None:
                enum_types, label, expression = left_label[1], left_label[0], right
            elif right_label is not None and left_label is None:
                enum_types, label, expression = right_label[1], right_label[0], left
            else:
                continue
            if _is_int_cast(source, expression):
                continue
            for enum_type in enum_types:
                comparisons[(enum_type, _text(source, expression))].append((expression, label))
    choices = [
        (key, rows)
        for key, rows in comparisons.items()
        if len({label for _, label in rows}) >= 2
    ]
    if len(choices) != 1:
        return None
    (_, expression_text), rows = choices[0]
    edits = { (expression.start_byte, expression.end_byte): expression for expression, _ in rows }
    candidate = source
    for expression in sorted(edits.values(), key=lambda item: item.start_byte, reverse=True):
        candidate = (
            candidate[: expression.start_byte]
            + _int_cast(source, expression)
            + candidate[expression.end_byte :]
        )
    return SwitchEdit(chain[0].start_point[0] + 1, "comparison-chain", expression_text.decode(), candidate)


def _scope_candidates(source: bytes, scope: Node, members: dict[str, frozenset[str]]) -> list[SwitchEdit]:
    candidates = []
    for node in _walk(scope):
        if node.type == "switch_statement":
            edit = _switch_edit(source, node, members)
            if edit is not None:
                candidates.append(edit)
        elif node.type == "if_statement":
            edit = _comparison_edits(source, node, members)
            if edit is not None:
                candidates.append(edit)
    seen = set()
    return [
        item
        for item in sorted(candidates, key=lambda candidate: (candidate.line, candidate.kind))
        if not (item.source in seen or seen.add(item.source))
    ]


def source_candidates(source: bytes, symbol: str, members: dict[str, frozenset[str]]) -> list[SwitchEdit]:
    """Find enum switch and comparison-chain casts in one function body."""
    tree = Parser(CPP).parse(source)
    scope = _definition_for_symbol(_definitions(tree), symbol)
    return _scope_candidates(source, scope, members) if scope is not None else []


def _guard_signatures(function: ObjectFunction) -> Counter:
    instructions = disassemble(function)
    guards = Counter()
    for previous, following in pairwise(instructions):
        conditional_jump = following.mnemonic.startswith("j") and following.mnemonic != "jmp"
        if previous.mnemonic in {"cmp", "test"} and conditional_jump:
            guards[(shape(previous), shape(following))] += 1
    return guards


def missing_range_checks(target: ObjectFunction, candidate: ObjectFunction) -> int:
    """Count target-only immediate comparisons followed by a conditional branch."""
    return len(missing_range_check_offsets(target, candidate)) // 2


def missing_range_check_offsets(target: ObjectFunction, candidate: ObjectFunction) -> list[int]:
    """Target offsets for immediate compare and branch rows missing from the build."""
    target_instructions = disassemble(target)
    candidate_guards = _guard_signatures(candidate)
    target_guards: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for comparison, branch in pairwise(target_instructions):
        conditional_jump = branch.mnemonic.startswith("j") and branch.mnemonic != "jmp"
        if not (
            comparison.mnemonic == "cmp"
            and conditional_jump
            and any(operand[0] == "imm" for operand in shape(comparison)[1])
        ):
            continue
        signature = (shape(comparison), shape(branch))
        target_guards[signature].append((comparison.offset, branch.offset))

    missing = []
    for signature, offsets in target_guards.items():
        count = candidate_guards[signature]
        available = offsets[count:]
        missing.extend(offset for pair in available for offset in pair)
    return missing


def _definitions(tree) -> dict[tuple[str, int], list[Node]]:
    definitions: dict[tuple[str, int], list[Node]] = defaultdict(list)
    for node in _walk(tree.root_node):
        if node.type != "function_definition":
            continue
        names = {_declarator_name(node)}
        full_name = _enclosing_path(node) + _declarator_parts(node)
        if full_name:
            names.add("::".join(full_name[-2:]))
        for name in names - {""}:
            definitions[(name, _arity(node))].append(node)
    return definitions


def _definition_for_symbol(definitions: dict[tuple[str, int], list[Node]], symbol: str) -> Node | None:
    demangled = demangle(symbol)
    name, arity = _name_and_arity(demangled)
    found = definitions.get((name, arity), [])
    if len(found) > 1:
        wanted = _demangled_params(demangled)
        matched = [node for node in found if _param_types(node) == wanted]
        if len(matched) == 1:
            return matched[0]
    if found:
        return found[0]
    return None


def _unit_verdicts(harvest: Harvest, unit: str | None, symbol: str | None) -> dict[str, list[FunctionVerdict]]:
    grouped: dict[str, list[FunctionVerdict]] = defaultdict(list)
    for verdict in harvest.verdicts(unit):
        if verdict.row["exact"] or (symbol is not None and verdict.symbol != symbol):
            continue
        grouped[verdict.unit].append(verdict)
    return grouped


def run(harvest: Harvest, *, unit: str | None = None, symbol: str | None = None, write: bool = False) -> list[dict]:
    """Measure enum casts through the author gate; optionally write accepted forms."""
    members = enum_members(harvest.root)
    records = []
    for slug, verdicts in _unit_verdicts(harvest, unit, symbol).items():
        source_unit = harvest.source(slug)
        source_path = harvest.root / "src" / source_unit
        current_object = harvest.objects / "base" / f"{slug}.o"
        target_object = harvest.objects / "target" / f"{slug}.o"
        parsed_source = None
        tree = None
        definitions = {}
        for verdict in verdicts:
            tried: set[tuple[int, str, str]] = set()
            attempts = 0
            while attempts < 2:
                source = source_path.read_bytes()
                if source != parsed_source:
                    tree = Parser(CPP).parse(source)
                    definitions = _definitions(tree)
                    parsed_source = source
                scope = _definition_for_symbol(definitions, verdict.symbol)
                if scope is None:
                    break
                candidates = _scope_candidates(source, scope, members)
                candidate_edit = next(
                    (
                        edit
                        for edit in candidates
                        if (edit.line, edit.kind, edit.expression) not in tried
                    ),
                    None,
                )
                if candidate_edit is None:
                    break
                target_function = read_function(target_object, verdict.symbol)
                current_function = read_function(current_object, verdict.symbol)
                missing_checks = missing_range_checks(target_function, current_function)
                if not missing_checks:
                    break
                tried.add((candidate_edit.line, candidate_edit.kind, candidate_edit.expression))
                attempts += 1
                result = authored.apply(
                    harvest,
                    source_unit,
                    verdict.symbol,
                    write=write,
                    candidate=candidate_edit.source,
                )
                applied = result["applied"]
                row = result["candidates"].get("causal-candidate", {})
                record = {
                    "unit": source_unit,
                    "symbol": verdict.symbol,
                    "kind": candidate_edit.kind,
                    "line": candidate_edit.line,
                    "expression": candidate_edit.expression,
                    "missing_checks": missing_checks,
                    "before_size": row.get("before_size", verdict.row["size"]),
                    "size": row.get("size"),
                    "before_score": row.get("before"),
                    "score": row.get("score"),
                    "exact": row.get("exact", False),
                    "kept": row.get("kept", False),
                    "applied": applied,
                    "oracle": row.get("oracle"),
                    "error": row.get("error"),
                }
                records.append(record)
                if applied and row.get("object"):
                    current_object = Path(row["object"])
                if row.get("kept") and not write:
                    break
    return records
