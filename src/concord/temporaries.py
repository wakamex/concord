"""Write the temporaries a search introduced the way a person would.

The named-temporary rewrite declares `__typeof__(E) concordTmpN = E;`, which keeps
it type-correct without type inference but is not source anyone would submit.
Once a search has finished, the result is compiled with -g, each temporary's type
is read from its DWARF variable entry, and it is named after the expression it
holds (`Items[i].Text` becomes `text`, `getScreenRes()` becomes `screenRes`).
concord.finish keeps the rewritten source only if the function compiles to the
same bytes.
"""

from __future__ import annotations

import re
from pathlib import Path

from elftools.elf.elffile import ELFFile
from tree_sitter import Node, Parser

from concord.transforms import CPP

DECLARATION = re.compile(rb"__typeof__\((?P<expr>.*?)\) (?P<name>concordTmp\d+) = (?P=expr);")
KEYWORDS = {"default", "delete", "new", "this", "class", "operator", "template", "int", "char", "float"}


def rename(source: bytes, types: dict[str, str], direct: bool = True, scope: bytes | None = None) -> bytes | None:
    """Replace each `__typeof__` declaration with its type and a name taken from its
    expression, and rename the temporary's uses. With `direct`, a conversion to the
    declared type is written as direct initialization: `CString<char> name(text);`.
    A name is new to `scope` (the function's text; the whole source by default)."""
    taken = set(re.findall(rb"\b[A-Za-z_]\w*\b", source if scope is None else scope))
    for match in list(DECLARATION.finditer(source)):
        name = match["name"].decode()
        if name not in types:
            return None
        chosen = _fresh(_name_for(match["expr"]), taken)
        taken.add(chosen.encode())
        declared = f"{types[name]} {chosen} = ".encode() + match["expr"] + b";"
        node = _expression(match["expr"])
        if direct and node.type == "call_expression":
            function = node.child_by_field_name("function").text.replace(b" ", b"")
            arguments = node.child_by_field_name("arguments")
            if function == types[name].replace(" ", "").encode():
                declared = f"{types[name]} {chosen}".encode() + arguments.text + b";"
        source = source.replace(match.group(0), declared, 1)
        source = re.sub(rb"\b" + match["name"] + rb"\b", chosen.encode(), source)
    return source


def variable_types(obj: Path) -> dict[str, str]:
    """C++ type names of the object's concordTmp variables, from their DWARF entries."""
    found = {}
    with open(obj, "rb") as stream:
        dwarf = ELFFile(stream).get_dwarf_info()
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                name = die.attributes.get("DW_AT_name")
                variable = die.tag == "DW_TAG_variable" and name and name.value.startswith(b"concordTmp")
                if variable and "DW_AT_type" in die.attributes:
                    found[name.value.decode()] = type_name(die.get_DIE_from_attribute("DW_AT_type"))
    return found


def type_name(die) -> str:
    tag = die.tag
    inner = die.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in die.attributes else None
    if tag == "DW_TAG_pointer_type":
        return (type_name(inner) if inner is not None else "void") + "*"
    if tag in ("DW_TAG_reference_type", "DW_TAG_rvalue_reference_type"):
        return type_name(inner) + "&"
    if tag in ("DW_TAG_const_type", "DW_TAG_volatile_type"):
        word = "const" if tag == "DW_TAG_const_type" else "volatile"
        target = type_name(inner) if inner is not None else "void"
        return f"{target} {word}" if target.endswith("*") else f"{word} {target}"
    for link in ("DW_AT_specification", "DW_AT_abstract_origin"):
        if "DW_AT_name" not in die.attributes and link in die.attributes:
            return type_name(die.get_DIE_from_attribute(link))
    name = die.attributes["DW_AT_name"].value.decode()
    if tag == "DW_TAG_base_type":
        return name
    scopes = []
    parent = die.get_parent()
    while parent is not None and parent.tag in ("DW_TAG_namespace", "DW_TAG_class_type", "DW_TAG_structure_type"):
        if "DW_AT_name" in parent.attributes:
            scopes.append(parent.attributes["DW_AT_name"].value.decode())
        parent = parent.get_parent()
    return "::".join([*reversed(scopes), name])


def _expression(text: bytes) -> Node:
    tree = Parser(CPP).parse(b"void f() { (" + text + b"); }")
    statement = tree.root_node.child(0).child_by_field_name("body").named_children[0]
    return statement.named_children[0].named_children[0]


def _name_for(text: bytes) -> str:
    node = _expression(text)
    while True:
        if node.type == "field_expression":
            word = node.child_by_field_name("field").text.decode()
            break
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments").named_children
            if b"<" in function.text and len(arguments) == 1:
                # a conversion such as CString<char>(extension): extensionString
                kind = re.sub(r"<.*", "", function.text.decode()).split("::")[-1]
                kind = kind[1:] if re.match(r"[A-Z][A-Z][a-z]", kind) else kind
                return _name_for(arguments[0].text) + kind[:1].upper() + kind[1:]
            node = function
            continue
        if node.type in ("subscript_expression", "pointer_expression", "parenthesized_expression", "cast_expression"):
            node = node.child_by_field_name("argument") or node.child_by_field_name("value") or node.named_children[-1]
            continue
        if node.type in ("identifier", "qualified_identifier"):
            word = node.text.decode().split("::")[-1]
            break
        word = "value"
        break
    word = re.sub(r"^(get|is)(?=[A-Z])", "", word)
    return word[:1].lower() + word[1:] or "value"


def _fresh(word: str, taken: set[bytes]) -> str:
    candidate, n = word, 2
    while candidate.encode() in taken or candidate in KEYWORDS:
        candidate, n = f"{word}{n}", n + 1
    return candidate
