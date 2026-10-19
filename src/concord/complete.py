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

import os
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


UNDECLARED = re.compile(r"'(\w+)' (?:does not name a type|was not declared in this scope|has not been declared)")
ORIGIN = re.compile(r"^// Adapted from Irrlicht 0\.7 include/(\S+\.h)", re.MULTILINE)


def undeclared(compiler_output: str) -> set[str]:
    """Names the compiler reports undeclared."""
    return set(UNDECLARED.findall(compiler_output))


def complete_enum(root: Path, name: str) -> str | None:
    """Add to the ox header adapted from an Irrlicht header the enum that Irrlicht
    header defines under `name`, or whose enumerator `name` is, translated, before
    the header's first class; return the header changed, or None when no ox header
    comes from an Irrlicht header with such an enum or it already has it."""
    for path in sorted((root / "src" / "ox").rglob("*.h")):
        text = path.read_text()
        origin = ORIGIN.search(text)  # a provisional header may lack the comment, and then shares its Irrlicht name
        original = root / "third_party" / "irrlicht-0.7" / "include" / (origin[1] if origin else path.name)
        if not original.exists():
            continue
        for found in re.finditer(r"^[ \t]*enum (\w+)\s*\{[^}]*\};", original.read_text(encoding="latin-1"), re.MULTILINE):
            enumerators = re.findall(r"^\s*(\w+)", found[0].split("{", 1)[1], re.MULTILINE)
            if name != found[1] and name not in enumerators:
                continue
            if any(re.search(r"\benum " + found[1] + r"\b", other.read_text()) for other in (root / "src" / "ox").rglob("*.h")):
                return None  # another ox header has it, as IVideoDriver.h has ECOLOR_FORMAT
            definition = _outdent(translate(found[0].strip("\n"))) + "\n\n"
            first = re.search(r"^(class|struct) \w+[^;]*$", text, re.MULTILINE) or re.search(r"^namespace \w+ \{\n(?!namespace)", text, re.MULTILINE)
            if first is None:
                return None
            at = first.start() if first[0].startswith(("class", "struct")) else first.end()
            path.write_text(text[:at] + definition + text[at:])
            return path.relative_to(root / "src").as_posix()
    return None


ENUM_BODY = re.compile(
    r"(?P<comment>(?:[ \t]*//[^\n]*\n)*)[ \t]*enum (?P<name>\w+)\s*\{(?P<body>[^}]*)\}\s*;[ \t]*\n(?:[ \t]*\n)?",
)


def shared_enums(root: Path) -> list[str]:
    """Make each enum an ox header defines only in one ox header. A header that
    keeps an enum with no enumerators, as a placeholder, gets an include of the
    header that defines it with enumerators, since a second definition does not
    compile. Returns the headers changed, under src/."""
    ox = sorted((root / "src" / "ox").rglob("*.h"))
    texts = {path: path.read_text() for path in ox}
    owners: dict[str, Path] = {}
    for path, text in texts.items():
        for found in ENUM_BODY.finditer(text):
            if re.search(r"\w", found["body"]):
                owners.setdefault(found["name"], path)
    changed = []
    for path, text in texts.items():
        updated = text
        includes = []
        for found in ENUM_BODY.finditer(text):
            owner = owners.get(found["name"])
            if owner is None or owner == path or re.search(r"\w", found["body"]):
                continue
            updated = updated.replace(found[0], "", 1)
            includes.append(f'#include "{Path(os.path.relpath(owner, path.parent)).as_posix()}"\n')
        if includes:  # outside the namespaces, after the last include the header has
            last = list(re.finditer(r'^#include "[^"]*"\n', updated, re.MULTILINE))
            at = last[-1].end() if last else 0
            updated = updated[:at] + "".join(includes) + updated[at:]
        if updated != text:
            path.write_text(updated)
            changed.append(path.relative_to(root / "src").as_posix())
    return changed


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
    return {key[0] for key in regressed(before, after)}


def regressed(before: State, after: State) -> set[tuple[str, str]]:
    """The (unit, symbol) functions that stopped matching, scores lower or are gone."""
    return {
        key
        for key, (exact, score) in before.items()
        if key not in after or (exact and not after[key][0]) or after[key][1] < score
    }


FILL = 30  # more members than this cannot reach a count the first 30 did not


def settle(harvest: Harvest, originals: dict[str, str], allowed: frozenset[str] = frozenset()) -> bool:
    """Whether regressions(...) finds nothing."""
    return not regressions(harvest, originals, allowed)


def regressions(harvest: Harvest, originals: dict[str, str], allowed: frozenset[str] = frozenset()) -> list[str]:
    """The functions ("unit symbol") that the change to the headers makes worse,
    after adding further Irrlicht members to those headers if that is what it
    takes; empty when the units including them are no worse than before. Header
    paths are under src/, and originals maps each to its text before the change.
    Functions whose symbols are in `allowed` may get worse: a trade the caller
    made knowingly."""
    root = harvest.root
    units = sorted({unit for header in originals for unit in harvest.dependents(f"src/{header}")})
    if not units:
        return []
    before = state(harvest, units, {f"src/{h}": text.encode() for h, text in originals.items()})
    before = {key: value for key, value in before.items() if key[1] not in allowed}
    first = regressed(before, state(harvest, units))
    if not first:
        return []
    broken = {unit for unit, _ in first}
    narrow = {key: value for key, value in before.items() if key[0] in broken}
    for header in originals:
        ox_type = next((t for t, (h, _, _) in TYPES.items() if h == header), None)
        if ox_type is None:
            continue  # a header completed with an enum has no class members to fill with
        for member in absent(root, ox_type)[:FILL]:
            previous = (root / "src" / header).read_text()
            if not complete(root, {(ox_type, member)}):
                continue
            after = state(harvest, sorted(broken))
            if {key[0] for key in narrow} - {key[0] for key in after}:
                (root / "src" / header).write_text(previous)  # the member does not compile here
                continue
            if not worse(narrow, after) and not worse(before, state(harvest, units)):
                return []
    return [f"{unit} {symbol}" for unit, symbol in sorted(first)]


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
