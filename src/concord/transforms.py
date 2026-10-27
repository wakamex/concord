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
from itertools import pairwise

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


def _declarator_parts(node: Node) -> list[str]:
    """A function_definition declarator's name as qualification parts, without templates."""
    d = node.child_by_field_name("declarator")
    while d is not None and d.type != "function_declarator":
        d = d.child_by_field_name("declarator")
    if d is None:
        return []
    name = d.child_by_field_name("declarator").text.decode()
    return re.sub(r"<[^<>]*>", "", name).replace(" ", "").split("::")


def _declarator_name(node: Node) -> str:
    """Qualified name of a function_definition's declarator, without templates."""
    return "::".join(_declarator_parts(node)[-2:])


def _enclosing_path(node: Node) -> list[str]:
    """The namespaces and classes enclosing a node, outermost first."""
    path, ancestor = [], node.parent
    while ancestor is not None:
        if ancestor.type in ("namespace_definition", "class_specifier", "struct_specifier", "union_specifier"):
            name = ancestor.child_by_field_name("name")
            if name is not None:
                path[:0] = re.sub(r"<[^<>]*>", "", name.text.decode()).replace(" ", "").split("::")
        ancestor = ancestor.parent
    return path


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


def _type_name(text: str) -> str:
    """A parameter type's unqualified name, without qualifiers, pointers or spaces."""
    text = re.sub(r"\bconst\b", "", text)
    return re.sub(r"[*&\[\]]", "", text).replace(" ", "").split("::")[-1]


def _demangled_params(demangled: str) -> list[str]:
    """The demangled signature's parameter type names, split on top-level commas."""
    params = demangled.partition("(")[2].rsplit(")", 1)[0].strip()
    if params in ("", "void"):
        return []
    out, depth, start = [], 0, 0
    for i, ch in enumerate(params):
        depth += ch in "<("
        depth -= ch in ">)"
        if ch == "," and depth == 0:
            out.append(params[start:i])
            start = i + 1
    out.append(params[start:])
    return [_type_name(p) for p in out]


def _param_types(node: Node) -> list[str]:
    """The function_definition's declared parameter type names."""
    d = node.child_by_field_name("declarator")
    while d is not None and d.type != "function_declarator":
        d = d.child_by_field_name("declarator")
    params = d.child_by_field_name("parameters") if d is not None else None
    if params is None:
        return []
    return [
        _type_name((p.child_by_field_name("type") or p).text.decode())
        for p in params.named_children
        if p.type != "comment"
    ]


def find_function(source: bytes, symbol: str) -> Node:
    """The definition of the symbol's function in source, matched by class,
    name and parameter count, and by the demangled parameter types when several
    same-name overloads share the count, as the drawIndexedTriangleList vertex
    overloads do. An unqualified declarator is also found by its enclosing
    namespaces and classes."""
    demangled = demangle(symbol)
    name, arity = _name_and_arity(demangled)
    parts = re.sub(r"<[^<>]*>", "", demangled.partition("(")[0]).replace(" ", "").split("::")
    tree = Parser(CPP).parse(source)
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition" and _arity(node) == arity:
            declarator = _declarator_parts(node)
            if "::".join(declarator[-2:]) == name or _enclosing_path(node) + declarator == parts:
                found.append(node)
        stack.extend(node.children)
    if not found:
        raise KeyError(f"no definition of {name} in the source")
    if len(found) > 1:
        wanted = _demangled_params(demangled)
        matched = [f for f in found if _param_types(f) == wanted]
        if len(matched) == 1:
            return matched[0]
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


COMMUTATIVE = {"+", "*", "&", "|", "^"}
# How tightly a binary operator binds, higher binds tighter. An operand that
# binds no tighter than the operator it moves under keeps parentheses.
BINDS = {"*": 12, "/": 12, "%": 12, "+": 11, "-": 11, "<<": 10, ">>": 10,
         "<": 9, "<=": 9, ">": 9, ">=": 9, "==": 8, "!=": 8, "&": 7, "^": 6, "|": 5, "&&": 4, "||": 3}
SIDE_EFFECTS = {"call_expression", "assignment_expression", "update_expression", "new_expression", "delete_expression"}


def _side_effect(node: Node) -> bool:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in SIDE_EFFECTS:
            return True
        stack.extend(current.children)
    return False


def _operand(node: Node, operator: str) -> bytes:
    """The operand's text, parenthesized when moving it under `operator` could regroup it."""
    loose = node.type in ("conditional_expression", "assignment_expression", "comma_expression")
    if node.type == "binary_expression":
        own = node.child_by_field_name("operator")
        loose = loose or (own is not None and BINDS.get(own.type, 0) <= BINDS[operator])
    return b"(" + node.text + b")" if loose else node.text


def swap_commutative(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per commutative operation whose operands have no side
    effects: `a + b` becomes `b + a`. The two spellings compute the same
    value, but GCC 4.4 creates the operands' values in source order, which
    sets SSA numbering and with it register choices and the operand order of
    the instruction that consumes the result. An overloaded operator may
    resolve differently swapped, or not at all; the compile step drops
    those, as it does for swapped comparisons."""
    for node in _descendants(scope, "binary_expression"):
        operator = node.child_by_field_name("operator").type
        if operator not in COMMUTATIVE:
            continue
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left.text == right.text or _side_effect(left) or _side_effect(right):
            continue
        swapped = _operand(right, operator) + b" " + operator.encode() + b" " + _operand(left, operator)
        yield _rewrite(
            "commutative-operand-order", Cause.OPERAND_ORDER, source, node, swapped, f"{node.text.decode()} -> {swapped.decode()}"
        )


INVERSE = {b"==": b"!=", b"!=": b"==", b"<": b">=", b">=": b"<", b">": b"<=", b"<=": b">"}
# Expressions that `!` binds to without parentheses.
TIGHT = {"identifier", "field_expression", "call_expression", "subscript_expression", "qualified_identifier",
         "this", "true", "false", "number_literal", "parenthesized_expression", "unary_expression",
         "pointer_expression"}


def _negate(condition: Node, source: bytes) -> bytes:
    """The condition's negation, written as a person would: `!x` becomes `x`,
    `a == b` becomes `a != b`, and `&&`/`||` follow De Morgan's laws, keeping the
    original spacing and line breaks. `a < b` becomes `!(a < b)`, which stays the
    same test for a NaN operand."""
    if condition.type == "parenthesized_expression":
        inner = condition.named_children[0]
        return _negate(inner, source)
    if condition.type == "unary_expression" and condition.child_by_field_name("operator").type == "!":
        argument = condition.child_by_field_name("argument")
        if argument.type == "parenthesized_expression":
            return argument.named_children[0].text
        return argument.text
    if condition.type == "binary_expression":
        left, right = condition.child_by_field_name("left"), condition.child_by_field_name("right")
        operator = condition.child_by_field_name("operator")
        before = source[left.end_byte : operator.start_byte]
        after = source[operator.end_byte : right.start_byte]
        if operator.text in (b"==", b"!="):
            return left.text + before + INVERSE[operator.text] + after + right.text
        if operator.text in INVERSE:
            # `a < b` and `a >= b` differ when an operand is NaN; finish() inverts
            # only where GCC compiles both spellings to the same bytes
            return b"!(" + condition.text + b")"
        if operator.text in (b"&&", b"||"):
            flipped = b"||" if operator.text == b"&&" else b"&&"
            parts = []
            for side in (left, right):
                negated = _negate(side, source)
                # an || operand under && keeps its own grouping
                if flipped == b"&&" and _top_operator(side, source) == b"&&":
                    negated = b"(" + negated + b")"
                parts.append(negated)
            return parts[0] + before + flipped + after + parts[1]
    if condition.type in TIGHT:
        return b"!" + condition.text
    return b"!(" + condition.text + b")"


def _top_operator(node: Node, source: bytes) -> bytes | None:
    """The operator at the top of the node's negation-to-be: the operator of a
    && or || expression, looking through parentheses."""
    while node.type == "parenthesized_expression":
        node = node.named_children[0]
    if node.type == "binary_expression":
        return node.child_by_field_name("operator").text
    return None


def _reindent(text: bytes, by: bytes) -> bytes:
    return text.replace(b"\n", b"\n" + by)


def swap_branches(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per if/else in scope: `if (c) A else B` becomes `if (!c) B else A`.
    The condition is still evaluated once and the same branch runs; GCC 4.4 tends
    to lay out the then-branch as the fall-through. The result follows the file's
    layout: each branch keeps its own text, a block stays a block and a single
    statement stays unbraced, placed the way the original if placed them."""
    for node in _descendants(scope, "if_statement"):
        if node.child_by_field_name("alternative") is None:
            continue
        clause = node.child_by_field_name("condition")
        condition = clause.child_by_field_name("value")
        if condition is None or clause.named_child_count != 1:
            continue  # an init-statement or declaration condition has no simple negation
        then = node.child_by_field_name("consequence")
        alternative = node.child_by_field_name("alternative")
        otherwise = alternative.named_children[-1]
        line = source[_line_start(source, node.start_byte) : node.start_byte]
        indent = line[: len(line) - len(line.lstrip(b" \t"))]  # an else-if takes its else's indent
        layout = _Layout(source, node, clause, then, alternative, otherwise, indent)
        new_then = otherwise
        if otherwise.type == "if_statement":  # an else-if becomes a block of its own
            body = _reindent(otherwise.text, layout.unit) if layout.multiline else otherwise.text
            new_then_text = b"{" + layout.inner + body + layout.close + b"}"
            new_then = None
        swapped = (
            b"if (" + _negate(condition, source) + b")"
            + (layout.before(otherwise.type) + otherwise.text if new_then is not None else layout.before("compound_statement") + new_then_text)
            + layout.middle(otherwise.type if new_then is not None else "compound_statement")
            + b"else"
            + layout.before(then.type) + then.text
        )  # fmt: skip
        yield _rewrite("branch-sense", Cause.BLOCK_ORDER, source, node, swapped, f"if ({condition.text.decode()}) swapped")


class _Layout:
    """How an if statement places its branches: on one line or across lines, and
    with a block's brace on the if's line or on a line of its own."""

    def __init__(self, source, node, clause, then, alternative, otherwise, indent):
        self.multiline = b"\n" in node.text
        self.indent = indent
        self.unit = b"    "
        gaps = {  # the whitespace before each branch, by kind
            then.type == "compound_statement": source[clause.end_byte : then.start_byte],
            otherwise.type == "compound_statement": source[alternative.child(0).end_byte : otherwise.start_byte],
        }
        simple = gaps.get(False)
        if simple is not None and b"\n" in simple:
            self.unit = simple.rsplit(b"\n", 1)[1][len(indent) :] or self.unit
        block = gaps.get(True)
        if block is None:  # no block in this if: follow the file's habit
            block = b" " if source.count(b") {") > source.count(b")\n" + indent + b"{") else b"\n" + indent
        self.block_gap = block if self.multiline else b" "
        self.simple_gap = (b"\n" + indent + self.unit) if self.multiline else b" "
        self.inner = (b"\n" + indent + self.unit) if self.multiline else b" "
        self.close = (b"\n" + indent) if self.multiline else b" "

    def before(self, kind: str) -> bytes:
        return self.block_gap if kind == "compound_statement" else self.simple_gap

    def middle(self, kind: str) -> bytes:
        if not self.multiline:
            return b" "
        if kind == "compound_statement" and b"\n" not in self.block_gap:
            return b" "  # } else
        return b"\n" + self.indent


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
        if n.type == "init_declarator" or n.type == "declaration" and n.child_by_field_name("declarator").type != "init_declarator":
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
        for first, second in pairwise(statements):
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
            for j in [*range(i), *range(i + 2, first_use + 1)]:
                at = _line_start(source, statements[j].start_byte)
                text = source[at : statements[j].start_byte] + declaration.text + b"\n"
                if at < start:
                    moved = source[:at] + text + source[at:start] + source[end:]
                else:
                    moved = source[:start] + source[end:at] + text + source[at:]
                line = source.count(b"\n", 0, declaration.start_byte) + 1
                target = source.count(b"\n", 0, at) + 1
                yield Rewrite(
                    "declaration-order",
                    Cause.REGISTER_ALLOCATION,
                    f"line {line}: {declaration.text.decode()} moved to line {target}",
                    moved,
                )


def split_declaration(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`float x = e;` as `float x; x = e;`, for a non-const local of a built-in
    type. GCC 4.4 numbers the declaration and the assignment as separate
    statements, which moves the order it creates later values in."""
    for node in _descendants(scope, "declaration"):
        kinds = [c.type for c in node.named_children]
        if kinds != ["primitive_type", "init_declarator"] or node.parent is None or node.parent.type != "compound_statement":
            continue
        declarator = node.named_children[1]
        name, value = declarator.child_by_field_name("declarator"), declarator.child_by_field_name("value")
        if name is None or value is None or name.type != "identifier" or value.type in ("initializer_list", "argument_list"):
            continue
        indent = source[_line_start(source, node.start_byte) : node.start_byte]
        if indent.strip():
            continue
        split = node.named_children[0].text + b" " + name.text + b";\n" + indent + name.text + b" = " + value.text + b";"
        yield _rewrite("split-declaration", Cause.REGISTER_ALLOCATION, source, node, split, f"{name.text.decode()} declared, then assigned")


def join_declaration(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`float x; x = e;` as `float x = e;`, the inverse of split_declaration:
    a declaration of a built-in type whose next statement assigns the name."""
    for block in _descendants(scope, "compound_statement"):
        statements = [c for c in block.named_children if c.type != "comment"]
        for declaration, statement in pairwise(statements):
            kinds = [c.type for c in declaration.named_children] if declaration.type == "declaration" else []
            if len(kinds) != 2 or kinds[0] != "primitive_type":
                continue
            name = declaration.child_by_field_name("declarator")
            if name is None or name.type != "identifier" or statement.type != "expression_statement" or len(statement.named_children) != 1:
                continue
            assignment = statement.named_children[0]
            if assignment.type != "assignment_expression" or assignment.child_by_field_name("operator").type != "=":
                continue
            left, value = assignment.child_by_field_name("left"), assignment.child_by_field_name("right")
            if left is None or left.type != "identifier" or left.text != name.text or value is None:
                continue
            start = _line_start(source, declaration.start_byte)
            if source[start : declaration.start_byte].strip():
                continue
            end = source.find(b"\n", statement.end_byte) + 1 or len(source)
            joined = declaration.named_children[0].text + b" " + name.text + b" = " + value.text + b";\n"
            line = source.count(b"\n", 0, declaration.start_byte) + 1
            yield Rewrite(
                "split-declaration",
                Cause.REGISTER_ALLOCATION,
                f"line {line}: {name.text.decode()} assigned in its declaration",
                source[:start] + source[start : declaration.start_byte] + joined + source[end:],
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


def inline_temporaries(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per local that is initialized where it is declared and used
    once, in the next statement: `T x = e; f(x);` becomes `f(e);`. This undoes
    name_temporaries, and removes a temporary the source's author named where the
    original had none. A local whose address is taken or that is written again is
    left alone, as is a use under `&&`, `||` or `?:`, where `e` would no longer be
    evaluated every time. The declared type's conversion goes with the
    declaration, so a rewrite that depended on it changes the bytes or fails to
    compile."""
    # a temporary name_temporaries introduced: its expression goes back where the name stands
    typeof = re.compile(rb"^[ \t]*__typeof__\((?P<expr>.*)\) (?P<name>concordTmp\d+) = (?P=expr);\n", re.MULTILINE)
    for found in typeof.finditer(source, scope.start_byte, scope.end_byte):
        uses = list(re.finditer(rb"\b" + found["name"] + rb"\b", source[found.end() : scope.end_byte]))
        if len(uses) == 1:
            at = found.end() + uses[0].start()
            text = source[: found.start()] + source[found.end() : at] + found["expr"] + source[at + len(found["name"]) :]
            line = source.count(b"\n", 0, found.start()) + 1
            yield Rewrite("named-temporary", Cause.REGISTER_ALLOCATION, f"line {line}: {found['name'].decode()} inlined into its one use", text)
    for block in _descendants(scope, "compound_statement"):
        statements = [c for c in block.named_children if c.type != "comment"]
        for index, (declaration, use) in enumerate(pairwise(statements)):
            declarators = [c for c in declaration.named_children if c.type == "init_declarator"] if declaration.type == "declaration" else []
            if len(declarators) != 1 or use.type not in SIMPLE | {"return_statement", "if_statement"}:
                continue
            name, value = declarators[0].child_by_field_name("declarator"), declarators[0].child_by_field_name("value")
            if name is None or value is None or name.type != "identifier" or value.type in ("initializer_list", "argument_list"):
                continue
            head = use.child_by_field_name("condition") if use.type == "if_statement" else use  # not the branches of an if
            uses = [i for i in _descendants(head, "identifier") if i.text == name.text]
            later = [i for s in statements[index + 1 :] for i in _descendants(s, "identifier") if i.text == name.text]
            if len(uses) != 1 or len(later) != 1 or _written(uses[0]):
                continue
            if any(
                a.type in ("conditional_expression", "lambda_expression")
                or (a.type == "binary_expression" and a.child_by_field_name("operator").type in ("&&", "||"))
                for a in _ancestors(uses[0], use)
            ):
                continue
            inlined = value.text if value.type in TIGHT or uses[0].parent.type in ("argument_list", "init_declarator", "return_statement") else b"(" + value.text + b")"
            text = source[: uses[0].start_byte] + inlined + source[uses[0].end_byte :]
            start = _line_start(source, declaration.start_byte)
            end = source.find(b"\n", declaration.end_byte) + 1
            if source[start : declaration.start_byte].strip() or source[declaration.end_byte : end].strip():
                start, end = declaration.start_byte, declaration.end_byte  # shares its line with other code
            line = source.count(b"\n", 0, declaration.start_byte) + 1
            yield Rewrite(
                "named-temporary",
                Cause.REGISTER_ALLOCATION,
                f"line {line}: {name.text.decode()} inlined into its one use",
                text[:start] + text[end:],
            )


PLAIN = {"identifier", "field_expression", "number_literal"}


def while_loop(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`for (init; cond; step) { body }` as `{ init; while (cond) { body step; } }`,
    for loops whose body has no continue."""
    for node in _descendants(scope, "for_statement"):
        fields = {name: node.child_by_field_name(name) for name in ("initializer", "condition", "update", "body")}
        body = fields["body"]
        if None in fields.values() or _descendants(body, "continue_statement"):
            continue
        initializer = fields["initializer"].text
        initializer += b"" if initializer.rstrip().endswith(b";") else b";"
        braced = body.text if body.type == "compound_statement" else b"{\n" + body.text + b"\n}"
        inner = braced[: braced.rindex(b"}")].rstrip() + b"\n" + fields["update"].text + b";\n}"
        replacement = b"{ " + initializer + b" while (" + fields["condition"].text + b") " + inner + b" }"
        yield _rewrite("loop-form", Cause.BLOCK_ORDER, source, node, replacement, "for loop as a while loop")


def for_loop(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`init; while (cond) { body step; }` as `for (init; cond; step) { body }`, when
    the statement before the loop is a declaration or an expression, the body ends
    in an expression statement and has no continue. A variable the declaration
    introduces is then scoped to the loop, so a later use fails to compile."""
    for block in _descendants(scope, "compound_statement"):
        statements = [c for c in block.named_children if c.type != "comment"]
        for initializer, loop in pairwise(statements):
            body = loop.child_by_field_name("body") if loop.type == "while_statement" else None
            inner = [c for c in body.named_children if c.type != "comment"] if body is not None and body.type == "compound_statement" else []
            if initializer.type not in SIMPLE or not inner or inner[-1].type != "expression_statement":
                continue
            if _descendants(body, "continue_statement"):
                continue
            condition = loop.child_by_field_name("condition").text
            condition = condition[1:-1] if condition.startswith(b"(") and condition.endswith(b")") else condition
            step = inner[-1].text.rstrip().removesuffix(b";")
            kept = source[body.start_byte : inner[-1].start_byte].rstrip() + b"\n" + source[_line_start(source, body.end_byte - 1) : body.end_byte]
            replacement = b"for (" + initializer.text + b" " + condition + b"; " + step + b") " + kept
            line = source.count(b"\n", 0, initializer.start_byte) + 1
            yield Rewrite(
                "loop-form",
                Cause.BLOCK_ORDER,
                f"line {line}: while loop as a for loop",
                source[: initializer.start_byte] + replacement + source[loop.end_byte :],
            )


EXPANDS = {"+=": b"+", "-=": b"-", "*=": b"*", "|=": b"|", "&=": b"&"}


def expand_assignment(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`a += b` as `a = a + (b)`, for a target with no side effects."""
    for node in _descendants(scope, "assignment_expression"):
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        operator = node.child_by_field_name("operator").type
        if operator in EXPANDS and left.type in PLAIN:
            value = right.text if right.type in PLAIN else b"(" + right.text + b")"
            expanded = left.text + b" = " + left.text + b" " + EXPANDS[operator] + b" " + value
            yield _rewrite("compound-assignment", Cause.INSTRUCTION_SELECTION, source, node, expanded, f"{node.text.decode()} spelled out")


COMPRESSES = {"+": b"+=", "-": b"-=", "*": b"*=", "|": b"|=", "&": b"&="}


def compress_assignment(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`a = a + b` as `a += b`, for a target with no side effects."""
    for node in _descendants(scope, "assignment_expression"):
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if node.child_by_field_name("operator").type != "=" or left.type not in PLAIN or right.type != "binary_expression":
            continue
        operator = right.child_by_field_name("operator").type
        if operator in COMPRESSES and right.child_by_field_name("left").text == left.text:
            rest = right.child_by_field_name("right").text
            compressed = left.text + b" " + COMPRESSES[operator] + b" " + rest
            yield _rewrite("compound-assignment", Cause.INSTRUCTION_SELECTION, source, node, compressed, f"{node.text.decode()} compressed")


def nest_condition(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`if (a && b) X` without an else as an if inside an if, laid out as the
    source lays out its ifs:

        if (a)
        {
            if (b)
                X
        }
    """
    for node in _descendants(scope, "if_statement"):
        clause, body = node.child_by_field_name("condition"), node.child_by_field_name("consequence")
        inner = clause.named_children[0] if clause is not None and len(clause.named_children) == 1 else None
        if node.child_by_field_name("alternative") is not None or inner is None or inner.type != "binary_expression":
            continue
        if inner.child_by_field_name("operator").type != "&&":
            continue
        indent = source[_line_start(source, node.start_byte) : node.start_byte]
        if indent.strip():
            continue  # the if shares its line with other code
        left, right = inner.child_by_field_name("left"), inner.child_by_field_name("right")
        step = b"\t" if b"\t" in indent else b"    "
        # the body's own lines move one level in; its first line follows the condition
        between = source[clause.end_byte : body.start_byte]
        inner_body = between + _reindent(body.text, step) if b"\n" in between else b" " + _reindent(body.text, step)
        nested = (
            b"if (" + left.text + b")\n" + indent + b"{\n"
            + indent + step + b"if (" + _reindent(right.text, step) + b")" + inner_body.replace(b"\n", b"\n" + step, 1 if b"\n" in between else 0) + b"\n"
            + indent + b"}"
        )  # fmt: skip
        yield _rewrite("condition-nesting", Cause.BLOCK_ORDER, source, node, nested, "the halves of && as nested ifs")


LOOSE = {"conditional_expression", "assignment_expression", "comma_expression"}


def merge_conditions(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """`if (a) { if (b) X }` as `if (a && b) X`, when neither if has an else and the
    inner one is all the outer one holds."""
    for node in _descendants(scope, "if_statement"):
        body = node.child_by_field_name("consequence")
        if node.child_by_field_name("alternative") is not None or body is None:
            continue
        held = [c for c in body.named_children if c.type != "comment"] if body.type == "compound_statement" else [body]
        if len(held) != 1 or held[0].type != "if_statement" or held[0].child_by_field_name("alternative") is not None:
            continue
        halves = []
        for statement in (node, held[0]):
            clause = statement.child_by_field_name("condition")
            inner = clause.named_children[0] if clause is not None and len(clause.named_children) == 1 else None
            if inner is None or inner.type == "declaration":
                break
            loose = inner.type in LOOSE or (inner.type == "binary_expression" and inner.child_by_field_name("operator").type == "||")
            halves.append(b"(" + inner.text + b")" if loose else inner.text)
        if len(halves) == 2:
            # the inner if's body, one level out, after the same gap the inner if had
            inner_if = held[0]
            gap = source[inner_if.child_by_field_name("condition").end_byte : inner_if.child_by_field_name("consequence").start_byte]
            indent = source[_line_start(source, node.start_byte) : node.start_byte]
            step = b"\t" if b"\t" in indent else b"    "
            prefix = b"\n" + step
            body = inner_if.child_by_field_name("consequence").text.replace(prefix, b"\n")
            gap = gap.replace(prefix, b"\n", 1) if b"\n" in gap else b" "
            merged = b"if (" + halves[0] + b" && " + halves[1].replace(prefix, b"\n") + b")" + gap + body
            yield _rewrite("condition-nesting", Cause.BLOCK_ORDER, source, node, merged, "nested ifs as one && condition")


BYTE_POINTER = re.compile(rb"^(unsigned\s+|signed\s+)?char\s*\*+$")
INTEGER_CASTS = {
    b"int", b"long", b"short", b"unsigned", b"unsigned int", b"unsigned long", b"unsigned short",
    b"signed", b"signed int", b"size_t", b"ssize_t", b"intptr_t", b"uintptr_t", b"ptrdiff_t",
}


def _cast_type(node: Node) -> Node | None:
    """A cast_expression's type_descriptor child, or None."""
    if node.type != "cast_expression":
        return None
    return next((c for c in node.children if c.type == "type_descriptor"), None)


def address_arithmetic(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per spelling of an address sum cast to another type:
    `(T*)((char*)p + n)` as `(T*)((int)p + n)` and `(T*)((long)p + n)`, and an
    integer cast back as `(T*)((char*)p + n)`. Pointer arithmetic folds a
    constant into the memory operand's displacement; integer arithmetic keeps
    the sum in a register, which is the `reg` against `reg + const` split the
    diff reports as struct-layout."""
    for node in _descendants(scope, "binary_expression"):
        if node.child_by_field_name("operator").type not in ("+", "-"):
            continue
        parent, grandparent = node.parent, node.parent.parent if node.parent is not None else None
        if parent is None or parent.type != "parenthesized_expression" or grandparent is None or grandparent.type != "cast_expression":
            continue
        for operand in (node.child_by_field_name("left"), node.child_by_field_name("right")):
            descriptor = _cast_type(operand)
            if descriptor is None:
                continue
            spelling = re.sub(rb"\s+", b" ", descriptor.text.strip())
            for replacement in (b"int", b"long") if BYTE_POINTER.match(descriptor.text) else (b"char*",) if spelling in INTEGER_CASTS else ():
                yield _rewrite(
                    "address-arithmetic",
                    Cause.STRUCT_LAYOUT,
                    source,
                    descriptor,
                    replacement,
                    f"({descriptor.text.decode()}) as ({replacement.decode()})",
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


TRANSFORMS = {Cause.OPERAND_ORDER: [swap_operands, swap_commutative], Cause.BLOCK_ORDER: [swap_branches], Cause.STRUCT_LAYOUT: [address_arithmetic]}
# Every rewrite, for searches that do not pick rewrites by cause.
def flip_nan_sense(source: bytes, scope: Node) -> Iterator[Rewrite]:
    """One rewrite per relational comparison in scope that changes how it treats
    NaN: `a > b` becomes `!(a <= b)` and `!(a <= b)` becomes `a > b`. For integers
    the two are the same test; for floats they differ exactly when an operand is
    NaN. This is the one transform meant to change behavior, so that source whose
    NaN handling differs from the original can be brought back to it; the oracle
    accepts such a rewrite only when it moves the function closer to the original
    code."""
    for node in _descendants(scope, "binary_expression"):
        operator = node.child_by_field_name("operator")
        if operator.text not in INVERSE or operator.text in (b"==", b"!="):
            continue
        parent = node.parent
        negated = (
            parent is not None
            and parent.type == "parenthesized_expression"
            and parent.parent is not None
            and parent.parent.type == "unary_expression"
            and parent.parent.child_by_field_name("operator").type == "!"
        )
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        inverted = left.text + source[left.end_byte : operator.start_byte] + INVERSE[operator.text] + source[operator.end_byte : right.start_byte] + right.text
        if negated:
            yield _rewrite("nan-sense", Cause.BLOCK_ORDER, source, parent.parent, inverted, f"{parent.parent.text.decode()} as {inverted.decode()}")
        else:
            yield _rewrite("nan-sense", Cause.BLOCK_ORDER, source, node, b"!(" + inverted + b")", f"{node.text.decode()} as !({inverted.decode()})")


ALL = [
    swap_operands,
    swap_commutative,
    swap_branches,
    swap_statements,
    move_declarations,
    split_declaration,
    join_declaration,
    name_temporaries,
    inline_temporaries,
    while_loop,
    for_loop,
    expand_assignment,
    compress_assignment,
    nest_condition,
    merge_conditions,
    flip_nan_sense,
    address_arithmetic,
]
