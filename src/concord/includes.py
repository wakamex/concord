"""Include closures and the functions and macros of gruntz-decomp headers.

A unit compiles against the headers under include/ and then the directories under vendor/, in
that order (gruntz-decomp's cl.repo_include_flags), so a name resolves to the first of those that
holds it. The closure of a unit is every header reached by #include from its source. A definition
is a function or macro in a header, keyed by its scope, name and parameter list, and compared by
its tokens: comments and whitespace do not count as a change.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import NamedTuple

from tree_sitter import Node, Parser

from concord.transforms import CPP

INCLUDE = re.compile(rb"^[ \t]*#[ \t]*include[ \t]*[<\"]([^>\"]+)[>\"]", re.MULTILINE)
CLASSES = ("class_specifier", "struct_specifier", "union_specifier")


class Tree:
    """The files a unit can include at one version: `names` are their paths, `read` gives a file's bytes."""

    def __init__(self, names: set[str], read: Callable[[str], bytes]) -> None:
        self.names = names
        self._read = read
        self._cache: dict[str, bytes | None] = {}
        vendored = sorted({name.split("/")[1] for name in names if name.startswith("vendor/") and name.count("/") >= 2})
        self.dirs = ["include", *(f"vendor/{directory}" for directory in vendored)]

    @classmethod
    def working(cls, root: Path, paths: list[str]) -> Tree:
        """The files as the working tree holds them, with `paths` as well."""
        names = {path.relative_to(root).as_posix() for directory in ("include", "vendor") for path in (root / directory).rglob("*") if path.is_file()}
        names.update(paths)
        return cls(names, lambda path: (root / path).read_bytes())

    def read(self, path: str) -> bytes | None:
        if path not in self._cache:
            self._cache[path] = self._read(path) if path in self.names else None
        return self._cache[path]

    def resolve(self, name: str) -> str | None:
        for directory in self.dirs:
            path = f"{directory}/{name}"
            if path in self.names:
                return path
        return None


def closure(tree: Tree, source: str) -> set[str]:
    """The source and every header it reaches through #include, as paths in the tree."""
    found: set[str] = set()
    todo = [source]
    while todo:
        path = todo.pop()
        if path in found:
            continue
        found.add(path)
        text = tree.read(path)
        for name in INCLUDE.findall(text or b""):
            resolved = tree.resolve(name.decode())
            if resolved:
                todo.append(resolved)
    return found


def words(node: Node, text: bytes) -> str:
    """The node's tokens joined by single spaces, without comments."""
    out: list[str] = []

    def walk(current: Node) -> None:
        if current.type == "comment":
            return
        if current.child_count == 0:
            out.append(text[current.start_byte : current.end_byte].decode(errors="replace"))
        for child in current.children:
            walk(child)

    walk(node)
    return " ".join(" ".join(out).split())


class Definition(NamedTuple):
    kind: str  # "function" or "macro"
    scope: str  # the class a function belongs to, or ""
    name: str
    signature: str  # the parameter list and qualifiers, without the name and return type
    text: str  # the whole definition's tokens
    line: int  # 1-based line of the definition in its header

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.kind, self.scope, self.name, self.signature)

    @property
    def qualified(self) -> str:
        return f"{self.scope}::{self.name}" if self.scope else self.name


def _line(node: Node, text: bytes) -> int:
    """The 1-based line a node starts on. Counted from its byte offset: reading `start_point` in tree-sitter 0.26.0
    corrupts the heap, so it is not used."""
    return text.count(b"\n", 0, node.start_byte) + 1


def _function(node: Node, text: bytes) -> Definition | None:
    declarator = node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    if declarator is None or declarator.child_by_field_name("declarator") is None:
        return None
    name = declarator.child_by_field_name("declarator")
    written = text[name.start_byte : name.end_byte].decode(errors="replace").replace(" ", "")
    scope, _, simple = written.rpartition("::")
    if not scope:
        parent = node.parent
        while parent is not None and parent.type not in CLASSES:
            parent = parent.parent
        named = parent.child_by_field_name("name") if parent is not None else None
        scope = text[named.start_byte : named.end_byte].decode(errors="replace") if named is not None else ""
    parts = [words(child, text) for child in declarator.children if (child.start_byte, child.end_byte) != (name.start_byte, name.end_byte)]
    return Definition("function", scope, simple, " ".join(part for part in parts if part), words(node, text), _line(node, text))


@cache
def definitions(text: bytes) -> dict[tuple[str, str, str, str], Definition]:
    """Every function and macro defined in a header, by key. A key defined twice keeps its last definition."""
    found: dict[tuple[str, str, str, str], Definition] = {}
    tree = Parser(CPP).parse(text)  # held by name while its nodes are read
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("preproc_def", "preproc_function_def"):
            name = node.child_by_field_name("name")
            if name is not None:
                definition = Definition("macro", "", text[name.start_byte : name.end_byte].decode(errors="replace"), "", words(node, text), _line(node, text))
                found[definition.key] = definition
        elif node.type == "function_definition":
            definition = _function(node, text)
            if definition is not None:
                found[definition.key] = definition
        stack.extend(node.children)
    return found
