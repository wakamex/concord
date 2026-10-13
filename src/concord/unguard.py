"""Give Harvest's matching build what one of its headers keeps for the port.

Harvest's port (port/) reimplements parts of daisy that have no recovered source
yet. Where that needed a class's fields, constructor or helper members, they sit
in the ox header behind `#ifdef HARVEST_PORT`, with a byte array standing in for
the fields in the matching build, and the member functions Irrlicht defines
inline live out of line in a port source file. A daisy file seeded from Irrlicht
needs the same fields and members in the matching build.

`unguard` rewrites such a header: the declarations guarded for the port become
unconditional (the stand-in of an `#else` branch goes), and each member function
gets its body inside the class, where Irrlicht has it. The place matters. A
virtual member declared in the class and defined after it, even as `inline`, is
the class's key function, and GCC then emits the vtable and every inline virtual
in each unit that includes the header; defined in the class, the class has no
key function and they are emitted only where used, as in the original.

Bodies come from the port source, which has daisy's changes, or from Irrlicht's
class, whichever the caller chooses per member. The port source calls free
functions where Irrlicht calls members the recovered ox types lacked
(`daisy::scene::transformBox(matrix, box)` for `matrix.transformBox(box)`); the
bodies call the members, which concord.complete adds from Irrlicht. With the
bodies in the header, the port drops its out-of-line copies.

Not every HARVEST_PORT block is recovered original code (ox/game/CGameMain.h
guards additions of the port's own), so unguard is applied header by header.
"""

from __future__ import annotations

import re

from tree_sitter import Node, Parser

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
    """What follows the declarator (initializer list and body) of each member
    function of `cls` the source defines out of line, by member name, calling
    members where the source calls the port's free functions."""
    tree = Parser(CPP).parse(source.encode())
    found, stack = {}, [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            declarator = _function_declarator(node)
            name = declarator.child_by_field_name("declarator") if declarator is not None else None
            if name is not None and name.text.decode().startswith(f"{cls}::"):
                tail = source.encode()[declarator.end_byte : node.end_byte].decode()
                found[name.text.decode().removeprefix(f"{cls}::")] = _member_calls(tail)
            continue
        stack.extend(reversed(node.children))
    return found


def irrlicht_definitions(original: str, cls: str) -> dict[str, str]:
    """The same for the member functions Irrlicht's class defines in its body,
    translated into ox's vocabulary."""
    text = translate(original, "ox")
    tree = Parser(CPP).parse(text.encode())
    found, stack = {}, [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("class_specifier", "struct_specifier"):
            name = node.child_by_field_name("name")
            if name is not None and name.text.decode() == cls and node.child_by_field_name("body") is not None:
                for member in node.child_by_field_name("body").named_children:
                    declarator = _function_declarator(member) if member.type == "function_definition" else None
                    declared = declarator.child_by_field_name("declarator") if declarator is not None else None
                    if declared is not None:
                        found[declared.text.decode()] = text.encode()[declarator.end_byte : member.end_byte].decode()
                return found
        stack.extend(reversed(node.children))
    return found


def _function_declarator(node: Node) -> Node | None:
    declarator = node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    return declarator


def _member_calls(text: str) -> str:
    def call(match: re.Match) -> str:
        member = MEMBERS.get(match["name"], match["name"])
        return f"{match['object']}.{member}({match['rest'] or ''}"

    return re.sub(r"\bdaisy::\w+::(?P<name>\w+)\((?P<object>\w+)(?:,\s*(?P<rest>(?=\S)))?", call, text)


def _declaration(text: str, name: str) -> re.Match | None:
    """The member's declaration in the class: its indentation, and everything up to
    the semicolon."""
    pattern = (
        r"^([ \t]*)((?:virtual[ \t]+)?[^;{}#\n]*?(?<![\w~:])"
        + re.escape(name)
        + r"[ \t]*\((?:[^;{}])*?\)[ \t]*(?:const[ \t]*)?);"
    )
    return re.search(pattern, text, re.MULTILINE)


def _placed(tail: str, indent: str) -> str:
    """The initializer list and body at the declaration's indentation."""
    tail = tail.replace("\t", "    ")
    if not tail.lstrip(" ").startswith("\n"):
        return " " + tail.strip()  # on the declaration's line: { return ID; }
    lines = tail.strip("\n").split("\n")
    common = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    return "\n" + "\n".join(indent + line[common:] if line.strip() else "" for line in lines)


def unguard(header: str, bodies: dict[str, str] | None = None, includes: tuple[str, ...] = ()) -> str:
    """The header exposed, each member in `bodies` (name -> initializer list and
    body) defined where the class declares it, and the headers those bodies need
    included."""
    text = expose(header)
    # find every declaration before placing any body, since a body's statements can look like one
    found = [(match, tail) for name, tail in (bodies or {}).items() if (match := _declaration(text, name))]
    for match, tail in sorted(found, key=lambda item: -item[0].start()):
        text = text[: match.start()] + match[1] + match[2] + _placed(tail, match[1]) + text[match.end() :]
    added = "".join(
        f'#include "{name}"\n'
        for name in includes
        if not re.search(r'#include "[^"]*' + re.escape(name.split("/")[-1]) + '"', text)
    )
    last = None
    for last in re.finditer(r"^#include [^\n]*\n", text, re.MULTILINE):
        pass
    if added and last is not None:
        text = text[: last.end()] + added + text[last.end() :]
    return text
