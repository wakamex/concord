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
from concord.irrlicht import translate
from concord.seed import IRRLICHT, qualify
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


def restored(current: bytes, irrlicht: bytes) -> dict[str, bytes]:
    """For each function of `current` whose body is a stub, the unit with that one
    body replaced by the body of the function of the same name in `irrlicht`."""
    ours, theirs = _definitions(current), _definitions(irrlicht)
    out = {}
    for name, node in ours.items():
        body = node.child_by_field_name("body")
        original = theirs.get(name)
        if body is None or b"__builtin_trap();" not in body.text or original is None:
            continue
        start = node.child_by_field_name("declarator").end_byte
        original_start = original.child_by_field_name("declarator").end_byte
        out[name] = current[:start] + irrlicht[original_start : original.end_byte] + current[body.end_byte :]
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


def stubs(harvest: Harvest, units: list[str], apply: bool = False) -> list[Stub]:
    """Diagnose each restored body, optionally writing the compiling ones in
    place after their combined source also compiles. No headers are changed."""
    root = harvest.root
    found = []
    for unit in units:
        path = root / "src" / unit
        irrlicht = root / IRRLICHT / "source" / "Irrlicht" / Path(unit).name
        current = path.read_bytes()
        if b"__builtin_trap();" not in current or not irrlicht.exists():
            continue
        translation = qualify(root, translate(irrlicht.read_text(encoding="latin-1")), unit).encode()
        candidates = restored(current, translation)
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

