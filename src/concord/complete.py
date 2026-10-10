"""Complete Harvest's partial ox core types with Irrlicht's members.

Harvest's ox::core and ox::video types are adapted from Irrlicht 0.7 headers and
declare only what recovered units use so far ("Partial" in their notices).
daisy's own copies had Irrlicht's full set, so when a seeded file calls a member
Harvest's header lacks, such as CAabbox3d::reset, the member is Irrlicht's.
`complete` copies every overload of that member from Irrlicht's class into
Harvest's, translated. An inline member is emitted only where it is used, yet
adding members to these widely included headers still changed GCC 4.4's code in
units that include them, so seeding completes headers only when asked, and the
result needs a whole-build check with concord scores.

std::vector-based containers (ox::TArray, ox::TList) are left alone: daisy
replaced Irrlicht's containers, so Irrlicht's members are not theirs.
"""

from __future__ import annotations

import re
from pathlib import Path

from tree_sitter import Node, Parser

from concord.irrlicht import translate
from concord.transforms import CPP

# Harvest's ox type -> (its header under src/, Irrlicht's header under include/, Irrlicht's class)
TYPES = {
    "CAabbox3d": ("ox/core/CAabbox3d.h", "aabbox3d.h", "aabbox3d"),
    "CVector3d": ("ox/core/CVector3d.h", "vector3d.h", "vector3d"),
    "CVector2d": ("ox/core/CVector2d.h", "vector2d.h", "vector2d"),
    "CPosition2d": ("ox/core/CPosition2d.h", "position2d.h", "position2d"),
    "CDimension2d": ("ox/core/CDimension2d.h", "dimension2d.h", "dimension2d"),
    "CRect": ("ox/core/CRect.h", "rect.h", "rect"),
    "CLine3d": ("ox/core/CLine3d.h", "line3d.h", "line3d"),
    "CTriangle3d": ("ox/core/CTriangle3d.h", "triangle3d.h", "triangle3d"),
    "CMatrix4": ("ox/core/CMatrix4.h", "matrix4.h", "matrix4"),
    "SColor": ("ox/video/SColor.h", "SColor.h", "SColor"),
}

MISSING = re.compile(r"'(?:class|struct|const class|const struct) ox::(?:core|video)::(\w+)(?:<[^']*>)?' has no member named '(\w+)'")


def missing_members(compiler_output: str) -> set[tuple[str, str]]:
    """(ox type, member) pairs the compiler reports missing, for types this module completes."""
    return {(t, m) for t, m in MISSING.findall(compiler_output) if t in TYPES}


def complete(root: Path, missing: set[tuple[str, str]]) -> list[str]:
    """Add each missing member's Irrlicht overloads to Harvest's header; return the
    headers changed."""
    changed = []
    for ox_type, member in sorted(missing):
        header, irrlicht_header, irrlicht_class = TYPES[ox_type]
        path = root / "src" / header
        original = (root / "third_party" / "irrlicht-0.7" / "include" / irrlicht_header).read_text(encoding="latin-1")
        definitions = _members(original, irrlicht_class, member)
        if not definitions:
            continue
        text = path.read_text()
        added = "".join(_indent(_rename(d)) + "\n\n" for d in definitions)
        public = re.search(r"^(class|struct) " + ox_type + r"\b[^{]*\{\s*\n(\s*public:\s*\n)?", text, re.MULTILINE)
        if public is None:
            continue
        text = text[: public.end()] + added + text[public.end() :]
        path.write_text(text)
        changed.append(header)
    return sorted(set(changed))


def _members(source: str, cls: str, member: str) -> list[str]:
    tree = Parser(CPP).parse(source.encode("latin-1"))
    out = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("class_specifier", "struct_specifier"):
            name = node.child_by_field_name("name")
            if name is not None and name.text.decode() == cls:
                body = node.child_by_field_name("body")
                out += [c.text.decode("latin-1") for c in body.named_children if _defines(c, member)]
                continue
        stack.extend(node.children)
    return out


def _defines(node: Node, member: str) -> bool:
    if node.type != "function_definition":
        return False
    declarator = node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    if declarator is None:
        return False
    name = declarator.child_by_field_name("declarator")
    return name is not None and name.text.decode() == member


def _rename(definition: str) -> str:
    """Translate a member body written inside Irrlicht's core namespace, where the
    core templates are named bare (vector3d<T>) rather than core::vector3d<T>."""
    text = translate(definition)
    for ox_type, (_, _, irrlicht_class) in TYPES.items():
        text = re.sub(r"(?<![\w:])" + irrlicht_class + r"\b", ox_type, text)
    return text


def _indent(definition: str) -> str:
    lines = definition.replace("\t", "    ").split("\n")
    first, rest = lines[0].strip(), lines[1:]
    common = min((len(line) - len(line.lstrip()) for line in rest if line.strip()), default=0)
    body = ["    " + line[common:] if line.strip() else "" for line in rest]
    return "\n".join(["    " + first, *body])
