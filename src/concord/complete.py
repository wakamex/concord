"""Complete Harvest's partial ox core types with Irrlicht's members.

Harvest's ox::core and ox::video types are adapted from Irrlicht 0.7 headers and
declare only what recovered units use so far ("Partial" in their notices).
daisy's own copies had Irrlicht's full set, so when a seeded file calls a member
Harvest's header lacks, such as CAabbox3d::reset, the member is Irrlicht's.
`complete` copies every overload of that member from Irrlicht's class into
Harvest's, translated. Every added member also shifts GCC 4.4's declaration
numbering in each unit that includes the header, which changes the code of the
functions concord phase lists as witnesses there, so seeding completes headers
only when asked, and `settle` then checks every unit that includes a changed
header: when a function there stops matching or scores lower, it adds more of
the class's Irrlicht members, one at a time, until the count suits those units
again, and reports failure when none does, so the caller can restore the header.

std::vector-based containers (ox::TArray, ox::TList) are left alone: daisy
replaced Irrlicht's containers, so Irrlicht's members are not theirs.
"""

from __future__ import annotations

import re
from pathlib import Path

from tree_sitter import Node, Parser

from concord.harvest import Harvest
from concord.irrlicht import bare, translate
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


NO_CONSTRUCTOR = re.compile(r"no matching function for call to 'ox::(?:core|video)::(\w+)(?:<[^']*?>)?::\1\(")


def missing_members(compiler_output: str) -> set[tuple[str, str]]:
    """(ox type, member) pairs the compiler reports missing, for types this module
    completes. A constructor call that matches no overload names the type's
    Irrlicht class as the member."""
    missing = {(t, m) for t, m in MISSING.findall(compiler_output) if t in TYPES}
    return missing | {(t, TYPES[t][2]) for t in NO_CONSTRUCTOR.findall(compiler_output) if t in TYPES}


def complete(root: Path, missing: set[tuple[str, str]]) -> list[str]:
    """Add each missing member's Irrlicht overloads to Harvest's header; return the
    headers changed."""
    changed = []
    for ox_type, member in sorted(missing):
        header, irrlicht_header, irrlicht_class = TYPES[ox_type]
        path = root / "src" / header
        original = (root / "third_party" / "irrlicht-0.7" / "include" / irrlicht_header).read_text(encoding="latin-1")
        definitions = _members(original, irrlicht_class, member)
        declarations, outside = [], []
        if not definitions:
            # declared in the class and defined after it, as matrix4's members are
            declarations = _declared(original, irrlicht_class, member)
            outside = _defined_outside(original, irrlicht_class, member)
            if not declarations or not outside:
                continue
        text = path.read_text()
        # an overload the header already has, such as one of several constructors, is not added again
        squeezed = "".join(text.split())
        definitions = [d for d in definitions if _signature(_rename(d, "::".join(header.split("/")[:-1]) + "::")) not in squeezed]
        if not definitions and not declarations:
            continue
        namespace = "::".join(header.split("/")[:-1]) + "::"  # the header's own: ox::core::
        added = "".join(_indent(_rename(d, namespace)) + "\n\n" for d in definitions)
        added += "".join("    " + " ".join(_rename(d, namespace).split()) + "\n" for d in declarations)
        public = re.search(r"^(class|struct) " + ox_type + r"\b[^{]*\{\s*\n(\s*public:\s*\n)?", text, re.MULTILINE)
        close = re.search(r"\n\}[^\n]*\n\}[^\n]*\n+#endif\s*$", text)
        if public is None or (outside and close is None):
            continue
        if outside:
            bodies = "".join("\n" + _outdent(_rename(d, namespace)) + "\n" for d in outside)
            text = text[: close.start()] + bodies + text[close.start() :]
        text = text[: public.end()] + added + text[public.end() :]
        path.write_text(text)
        changed.append(header)
    return sorted(set(changed))


def absent(root: Path, ox_type: str) -> list[str]:
    """Members of Irrlicht's class that Harvest's lacks, in Irrlicht's order."""
    header, irrlicht_header, irrlicht_class = TYPES[ox_type]
    original = (root / "third_party" / "irrlicht-0.7" / "include" / irrlicht_header).read_text(encoding="latin-1")
    text = (root / "src" / header).read_text()
    names = [_name(node) for node in _class_members(original, irrlicht_class, declarations=True)]
    return [
        name
        for name in dict.fromkeys(names)
        if name and name != irrlicht_class and not re.search(_spelled(name), text)
    ]


def _spelled(name: str) -> str:
    """A pattern for the member's definition or declaration, however an operator is spaced."""
    if name.startswith("operator"):
        return r"\boperator\s*" + re.escape(name.removeprefix("operator").strip()) + r"\s*\("
    return r"(?<![\w~])" + re.escape(name) + r"\s*\("


State = dict[tuple[str, str], tuple[bool, float]]  # (unit, symbol) -> (exact, objdiff score)


def state(harvest: Harvest, units: list[str], overlays: dict[str, bytes] | None = None) -> State:
    """Every function of the units as the checkout compiles them now, or with the
    given files (path under the checkout -> contents) in place of its own. A unit
    that does not compile contributes nothing."""
    items = {f"u{n}": (unit, overlays or {}) for n, unit in enumerate(units)}
    found = {}
    for name, evaluation in harvest.evaluate_overlays(items).items():
        for section in evaluation.sections:
            for row in section.get("functions", []):
                found[(items[name][0], row["symbol"])] = (row["exact"], evaluation.fuzzy.get(row["symbol"], 0.0))
    return found


def worse(before: State, after: State) -> set[str]:
    """Units with a function that stopped matching, scores lower or is gone."""
    return {
        key[0]
        for key, (exact, score) in before.items()
        if key not in after or (exact and not after[key][0]) or after[key][1] < score
    }


FILL = 30  # more members than this cannot reach a count the first 30 did not


def settle(harvest: Harvest, originals: dict[str, str], allowed: frozenset[str] = frozenset()) -> bool:
    """Whether the units including the changed headers (header under src/ -> its
    text before the change) are no worse than before, after adding further
    Irrlicht members to those headers if that is what it takes. Functions whose
    symbols are in `allowed` may get worse: a trade the caller made knowingly."""
    root = harvest.root
    units = sorted({unit for header in originals for unit in harvest.dependents(f"src/{header}")})
    if not units:
        return True
    before = state(harvest, units, {f"src/{h}": text.encode() for h, text in originals.items()})
    before = {key: value for key, value in before.items() if key[1] not in allowed}
    broken = worse(before, state(harvest, units))
    if not broken:
        return True
    narrow = {key: value for key, value in before.items() if key[0] in broken}
    for header in originals:
        ox_type = next(t for t, (h, _, _) in TYPES.items() if h == header)
        for member in absent(root, ox_type)[:FILL]:
            previous = (root / "src" / header).read_text()
            if not complete(root, {(ox_type, member)}):
                continue
            after = state(harvest, sorted(broken))
            if {key[0] for key in narrow} - {key[0] for key in after}:
                (root / "src" / header).write_text(previous)  # the member does not compile here
                continue
            if not worse(narrow, after) and not worse(before, state(harvest, units)):
                return True
    return False


def _class_members(source: str, cls: str, declarations: bool = False) -> list[Node]:
    """The member functions the class defines in its body, and with `declarations`
    also those it only declares there."""
    kinds = ("function_definition", "field_declaration", "declaration") if declarations else ("function_definition",)
    # Irrlicht qualifies one declaration inside its class (matrix4::getTranslation),
    # which is not standard C++; read it as the plain declaration it stands for
    source = re.sub(r"\b" + cls + r"::(?=\w+\s*\()", "", source)
    tree = Parser(CPP).parse(source.encode("latin-1"))
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("class_specifier", "struct_specifier"):
            name = node.child_by_field_name("name")
            if name is not None and name.text.decode() == cls and node.child_by_field_name("body") is not None:
                return [c for c in node.child_by_field_name("body").named_children if c.type in kinds and _name(c)]
        stack.extend(node.children)
    return []


def _declared(source: str, cls: str, member: str) -> list[str]:
    return [
        node.text.decode("latin-1")
        for node in _class_members(source, cls, declarations=True)
        if node.type != "function_definition" and _name(node) == member
    ]


def _defined_outside(source: str, cls: str, member: str) -> list[str]:
    """Definitions of cls::member written after the class."""
    tree = Parser(CPP).parse(source.encode("latin-1"))
    found, stack = [], [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            if _name(node) == f"{cls}::{member}":
                found.append(node.text.decode("latin-1"))
            continue
        stack.extend(reversed(node.children))
    return found


def _name(node: Node) -> str | None:
    declarator = node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    if declarator is None:
        return None
    name = declarator.child_by_field_name("declarator")
    return "".join(name.text.decode().split()) if name is not None else None


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
    return node.type == "function_definition" and _name(node) == member


def _rename(definition: str, namespace: str = "") -> str:
    """Translate a member body written inside Irrlicht's core namespace, where the
    core templates are named bare (vector3d<T>) rather than core::vector3d<T>.
    `namespace` (ox::core::) is the destination's own and is left off its names."""
    text = bare(translate(definition))
    for ox_type, (_, _, irrlicht_class) in TYPES.items():
        text = re.sub(r"(?<![\w:])" + irrlicht_class + r"\b", ox_type, text)
    return text.replace(namespace, "") if namespace else text


def _signature(definition: str) -> str:
    """The definition up to its body or initializer list, without whitespace."""
    return "".join(re.split(r"\)\s*(?:const\s*)?[:{]", definition, maxsplit=1)[0].split())


def _outdent(definition: str) -> str:
    lines = definition.replace("\t", "    ").split("\n")
    common = min((len(line) - len(line.lstrip()) for line in lines[1:] if line.strip()), default=0)
    return "\n".join([lines[0].strip(), *(line[common:] for line in lines[1:])])


def _indent(definition: str) -> str:
    lines = definition.replace("\t", "    ").split("\n")
    first, rest = lines[0].strip(), lines[1:]
    common = min((len(line) - len(line.lstrip()) for line in rest if line.strip()), default=0)
    body = ["    " + line[common:] if line.strip() else "" for line in rest]
    return "\n".join(["    " + first, *body])
