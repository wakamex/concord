"""Why each stubbed method still has a stub, grouped by cause.

`seed --stub` gives a method whose Irrlicht body does not compile a body that
traps. For each such stub in the checkout, `stubs` puts Irrlicht's translated
body back into the unit as it stands (every other function, and every header,
unchanged), compiles all of a unit's candidates in one batch, and keeps the
compiler errors that fall inside the method. `causes` reduces each error to a
cause that names the type or member it is about, so that the stubs sharing a
blocker can be counted and the blocker that frees the most stubs fixed first.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from concord.harvest import Harvest
from concord.irrlicht import HELPERS, helper_names, name_helpers, translate
from concord.seed import IRRLICHT, companion_header_first, helper_taken, qualify
from concord.transforms import CPP


@dataclass
class Stub:
    unit: str
    name: str  # the method's declarator name, as Class::method
    errors: list[str] = field(default_factory=list)  # compiler messages inside the restored body
    applied: bool = False

    @property
    def causes(self) -> set[str]:
        return {cause(message) for message in self.errors}


def _definitions(source: bytes) -> dict[str, Node]:
    tree = Parser(CPP).parse(source)
    groups, stack = {}, [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            while declarator is not None and declarator.type != "function_declarator":
                declarator = declarator.child_by_field_name("declarator") or next(
                    (c for c in declarator.named_children if c.type.endswith("declarator")), None
                )
            name = declarator.child_by_field_name("declarator") if declarator is not None else None
            if name is not None:
                groups.setdefault("".join(name.text.decode().split()), []).append((node, declarator))
            continue
        stack.extend(node.children)
    found = {}
    for name, definitions in groups.items():
        for node, declarator in definitions:
            suffix = "" if len(definitions) == 1 else "".join(declarator.text.decode().split())[len(name):]
            found[name + suffix] = node
    return found


INCLUDE = re.compile(rb'^#include [<"]([^>"]+)[>"][^\n]*\n', re.MULTILINE)


def _missing_includes(current: bytes, irrlicht: bytes) -> bytes:
    """The #include lines of the qualified translation that the unit lacks, by file
    name: a relocated include (daisy/os.h for Irrlicht's os.h) satisfies it, so what
    is added are the headers qualify picked for what the translated bodies call:
    ox/algo/CArrayFunctions.h for the ox::algo searches a restored body makes."""
    have = {line.group(1).rsplit(b"/", 1)[-1] for line in INCLUDE.finditer(current)}
    added = b""
    for line in INCLUDE.finditer(irrlicht):
        name = line.group(1).rsplit(b"/", 1)[-1]
        if name not in have:
            added += line.group(0)
            have.add(name)
    return added


def _helper_definition(text: bytes, name: str) -> bytes | None:
    """The one file-local inline definition of `name` in `text`, or None."""
    helper = re.compile(
        rb"^inline\b[^\n]*?\b" + re.escape(name.encode()) + rb"\s*\([^;{]*\)\s*\{.*?^\}",
        re.MULTILINE | re.DOTALL,
    )
    found = helper.findall(text)
    return found[0] if len(found) == 1 else None


def _missing_helpers(current: bytes, irrlicht: bytes, names: set[str]) -> bytes:
    """The file-local helpers translate wrote into the translation for members a
    shared ox header cannot add (CTriangle3d's getIntersectionWithLine), which a
    restored body also calls. `names` is the helpers' names in the translation."""
    if not names:
        return b""
    helper = re.compile(
        rb"^inline\b[^\n]*?\b(?:" + b"|".join(re.escape(name.encode()) for name in sorted(names)) + rb")\s*\([^;{]*\)\s*\{.*?^\}",
        re.MULTILINE | re.DOTALL,
    )
    return b"".join(
        found.group(0) + b"\n\n" for found in helper.finditer(irrlicht) if found.group(0) not in current
    )


def _reused_helpers(current: bytes, translation: bytes, names: dict[str, str]) -> tuple[bytes, dict[str, str]]:
    """The translation with each helper the unit already defines under its
    plain name pointed back at that definition, and the marker map updated.
    The unit's own helper names count as taken, so a refresh of a unit whose
    earlier restore already placed getIntersectionWithLine would carry a
    numbered copy and call it, doubling both declarations. When the helper's
    definition under the plain name is byte-identical to the unit's, the
    translation calls the existing helper and carries nothing; a different
    definition under the plain name keeps the numbered one. Sibling helpers
    settle by repetition: renaming isOnSameSide first lets
    getIntersectionWithLine's body then match the unit's."""
    names = dict(names)
    changed = True
    while changed:
        changed = False
        for marker, name in names.items():
            plain = HELPERS.get(marker, marker.removeprefix("_concord_"))
            if name == plain:
                continue
            definition = _helper_definition(translation, name)
            if definition is None:
                continue
            candidate = re.sub(rb"\b" + re.escape(name.encode()) + rb"\b", plain.encode(), definition)
            if candidate in current:
                translation = re.sub(rb"\b" + re.escape(name.encode()) + rb"\b", plain.encode(), translation)
                names[marker] = plain
                changed = True
    return translation, names


def _with_includes(text: bytes, includes: bytes, helpers: bytes = b"") -> bytes:
    """The unit with `includes` added after its first #include line and the
    `helpers` (file-local inline functions) placed at global scope before its
    first namespace opens."""
    if includes:
        first = INCLUDE.search(text)
        text = text[: first.end()] + includes + text[first.end() :] if first else includes + text
    if helpers:
        opening = re.search(rb"^namespace ", text, re.MULTILINE)
        text = text[: opening.start()] + helpers + text[opening.start() :] if opening else text + helpers
    return text


def restored(current: bytes, irrlicht: bytes, helpers: set[str] = frozenset(), *, stubs_only: bool = True,
             carry_includes: bool = True) -> dict[str, bytes]:
    """For each function of `current` whose body is a stub, the unit with that one
    body replaced by the body of the function of the same name in `irrlicht` (the
    qualified translation), plus the includes the translation has that the unit
    lacks. With `stubs_only=False`, existing bodies are candidates too; callers
    must explicitly select the methods to refresh. `helpers` is the names of the file-local helpers the translation
    defines; the ones the unit lacks are carried too."""
    ours, theirs = _definitions(current), _definitions(irrlicht)
    includes = _missing_includes(current, irrlicht) if carry_includes else b""
    helper_text = _missing_helpers(current, irrlicht, helpers)
    out = {}
    for name, node in ours.items():
        body = node.child_by_field_name("body")
        original = theirs.get(name)
        if body is None or (stubs_only and b"__builtin_trap();" not in body.text) or original is None:
            continue
        start = node.child_by_field_name("declarator").end_byte
        original_start = original.child_by_field_name("declarator").end_byte
        out[name] = _with_includes(
            current[:start] + irrlicht[original_start : original.end_byte] + current[body.end_byte :],
            includes,
            helper_text,
        )
    return out


ERROR = re.compile(r"^src/([^:\s]+):(\d+): error: (.+)$", re.MULTILINE)


def errors_in(output: str, unit: str, text: bytes, name: str) -> list[str]:
    """The compiler's error messages for lines of the unit inside the named function."""
    node = _definitions(text).get(name)
    if node is None:
        return []
    first, last = node.start_point[0] + 1, node.end_point[0] + 1
    return [message for path, line, message in ERROR.findall(output) if path == unit and first <= int(line) <= last]


MEMBER = re.compile(r"'(?:const )?(?:class|struct) ([\w:]+?)(?:<[^']*>)?' has no member named '(\w+)'")
UNDECLARED = re.compile(r"'([\w:]+)' (?:was not declared in this scope|has not been declared|does not name a type|is not a member of '([\w:]+)')")
NO_MATCH = re.compile(r"no matching function for call to '([\w:]+?)(?:<[^']*>)?(?:::(\w+))?\(")
INCOMPLETE = re.compile(r"invalid use of incomplete type '(?:const )?(?:class|struct) ([\w:]+)")
CONVERSION = re.compile(r"cannot convert '([^']+)' to '([^']+)'")


def cause(message: str) -> str:
    """A message reduced to the type or member it is about."""
    if found := MEMBER.search(message):
        return f"{found[1]} lacks {found[2]}"
    if found := INCOMPLETE.search(message):
        return f"{found[1]} is incomplete"
    if found := NO_MATCH.search(message):
        return f"no overload of {found[1]}" + (f"::{found[2]}" if found[2] else "")
    if found := UNDECLARED.search(message):
        return f"{found[1]} undeclared" + (f" in {found[2]}" if found[2] else "")
    if found := CONVERSION.search(message):
        return f"cannot convert {found[1]} to {found[2]}"
    return re.sub(r"'[^']*'", "X", message)[:80]


def stubs(harvest: Harvest, units: list[str], apply: bool = False, only: set[str] | None = None, *, refresh: bool = False, companion_first: bool = False) -> list[Stub]:
    """Diagnose each restored body, optionally writing the compiling ones in
    place after their combined source also compiles. No headers are changed."""
    if refresh and not only:
        raise ValueError("refresh requires explicit method names")
    root = harvest.root
    found = []
    for unit in units:
        path = root / "src" / unit
        irrlicht = root / IRRLICHT / "source" / "Irrlicht" / Path(unit).name
        current = path.read_bytes()
        if (not refresh and b"__builtin_trap();" not in current) or not irrlicht.exists():
            continue
        raw = translate(irrlicht.read_text(encoding="latin-1"))
        taken = helper_taken(root, raw, unit)
        translation, helper_map = _reused_helpers(
            current, qualify(root, name_helpers(raw, taken), unit).encode(), helper_names(raw, taken)
        )
        helpers = set(helper_map.values())
        candidates = restored(current, translation, helpers, stubs_only=not refresh)
        if companion_first:
            candidates = {name: companion_header_first(text.decode(), unit).encode() for name, text in candidates.items()}
        if only is not None:
            candidates = {name: text for name, text in candidates.items() if name in only}
        if not candidates:
            continue
        names = {f"s{n}": name for n, name in enumerate(candidates)}
        evaluations = harvest.evaluate(unit, {key: candidates[name] for key, name in names.items()})
        accepted = []
        for key, name in names.items():
            error = evaluations[key].error
            messages = errors_in(error, unit, candidates[name], name) or ([error.strip()] if error else [])
            stub = Stub(unit, name, messages)
            found.append(stub)
            if not error:
                accepted.append(stub)
        if apply and accepted:
            definitions = _definitions(current)
            edits = []
            for stub in accepted:
                node = definitions[stub.name]
                replacement = _definitions(candidates[stub.name])[stub.name]
                start = node.child_by_field_name("declarator").end_byte
                stop = node.child_by_field_name("body").end_byte
                replacement_start = replacement.child_by_field_name("declarator").end_byte
                edits.append((start, stop, candidates[stub.name][replacement_start:replacement.end_byte]))
            combined = current
            for start, stop, body in sorted(edits, reverse=True):
                combined = combined[:start] + body + combined[stop:]
            combined = _with_includes(
                combined,
                _missing_includes(current, translation),
                _missing_helpers(current, translation, helpers),
            )
            if companion_first:
                combined = companion_header_first(combined.decode(), unit).encode()
            verification = harvest.evaluate(unit, {"combined": combined})["combined"]
            if verification.error:
                raise RuntimeError(f"{unit}: combined restored bodies do not compile:\n{verification.error}")
            if path.read_bytes() != current:
                raise RuntimeError(f"{unit}: source changed while compiling restored bodies")
            path.write_bytes(combined)
            for stub in accepted:
                stub.applied = True
    return found


def ranked(found: list[Stub]) -> list[tuple[str, int, int]]:
    """(cause, stubs it blocks, stubs it alone blocks), most stubs first."""
    every = Counter(c for stub in found for c in stub.causes)
    alone = Counter(next(iter(stub.causes)) for stub in found if len(stub.causes) == 1)
    return sorted(((c, n, alone[c]) for c, n in every.items()), key=lambda row: (-row[1], -row[2], row[0]))



def refresh_header(harvest: Harvest, unit: str, header: str, methods: set[str], apply: bool = False) -> list[Stub]:
    """Restore selected inline bodies from the ancestor, compiling the including
    unit with a header overlay before writing. Whole-build scoring is separate.
    """
    if not methods:
        raise ValueError("refresh requires explicit method names")
    root = harvest.root
    path = root / "src" / header
    original = root / IRRLICHT / "source" / "Irrlicht" / Path(header).name
    if not original.exists():
        original = root / IRRLICHT / "include" / Path(header).name
    current = path.read_bytes()
    translation = qualify(root, translate(original.read_text(encoding="latin-1")), header).encode()
    combined = current
    found = []
    for name in sorted(methods):
        candidate = restored(combined, translation, stubs_only=False, carry_includes=False).get(name)
        if candidate is None:
            continue
        evaluation = harvest.evaluate_overlays({"header": (unit, {"src/" + header: candidate})})["header"]
        if evaluation.error:
            with_includes = restored(combined, translation, stubs_only=False)[name]
            if with_includes != candidate:
                candidate = with_includes
                evaluation = harvest.evaluate_overlays({"header": (unit, {"src/" + header: candidate})})["header"]
        row = Stub(unit, name, [evaluation.error] if evaluation.error else [])
        found.append(row)
        if not evaluation.error:
            combined = candidate
    if apply and combined != current:
        if path.read_bytes() != current:
            raise RuntimeError(f"{header}: source changed while compiling inline bodies")
        path.write_bytes(combined)
        for row in found:
            row.applied = not row.errors
    return found
