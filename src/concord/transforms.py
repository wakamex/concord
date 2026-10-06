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


def swap_operands(source: bytes, symbol: str) -> Iterator[Rewrite]:
    """One rewrite per comparison of two non-constant operands in the function:
    `a < b` becomes `b > a`."""
    for node in comparisons(find_function(source, symbol)):
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left.type in CONSTANTS or right.type in CONSTANTS:
            continue
        op = node.child_by_field_name("operator").type
        swapped = right.text + b" " + MIRROR[op].encode() + b" " + left.text
        line = source.count(b"\n", 0, node.start_byte) + 1
        yield Rewrite(
            "compare-operand-order",
            Cause.OPERAND_ORDER,
            f"line {line}: {node.text.decode()} -> {swapped.decode()}",
            source[: node.start_byte] + swapped + source[node.end_byte :],
        )


TRANSFORMS = {Cause.OPERAND_ORDER: [swap_operands]}
