"""Source rewrites intended to preserve semantics, each mapped to a codegen cause.

Every rewrite works on the C++ syntax tree (tree-sitter), so it touches exactly
one expression and leaves the rest of the file byte for byte. A rewrite that
does not compile, such as a swapped comparison on a type without the mirrored
operator, is dropped by the compile step.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

from concord.model import Cause

CPP = Language(tree_sitter_cpp.language())
MIRROR = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "==": "==", "!=": "!="}
# GCC moves a constant operand to the right of a comparison itself, so swapping
# a comparison with a constant never changes the bytes.
CONSTANTS = {"number_literal", "char_literal", "string_literal", "true", "false", "null", "nullptr"}


@dataclass(frozen=True)
class Rewrite:
    transform: str  # idiom id in the knowledge base
    cause: Cause
    description: str
    source: bytes


def demangle(symbol: str) -> str:
    return subprocess.run(["c++filt", symbol], capture_output=True, text=True, check=True).stdout.strip()


def _name_and_arity(demangled: str) -> tuple[str, int]:
    """`ns::Class::method(float, int) const` -> ("Class::method", 2)."""
    head, _, params = demangled.partition("(")
    params = params.rsplit(")", 1)[0].strip()
    depth, arity = 0, 0 if params in ("", "void") else 1
    for ch in params:
        depth += ch in "<("
        depth -= ch in ">)"
        arity += ch == "," and depth == 0
    parts = re.sub(r"<[^<>]*>", "", head).split("::")
    return "::".join(parts[-2:]), arity


def _declarator_name(node: Node) -> str:
    """Qualified name of a function_definition's declarator, without templates."""
    d = node.child_by_field_name("declarator")
    while d is not None and d.type != "function_declarator":
        d = d.child_by_field_name("declarator")
    if d is None:
        return ""
    name = d.child_by_field_name("declarator").text.decode()
    return "::".join(re.sub(r"<[^<>]*>", "", name).replace(" ", "").split("::")[-2:])


def _arity(node: Node) -> int:
    d = node.child_by_field_name("declarator")
    while d is not None and d.type != "function_declarator":
        d = d.child_by_field_name("declarator")
    params = d.child_by_field_name("parameters") if d is not None else None
    if params is None:
        return 0
    named = [c for c in params.named_children if c.type != "comment"]
    if len(named) == 1 and named[0].text.strip() == b"void":
        return 0
    return len(named)


def find_function(source: bytes, symbol: str) -> Node:
    """The definition of the symbol's function in source, matched by class,
    name and parameter count."""
    name, arity = _name_and_arity(demangle(symbol))
    tree = Parser(CPP).parse(source)
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition" and _declarator_name(node) == name:
            if _arity(node) == arity:
                found.append(node)
        stack.extend(node.children)
    if not found:
        raise KeyError(f"no definition of {name} in the source")
    return found[0]


def comparisons(function: Node) -> list[Node]:
    out = []
    stack = [function]
    while stack:
        node = stack.pop()
        if node.type == "binary_expression":
            op = node.child_by_field_name("operator")
            if op is not None and op.type in MIRROR:
                out.append(node)
        stack.extend(node.children)
    return sorted(out, key=lambda n: n.start_byte)


def _rewrite(transform: str, cause: Cause, source: bytes, node: Node, replacement: bytes, what: str) -> Rewrite:
    line = source.count(b"\n", 0, node.start_byte) + 1
    return Rewrite(transform, cause, f"line {line}: {what}", source[: node.start_byte] + replacement + source[node.end_byte :])


def swap_operands(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per comparison of two non-constant operands in scope (a function
    or a whole file): `a < b` becomes `b > a`."""
    for node in comparisons(scope):
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left.type in CONSTANTS or right.type in CONSTANTS:
            continue
        op = node.child_by_field_name("operator").type
        swapped = right.text + b" " + MIRROR[op].encode() + b" " + left.text
        yield _rewrite(
            "compare-operand-order", Cause.OPERAND_ORDER, source, node, swapped, f"{node.text.decode()} -> {swapped.decode()}"
        )


def _negate(condition: Node) -> bytes:
    if condition.type == "unary_expression" and condition.child_by_field_name("operator").type == "!":
        return condition.child_by_field_name("argument").text
    return b"!(" + condition.text + b")"


def _block(statement: Node) -> bytes:
    """The statement as a block, so a moved branch cannot capture a following else."""
    return statement.text if statement.type == "compound_statement" else b"{ " + statement.text + b" }"


def swap_branches(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per if/else in scope: `if (c) A else B` becomes `if (!c) B else A`.
    The condition is still evaluated once and the same branch runs; GCC 4.4 tends
    to lay out the then-branch as the fall-through."""
    for node in _descendants(scope, "if_statement"):
        if node.child_by_field_name("alternative") is None:
            continue
        clause = node.child_by_field_name("condition")
        condition = clause.child_by_field_name("value")
        if condition is None or clause.named_child_count != 1:
            continue  # an init-statement or declaration condition has no simple negation
        then = node.child_by_field_name("consequence")
        otherwise = node.child_by_field_name("alternative").named_children[-1]
        swapped = b"if (" + _negate(condition) + b") " + _block(otherwise) + b" else " + _block(then)
        yield _rewrite("branch-sense", Cause.BLOCK_ORDER, source, node, swapped, f"if ({condition.text.decode()}) swapped")


def _descendants(scope: Node, kind: str) -> list[Node]:
    found, stack = [], [scope]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type == kind:
            found.append(node)
    return sorted(found, key=lambda n: n.start_byte)


SIMPLE = {"declaration", "expression_statement"}
WRITES = {"assignment_expression", "update_expression"}


def _names(node: Node) -> tuple[set[bytes], set[bytes]]:
    """Identifiers a statement writes (declares, assigns or increments) and every
    identifier it mentions. Calls are not followed: two calls may still depend on
    each other through state, which the byte comparison then rejects."""
    writes, mentions = set(), set()
    stack = [node]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type in ("identifier", "field_identifier"):
            mentions.add(n.text)
        target = None
        if n.type == "init_declarator":
            target = n.child_by_field_name("declarator")
        elif n.type == "declaration" and n.child_by_field_name("declarator").type != "init_declarator":
            target = n.child_by_field_name("declarator")
        elif n.type == "assignment_expression":
            target = n.child_by_field_name("left")
        elif n.type == "update_expression":
            target = n.child_by_field_name("argument")
        if target is not None:
            writes |= {i.text for i in _descendants(target, "identifier")} | ({target.text} if target.type == "identifier" else set())
    return writes, mentions


def swap_statements(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per pair of adjacent declarations or expression statements that
    share no written variable: `a; b;` becomes `b; a;`. The order statements are
    written in sets the order GCC 4.4 creates their values in, which decides
    register choices and, through SSA numbering, the operand order of compares."""
    for block in _descendants(scope, "compound_statement"):
        statements = [c for c in block.named_children if c.type != "comment"]
        for first, second in zip(statements, statements[1:]):
            if first.type not in SIMPLE or second.type not in SIMPLE:
                continue
            w1, m1 = _names(first)
            w2, m2 = _names(second)
            if w1 & m2 or w2 & m1:
                continue
            between = source[first.end_byte : second.start_byte]
            replacement = second.text + between + first.text
            line = source.count(b"\n", 0, first.start_byte) + 1
            yield Rewrite(
                "statement-order",
                Cause.REGISTER_ALLOCATION,
                f"line {line}: swapped with the next statement",
                source[: first.start_byte] + replacement + source[second.end_byte :],
            )


def _line_start(source: bytes, offset: int) -> int:
    return source.rfind(b"\n", 0, offset) + 1


def move_declarations(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per position a declaration without an initializer can move to
    within its block, from the block's start up to its first use. GCC 4.4 assigns
    registers and stack slots in declaration order."""
    for block in _descendants(scope, "compound_statement"):
        statements = [c for c in block.named_children if c.type != "comment"]
        for i, declaration in enumerate(statements):
            if declaration.type != "declaration" or _descendants(declaration, "init_declarator"):
                continue
            declared, _ = _names(declaration)
            if not declared or b"(" in declaration.text:
                continue  # a function declaration, or a constructor call
            uses = (j for j in range(i + 1, len(statements)) if declared & _names(statements[j])[1])
            first_use = next(uses, len(statements) - 1)
            start = _line_start(source, declaration.start_byte)
            end = source.find(b"\n", declaration.end_byte) + 1 or len(source)
            if source[start : declaration.start_byte].strip() or source[declaration.end_byte : end].strip():
                continue  # shares its line with other code
            for j in [*range(0, i), *range(i + 2, first_use + 1)]:
                at = _line_start(source, statements[j].start_byte)
                text = source[at : statements[j].start_byte] + declaration.text + b"\n"
                if at < start:
                    moved = source[:at] + text + source[at:start] + source[end:]
                else:
                    moved = source[:start] + source[end:at] + text + source[at:]
                line = source.count(b"\n", 0, declaration.start_byte) + 1
                yield Rewrite(
                    "declaration-order",
                    Cause.REGISTER_ALLOCATION,
                    f"line {line}: {declaration.text.decode()} moved to line {source.count(b'\n', 0, at) + 1}",
                    moved,
                )


# Expressions worth naming: a value computed from others, not a plain name or literal.
HOISTABLE = {"binary_expression", "field_expression", "subscript_expression", "pointer_expression", "call_expression"}


def name_temporaries(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per subexpression of a declaration or expression statement:
    `f(a.b + c);` becomes `__typeof__(a.b + c) tmp = a.b + c; f(tmp);`. A named
    local changes the order GCC 4.4 creates values in and what stays in a register.
    `__typeof__` (a GCC extension) keeps the rewrite type-correct without type
    inference; it drops references, so a temporary of class type is a copy, which
    the byte comparison then rejects."""
    for statement in _descendants(scope, "expression_statement") + _descendants(scope, "declaration"):
        if statement.parent is None or statement.parent.type != "compound_statement":
            continue
        indent = source[_line_start(source, statement.start_byte) : statement.start_byte]
        if indent.strip():
            continue
        for node in _subexpressions(statement):
            if node.type not in HOISTABLE or node.parent == statement or b"\n" in node.text or _written(node):
                continue
            if node.type == "binary_expression" and node.child_by_field_name("operator").type in ("&&", "||"):
                continue  # naming the right operand would evaluate it unconditionally
            if any(a.type in ("conditional_expression", "lambda_expression") or (a.type == "binary_expression" and a.child_by_field_name("operator").type in ("&&", "||")) for a in _ancestors(node, statement)):
                continue
            name = b"concordTmp%d" % source.count(b"concordTmp")
            declaration = b"__typeof__(" + node.text + b") " + name + b" = " + node.text + b";\n" + indent
            replaced = source[: node.start_byte] + name + source[node.end_byte :]
            at = statement.start_byte
            line = source.count(b"\n", 0, node.start_byte) + 1
            yield Rewrite(
                "named-temporary",
                Cause.REGISTER_ALLOCATION,
                f"line {line}: {node.text.decode()} named",
                replaced[:at] + declaration + replaced[at:],
            )


def _written(node: Node) -> bool:
    """Whether the expression is assigned, incremented or has its address taken,
    where a named copy would change what is written."""
    parent = node.parent
    if parent.type == "assignment_expression" and parent.child_by_field_name("left") == node:
        return True
    if parent.type == "update_expression":
        return True
    return parent.type == "pointer_expression" and parent.child_by_field_name("operator").type == "&"


def _subexpressions(statement: Node) -> list[Node]:
    out, stack = [], list(statement.children)
    while stack:
        node = stack.pop()
        if node.type in ("lambda_expression", "compound_statement"):
            continue
        out.append(node)
        stack.extend(node.children)
    return sorted(out, key=lambda n: n.start_byte)


def _ancestors(node: Node, stop: Node) -> list[Node]:
    out = []
    node = node.parent
    while node is not None and node != stop:
        out.append(node)
        node = node.parent
    return out


TRANSFORMS = {Cause.OPERAND_ORDER: [swap_operands], Cause.BLOCK_ORDER: [swap_branches]}
# Every rewrite, for searches that do not pick rewrites by cause.
ALL = [swap_operands, swap_branches, swap_statements, move_declarations, name_temporaries]
