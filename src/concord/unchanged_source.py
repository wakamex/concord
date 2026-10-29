"""Conservative source and inline-dependency evidence for authored overlays."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from tree_sitter import Parser

from concord.inline_budget import GRAPH, parse
from concord.transforms import CPP, _descendants


def _family(description: str) -> str:
    """A qualified function family, conservatively merging template/overload variants."""
    if re.fullmatch(r'\(static initializers for [^()]+\)', description):
        return description
    # GCC prints template substitutions after the signature. Strip balanced
    # template arguments, but retain comparison operators in operator names.
    text, depth = '', 0
    for character in description.split(' [with ', 1)[0]:
        if character == '<' and not text.rstrip().endswith('operator'):
            depth += 1
        elif character == '>' and depth:
            depth -= 1
        elif not depth:
            text += character
    if depth:
        raise ValueError('unresolved template name')
    text = re.sub(r'\s*::\s*', '::', text)
    match = re.search(r'((?:[A-Za-z_]\w*::)*(?:operator(?:\(\)|\[\]|[^\w\s(]+)|~?[A-Za-z_]\w*))\(', text)
    if not match:
        raise ValueError(f'unresolved function name: {description}')
    return match[1]


def changed_functions(originals: dict[str, bytes], files: dict[str, bytes]) -> set[str] | None:
    """Return changed function families, or None for any non-body edit or parse doubt.

    Replacing bodies with a marker must leave identical files. Declarations,
    initializers, definition order and directives therefore cannot be exempted.
    """
    changed = set()
    for name, original in originals.items():
        snapshots = []
        for source in (original, files[name]):
            tree = Parser(CPP).parse(source)
            if tree.root_node.has_error:
                return None
            definitions = _descendants(tree.root_node, 'function_definition')
            skeleton, bodies, end = [], [], 0
            for node in definitions:
                body = node.child_by_field_name('body')
                declarator = node.child_by_field_name('declarator')
                while declarator is not None and declarator.type != 'function_declarator':
                    declarator = declarator.child_by_field_name('declarator')
                if body is None or declarator is None or body.start_byte < end:
                    return None
                # A directive inside a body can change the rest of the unit.
                if re.search(rb'(?m)^\s*#', body.text):
                    return None
                identifier = declarator.child_by_field_name('declarator')
                if identifier is None:
                    return None
                try:
                    enclosing = []
                    ancestor = node.parent
                    while ancestor is not None:
                        if ancestor.type in {'namespace_definition', 'class_specifier', 'struct_specifier', 'union_specifier'}:
                            scope = ancestor.child_by_field_name('name')
                            if scope is None:
                                return None  # Anonymous scopes have compiler-specific names.
                            enclosing.insert(0, scope.text.decode())
                        ancestor = ancestor.parent
                    family = _family('::'.join([*enclosing, identifier.text.decode()]) + '()')
                except ValueError:
                    return None
                skeleton.extend((source[end:body.start_byte], b'{}'))
                bodies.append((family, body.text))
                end = body.end_byte
            skeleton.append(source[end:])
            snapshots.append((b''.join(skeleton), bodies))
        if snapshots[0][0] != snapshots[1][0]:
            return None
        if len(snapshots[0][1]) != len(snapshots[1][1]):
            return None
        for (old_name, old_body), (new_name, new_body) in zip(snapshots[0][1], snapshots[1][1], strict=True):
            if old_name != new_name:
                return None
            if old_body != new_body:
                changed.add(old_name)
    return changed


def _dependencies(ipa: str, early: str, symbol: str) -> tuple[str, set[str]]:
    parse(ipa)  # Require the complete IPA decision summary.
    if 'callgraph:' not in ipa or ';; Function ' not in early:
        raise ValueError('incomplete inline dumps')
    descriptions = re.findall(r'^;; Function (.+) \(' + re.escape(symbol) + r'\)$', ipa, re.MULTILINE)
    if len(set(descriptions)) != 1:
        raise ValueError('unresolved emitted function')
    root = _family(descriptions[0])
    edges = defaultdict(set)
    graph_names = set()

    def edge(caller, callee):
        edges[_family(caller)].add(callee)

    for line in ipa.splitlines():
        match = GRAPH.match(line)
        if match:
            graph_names.add(_family(match[1]))
        if '(inline copy in ' in line:
            owner = re.search(r'\(inline copy in (.+)/\d+\) availability:', line)
            if not match or not owner:
                raise ValueError('unresolved inline copy')
            edge(owner[1], match[1])
    if root not in graph_names:
        raise ValueError('missing function in inline graph')
    for callee, caller in re.findall(r'^ Inlining (.+) into (.+)\.$', early, re.MULTILINE):
        edge(caller, callee)
    pending, inlined = [root], set()
    while pending:
        for description in edges[pending.pop()]:
            callee = _family(description)
            if callee not in inlined:
                inlined.add(callee)
                pending.append(callee)
    return root, inlined


def evidence(before: Path | None, after: Path | None, symbol: str, changed: set[str] | None) -> dict | None:
    """Union both compiles' transitive inlines; any doubt requests the oracle."""
    if changed is None or before is None or after is None:
        return None
    inlined = set()
    try:
        for object_path in (before, after):
            stem = object_path.stem
            ipa, = object_path.parent.glob(stem + '.*i.inline')
            early, = object_path.parent.glob(stem + '.*t.einline2')
            root, dependencies = _dependencies(ipa.read_text(), early.read_text(), symbol)
            if root in changed:
                return None
            inlined.update(dependencies)
    except (OSError, UnicodeError, ValueError, KeyError):
        return None
    if inlined & changed:
        return None
    return {'inlined': sorted(inlined)}
