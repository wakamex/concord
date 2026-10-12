"""Give Harvest's matching build what one of its headers keeps for the port.

Harvest's port (port/) reimplements parts of daisy that have no recovered source
yet. Where that needed a class's fields, constructor or helper members, they sit
in the ox header behind `#ifdef HARVEST_PORT`, with a byte array standing in for
the fields in the matching build, and the member functions Irrlicht defines
inline live out of line in a port source file. A daisy file seeded from Irrlicht
needs the same fields and members in the matching build, where the original had
them inline.

`unguard` rewrites such a header for both builds: the declarations guarded for
the port become unconditional (the stand-in of an `#else` branch goes), and the
class's member functions the port source defines are added after the class as
inline definitions, compiled only when HARVEST_PORT is not defined, since the
port keeps its own out-of-line copies. The port source calls free functions
where Irrlicht calls members the recovered ox types lack
(`daisy::scene::transformBox(matrix, box)` for `matrix.transformBox(box)`); the
inline copies call the members, which concord.complete adds from Irrlicht.

Not every HARVEST_PORT block is recovered original code (ox/game/CGameMain.h
guards additions of the port's own), so unguard is applied header by header.
"""

from __future__ import annotations

import re

from tree_sitter import Parser

from concord.irrlicht import translate
from concord.transforms import CPP

GUARD = "HARVEST_PORT"
# the port's free functions named differently from the member they stand for
MEMBERS = {"resetBox": "reset", "repairBox": "repair"}


def expose(header: str) -> str:
    """The header with every `#ifdef HARVEST_PORT` block made unconditional and the
    `#else` branch of such a block dropped."""
    out, stack = [], []  # stack: "keep" or "drop" for a guard block, None for another conditional
    for line in header.split("\n"):
        word = line.strip()
        if re.fullmatch(r"#\s*ifdef\s+" + GUARD, word):
            stack.append("keep")
            continue
        if re.match(r"#\s*if", word):
            stack.append(None)
        elif re.match(r"#\s*else\b", word) and stack and stack[-1] is not None:
            stack[-1] = "drop"
            continue
        elif re.match(r"#\s*endif\b", word) and stack and stack.pop() is not None:
            continue
        if "drop" not in stack:
            out.append(line)
    return "\n".join(out)


def definitions(source: str, cls: str) -> dict[str, str]:
    """The member functions of `cls` the source defines, by member name, as inline
    definitions that call members where the source calls the port's free functions."""
    tree = Parser(CPP).parse(source.encode())
    found, stack = {}, [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            name = _declared_name(node)
            if name is not None and name.text.decode().startswith(f"{cls}::"):
                found[name.text.decode().removeprefix(f"{cls}::")] = "inline " + _member_calls(node.text.decode())
            continue
        stack.extend(reversed(node.children))
    return found


def _declared_name(node):
    declarator = node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    return declarator.child_by_field_name("declarator") if declarator is not None else None


def irrlicht_definitions(original: str, irrlicht_cls: str, cls: str) -> dict[str, str]:
    """The member functions Irrlicht's class defines in its body, by member name,
    translated and written as inline definitions after the class: `virtual` and
    default arguments go, and the name gains the class."""
    text = translate(original, "ox")
    tree = Parser(CPP).parse(text.encode())
    found, stack = {}, [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("class_specifier", "struct_specifier"):
            name = node.child_by_field_name("name")
            if name is not None and name.text.decode() == irrlicht_cls and node.child_by_field_name("body") is not None:
                for member in node.child_by_field_name("body").named_children:
                    declared = _declared_name(member) if member.type == "function_definition" else None
                    if declared is None:
                        continue
                    source = text.encode()
                    # cut default arguments, then qualify the name; later edits first
                    edits = [(declared.start_byte, declared.start_byte, f"{cls}::".encode())]
                    stack2 = [member]
                    while stack2:
                        inner = stack2.pop()
                        if inner.type == "optional_parameter_declaration":
                            value = inner.child_by_field_name("default_value")
                            equals = next(c for c in inner.children if c.type == "=")
                            edits.append((equals.start_byte, value.end_byte, b""))
                        elif inner.type != "compound_statement":
                            stack2.extend(inner.children)
                    body = source[member.start_byte : member.end_byte]
                    for start, end, replacement in sorted(edits, reverse=True):
                        body = body[: start - member.start_byte] + replacement + body[end - member.start_byte :]
                    body = re.sub(r"^\s*virtual\s+", "", body.decode())
                    body = re.sub(r"[ \t]+(?=[,)])", "", body.split("{", 1)[0]) + "{" + body.split("{", 1)[1]
                    lines = body.replace("\t", "    ").split("\n")
                    indent = min((len(line) - len(line.lstrip()) for line in lines[1:] if line.strip()), default=0)
                    key = declared.text.decode().replace(irrlicht_cls, cls) if declared.text.decode().lstrip("~") == irrlicht_cls else declared.text.decode()
                    found[key] = "inline " + "\n".join([lines[0].strip(), *(line[indent:] for line in lines[1:])])
                return found
        stack.extend(reversed(node.children))
    return found


def _member_calls(text: str) -> str:
    def call(match: re.Match) -> str:
        member = MEMBERS.get(match["name"], match["name"])
        return f"{match['object']}.{member}({match['rest'] or ''}"

    return re.sub(r"\bdaisy::\w+::(?P<name>\w+)\((?P<object>\w+)(?:,\s*(?P<rest>(?=\S)))?", call, text)


def unguard(header: str, bodies: list[str], includes: tuple[str, ...] = ()) -> str:
    """The header exposed, with the inline definitions added after its namespaces
    close, in a block of the same namespaces that first includes the headers the
    bodies need (after the class, since such a header may include this one)."""
    text = expose(header)
    if not bodies:
        return text
    close = re.search(r"\n\} // end namespace (\w+)\n\} // end namespace (\w+)\n+(?=#endif\s*$)", text)
    if close is None:
        raise ValueError("the header does not end with two commented closing namespaces and its include guard")
    inner, outer = close[1], close[2]
    block = (
        f"#ifndef {GUARD}\n// Inline in the original, as in Irrlicht 0.7; the port defines these out of line.\n"
        + "".join(f'#include "{name}"\n' for name in includes if f'"{name}"' not in text)
        + f"\nnamespace {outer} {{\nnamespace {inner} {{\n\n"
        + "\n\n".join(bodies)
        + f"\n\n}} // end namespace {inner}\n}} // end namespace {outer}\n#endif\n\n"
    )
    return text[: close.end()] + block + text[close.end() :]
