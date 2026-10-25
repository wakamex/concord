"""Seed a daisy unit from Irrlicht 0.7 and place it in the target.

daisy is a fork of Irrlicht 0.7, and many daisy files kept Irrlicht's code with
the names changed. `seed` writes a unit's source from Irrlicht's file of the same
name (translated by concord.irrlicht), along with any Irrlicht headers it needs
that Harvest does not have yet; adds the iostream include when the Mac debug map
shows the unit had a static initializer; registers the unit in units.toml; and,
when the matcher cannot place the unit's own .text from known symbols, finds it
in the target as the run of function extents whose sizes match the compiled
object's functions, preferring the run nearest the unit's placed inline copies.
`hv match --learn` then fills in symbol addresses the matched code implies.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from elftools.elf.elffile import ELFFile
from tree_sitter import Parser

from concord.complete import (
    TYPES,
    complete,
    complete_enum,
    missing_members,
    regressions,
    shared_enums,
    undeclared,
)
from concord.harvest import BUILD, Harvest
from concord.irrlicht import HEADERS, bare, code_words, name_helpers, translate
from concord.transforms import CPP

IRRLICHT = Path("third_party/irrlicht-0.7")


@dataclass
class SeedResult:
    unit: str
    exact: int = 0
    functions: int = 0
    port_conflicts: list[str] = field(default_factory=list)  # functions port/src also defines
    stubbed: list[str] = field(default_factory=list)  # "path:line name" of each function given a stub body
    placements: dict[str, int] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)
    error: str = ""


def _mac_path(root: Path, unit: str) -> str:
    """The daisy path the Mac debug map gives a unit with this file name, which
    can sit in a subdirectory the path given does not name, such as Null/ or
    Software/. The path given when the map has none."""
    symbols = root / "reference" / "1.18-mac-i386" / "symbols.csv"
    if not symbols.exists():
        return unit
    found = re.search(r",(daisy/[^,\s]*/" + re.escape(Path(unit).name) + r"),", symbols.read_text())
    return found[1] if found else unit


def _settle_headers(root: Path, originals: dict[str, str], allowed: frozenset[str]) -> tuple[list[str], list[str]]:
    """The changed headers kept, and a note for each one restored. Headers that settle
    together are kept together, since one alone can make a unit worse that the pair does
    not, as a class header and the interface it derives from. Only when the set does not
    settle is each header tried alone."""
    if originals and not regressions(Harvest(root), originals, allowed):
        return sorted(originals), []
    kept, notes = [], []
    for header, text in sorted(originals.items()):  # one at a time: each has its own includers
        regressed = regressions(Harvest(root), {header: text}, allowed)
        if regressed:
            (root / "src" / header).write_text(text)
            notes.append(f"completing {header} makes {', '.join(regressed)} worse")
        else:
            kept.append(header)
    return kept, notes


def seed(
    root: Path, unit: str, complete_types: bool = False, allowed: frozenset[str] = frozenset(), stub: bool = False
) -> SeedResult:
    """Seed `unit` (a path under src/, such as daisy/video/Software/CTRFlat.cpp).
    With `stub`, a function of the translated files whose body does not compile
    keeps its declarator and gets a body that traps, so the rest of the unit can
    compile and match."""
    unit = _mac_path(root, unit)
    result = SeedResult(unit)
    irrlicht = root / IRRLICHT / "source" / "Irrlicht" / Path(unit).name
    if not irrlicht.exists():
        result.error = f"no Irrlicht source {irrlicht.name}"
        return result
    target = root / "src" / unit
    target.parent.mkdir(parents=True, exist_ok=True)
    present = _snapshot(root)
    shared_enums(root)
    text = _tidy(provenance(translate(irrlicht.read_text(encoding="latin-1")), f"source/Irrlicht/{irrlicht.name}"))
    if _has_static_initializer(root, unit):
        text = re.sub(r"^#include", "#include <iostream>\n#include", text, count=1, flags=re.MULTILINE)
    target.write_text(text)
    result.written.append(unit)
    result.written += _headers(root, target, text, set())
    # qualify once every header is in place, since a name is pointed at ox only
    # when a header there declares it
    _indexes.clear()
    helpers = _color_helpers(root, target.read_text())  # before qualify, which includes ColorPacking.h when a helper is called
    for written in result.written:
        path = root / "src" / written
        path.write_text(drop_forward_declared_includes(qualify(root, path.read_text(), written)))
    result.written += helpers
    result.written += _rename_invented_structs(root, result.written)
    _register(root, unit, {})
    output = _match(root, unit, learn=False)
    tried: set[tuple[str, str]] = set()
    # Completing ox headers is opt-in: each member added to SColor.h, CRect.h or
    # CVector3d.h shifts the declaration numbering of every unit including it, and
    # functions sensitive to it (concord phase) stop matching, as
    # CSpritePackage::removeAnimationState did. A completion stays only if settle
    # finds those units no worse.
    originals: dict[str, str] = {}
    if complete_types and _complete_interfaces(root, result.written, originals):
        output = _match(root, unit, learn=False)
    names_tried: set[str] = set()
    for _ in range(8):  # each round can expose errors the previous ones hid
        progressed = _include_incomplete(root, target, output)
        missing = (missing_members(output) - tried) if complete_types else set()
        failed = set()
        if complete_types and _declare_members(root, output, originals):
            progressed = True
        if complete_types and _declare_overrides(root, output, originals):
            progressed = True
        for ox_type, member in sorted(missing):
            # one member at a time: one that calls an Irrlicht helper Harvest lacks
            # would break every unit including the header, and is left out
            header = TYPES[ox_type][0]
            before = (root / "src" / header).read_text()
            if not complete(root, {(ox_type, member)}):
                tried.add((ox_type, member))
                continue
            errors = _compile_errors(root, header)
            if errors and not _settle_completion(root, header, ox_type, errors, originals):
                (root / "src" / header).write_text(before)
                failed.add((ox_type, member))
                continue
            originals.setdefault(header, before)
            tried.add((ox_type, member))
            progressed = True
        for name in sorted(undeclared(output) - names_tried) if complete_types else []:
            names_tried.add(name)
            snapshot = {p: p.read_text() for p in (root / "src" / "ox").rglob("*.h")}
            changed = complete_enum(root, name)
            if changed is None:
                continue
            if not _compiles_header(root, changed):
                (root / "src" / changed).write_text(snapshot[root / "src" / changed])
                continue
            originals.setdefault(changed, snapshot[root / "src" / changed])
            progressed = True
        if not progressed:
            tried |= failed  # else tried again next round: it may call a member this round added
        if not progressed:
            break
        output = _match(root, unit, learn=False)
    kept, unsettled = _settle_headers(root, originals, allowed)  # a header whose change makes other units worse is restored
    result.written += kept
    note = "; ".join(unsettled)
    if unsettled and not stub:
        result.error = note
        _unregister(root, unit)
        _discard(root, result, present)
        return result
    if unsettled:
        output = _match(root, unit, learn=False)
    for _ in range(6 if stub else 0):  # a stubbed body can expose errors the compiler stopped before
        if "error:" not in output:
            break
        stubbed = _stub_failing(root, output, set(result.written))
        if not stubbed:
            break
        result.stubbed += stubbed
        output = _match(root, unit, learn=False)
    if "error:" in output or "returned non-zero" in output:
        errors = "\n".join(line for line in output.splitlines() if "error" in line)[:3000]
        result.error = f"{note}\n{errors}" if note else errors
        _unregister(root, unit)
        _discard(root, result, present)
        return result
    if ".text: not placed" in output:
        result.placements = _place(root, unit)
    elif ".bss: not placed" in output:
        result.placements = _place_bss(root, unit)
    if result.placements:
        _register(root, unit, result.placements)
    for _ in range(3):  # a learned address can let the next run learn another
        output = _match(root, unit, learn=True)
        if ("unmapped or truncated range:" in output or ".rodata: not placed" in output) and ".rodata" not in result.placements:
            # a jump table's function laid out past its FDE, or an inline after it holds a jump table
            rodata = _place_rodata(root, unit)
            if rodata:
                result.placements.update(rodata)
                _register(root, unit, result.placements)
    for _ in range(2):
        called = _place_called_sections(root, unit)
        if not called:
            break
        _register(root, unit, {**result.placements, **called})
        for _ in range(3):
            output = _match(root, unit, learn=True)
        _register(root, unit, result.placements)
        output = _match(root, unit, learn=False)
    result.port_conflicts = port_conflicts(root, unit)
    found = re.search(r"functions (\d+)/(\d+)", output)
    if found:
        result.exact, result.functions = int(found[1]), int(found[2])
    else:
        # the matcher stopped, as on a placement outside the target: undo the seed as for a compile error
        last = output.strip().splitlines()[-1] if output.strip() else "hv match printed no result"
        result.error = f"{note}\n{last}" if note else last
        _unregister(root, unit)
        _discard(root, result, present)
    return result


ERROR_LINE = re.compile(r"^(src/[^:\s]+):(\d+): error:", re.MULTILINE)
STUB = b"\n{\n    // Not recovered: Irrlicht 0.7's body does not compile against daisy's interfaces.\n    __builtin_trap();\n}"


def _stub_failing(root: Path, output: str, written: set[str]) -> list[str]:
    """Give every function definition of a file this seed wrote that holds a
    compile error a body that only traps, and drop its constructor initializer
    list, keeping the declarator: the unit keeps the function, its vtable when
    the function is the key function, and its place among the unit's functions,
    while the function itself stays inexact. An error outside any function body,
    or in a file the seed did not write, is left for the caller to report. A
    non-void function without a return is a warning in C++, so one body fits all.
    Returns the functions stubbed, as path:line name."""
    by_file: dict[str, set[int]] = {}
    for path, line in ERROR_LINE.findall(output):
        relative = path.removeprefix("src/")
        if relative in written:
            by_file.setdefault(relative, set()).add(int(line))
    stubbed = []
    for relative, lines in sorted(by_file.items()):
        path = root / "src" / relative
        source = path.read_bytes()
        tree = Parser(CPP).parse(source)
        edits = []
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "function_definition":
                body = node.child_by_field_name("body")
                first, last = node.start_point[0] + 1, node.end_point[0] + 1
                if body is not None and any(first <= line <= last for line in lines) and body.text != STUB.strip():
                    if any(line <= body.start_point[0] for line in lines if first <= line <= last):
                        edits.append((node.child_by_field_name("declarator").end_byte, body.end_byte, node))  # the initializer list fails: drop it
                    else:  # only the body fails: keep a base-class initializer list that compiles
                        edits.append((body.start_byte, body.end_byte, node))
                continue
            stack.extend(node.children)
        for start, end, node in sorted(edits, key=lambda edit: -edit[0]):
            stub = STUB if source[start:start + 1] != b"{" else STUB.lstrip()  # a body start already has no newline before it
            source = source[:start] + stub + source[end:]
            name = node.child_by_field_name("declarator").text.decode().split("(")[0].strip()
            stubbed.append(f"{relative}:{node.start_point[0] + 1} {name}")
        if edits:
            path.write_bytes(source)
    return sorted(stubbed, key=lambda item: (item.split(":")[0], int(item.split(":")[1].split()[0])))


def _snapshot(root: Path) -> dict[Path, bytes]:
    """Every source and header under src/ with its contents, and the port's build
    file and the target's config, so a failed seed can put the checkout back as it
    found it."""
    files = [path for pattern in ("*.h", "*.cpp") for path in (root / "src").rglob(pattern)]
    # the build file and the target's config, which a seed's learning pass and registration write
    others = [root / "port" / "build.zig", root / "config" / BUILD / "symbols.tsv", root / "config" / BUILD / "units.toml"]
    return {path: path.read_bytes() for path in [*files, *(other for other in others if other.exists())]}


def _discard(root: Path, result: SeedResult, present: dict[Path, bytes]) -> None:
    """Undo a failed seed: remove the files it created and restore the ones it
    changed, such as a seeded header it translated again, which an earlier unit
    may depend on as it was."""
    for path in [*(root / "src").rglob("*.h"), *(root / "src").rglob("*.cpp")]:
        if path not in present:
            path.unlink()
    for path, contents in present.items():
        if path.exists() and path.read_bytes() != contents:
            path.write_bytes(contents)
    result.written = [w for w in result.written if (root / "src" / w).exists()]


def header(root: Path, name: str) -> list[str]:
    """Translate one Irrlicht header Harvest lacks, and the headers it includes, to
    the paths the Mac debug map records; return what was written."""
    existing = [
        p
        for p in (root / "src").rglob(Path(name).name)
        if ORIGIN_COMMENT in p.read_text(errors="replace")
        and p.read_text(errors="replace").splitlines()[2].endswith("not the original source.")
    ]
    if len(existing) == 1 and _original(root, Path(name).name) is not None:
        relative = existing[0].relative_to(root / "src").as_posix()
        translated = _translation(root, _original(root, Path(name).name), relative, Path(name).name)
        existing[0].write_text(translated)
        _headers(root, existing[0], translated, {relative})
        _indexes.clear()
        existing[0].write_text(qualify(root, existing[0].read_text(), relative))
        return [relative]
    probe = root / "src" / "concord_header_probe.cpp"
    probe.write_text(f'#include "{name}"\n')
    try:
        written = _headers(root, probe, probe.read_text(), set())
    finally:
        probe.unlink()
    _indexes.clear()
    for path in written:
        (root / "src" / path).write_text(qualify(root, (root / "src" / path).read_text(), path))
    return written


NAMESPACES = ("core", "video", "scene", "io", "gui", "event")
DEFINITION = re.compile(r"^\s*(?:class|struct)\s+(\w+)\s*(?::[^;{]*)?\{|^\s*enum\s+(\w+)\s*\{([^}]*)\}", re.MULTILINE | re.DOTALL)
DECLARATION = re.compile(r"^\s*(?:class|struct|enum)\s+(\w+)\s*[;{:\n]|^\s*typedef\b[^;]*?(\w+)\s*;", re.MULTILINE)
CONSTANT = re.compile(r"^\s*const\s+(?:unsigned\s+)?\w+\s+([A-Z]\w*)\s*=", re.MULTILINE)


@dataclass
class Index:
    """What a tree of headers declares: every name, the header that defines each
    class, struct, enum, enumerator or constant, and the namespace-level
    enumerators and constants."""

    names: set[str] = field(default_factory=set)
    headers: dict[str, str] = field(default_factory=dict)
    enumerators: set[str] = field(default_factory=set)


_indexes: dict[tuple[Path, bool], Index] = {}


def _index(root: Path, directory: Path, recursive: bool = True) -> Index:
    if (directory, recursive) not in _indexes:
        index = Index()
        top = directory == root / "src" / "ox" and not recursive
        for header in sorted(directory.rglob("*.h") if recursive else directory.glob("*.h")):
            text = header.read_text(errors="replace")
            if top:  # a name in a nested namespace belongs to that namespace, not to ox
                text = _without_namespaces(text, NAMESPACES)
            _index_text(root, index, header, text)
        if directory.parent == root / "src" / "ox" and directory.name in NAMESPACES:
            # a top-level ox header may open this namespace, as IOxDevice.h opens video
            for header in sorted((root / "src" / "ox").glob("*.h")):
                body = _namespace_text(header.read_text(errors="replace"), directory.name)
                if body:
                    _index_text(root, index, header, body)
        _indexes[(directory, recursive)] = index
    return _indexes[(directory, recursive)]


def _index_text(root: Path, index: Index, header: Path, text: str) -> None:
    """Add the names a header's text declares to the index, under the header's path."""
    relative = header.relative_to(root / "src").as_posix()
    index.names |= {found[1] or found[2] for found in DECLARATION.finditer(text)}
    for found in DEFINITION.finditer(text):
        name = found[1] or found[2]
        index.headers.setdefault(name, relative)
        if found[3]:
            enumerators = set(re.findall(r"^\s*(\w+)", found[3], re.MULTILINE))
            index.enumerators |= enumerators
            for enumerator in enumerators:
                index.headers.setdefault(enumerator, relative)
    for constant in CONSTANT.findall(text):
        index.enumerators.add(constant)
        index.headers.setdefault(constant, relative)


def _without_namespaces(text: str, names: tuple[str, ...]) -> str:
    """The text without the blocks of the named namespaces."""
    pattern = re.compile(r"namespace (?:" + "|".join(names) + r")\s*\{")
    while found := pattern.search(text):
        text = text[: found.start()] + text[_brace_end(text, found.end() - 1) + 1 :]
    return text


def _namespace_text(text: str, name: str) -> str:
    """The bodies of the blocks of the named namespace, joined."""
    return "\n".join(text[found.end() : _brace_end(text, found.end() - 1)] for found in re.finditer(r"namespace " + name + r"\s*\{", text))


def _in_code(pattern: str, replacement: str, text: str) -> str:
    """re.sub outside preprocessor lines, string literals and // comments."""
    lines = []
    for line in text.split("\n"):
        if not line.lstrip().startswith("#"):
            code, mark, comment = line.partition("//")
            if code.count('"') % 2:  # the // is inside a string
                code, mark, comment = line, "", ""
            parts = code.split('"')
            parts[::2] = [re.sub(pattern, replacement, part) for part in parts[::2]]
            line = '"'.join(parts) + mark + comment
        lines.append(line)
    return "\n".join(lines)


def drop_forward_declared_includes(text: str) -> str:
    """The header without an include of an ox header whose type it also
    forward-declares: an Irrlicht header forward-declares a type it uses only by
    pointer or reference, and including the full definition there clashes with the
    port's own definition of the type."""
    for name in re.findall(r"^\s*(?:class|struct) (\w+);", text, re.MULTILINE):
        text = re.sub(r'^#include "ox/\w+/' + name + r'\.h"\n', "", text, flags=re.MULTILINE)
    return text


def helper_taken(root: Path, text: str, own: str = "") -> set[str]:
    """The names a file-local helper in `text` cannot take: every identifier of
    the unit's own code, of the file it replaces (the on-disk `own`), and every
    name the checkout's headers declare."""
    taken = code_words(text)
    path = root / "src" / own if own else None
    if path is not None and path.exists():
        taken |= code_words(path.read_text(errors="replace"))
    indexes = [_index(root, root / "src" / "ox", recursive=False)]
    indexes += [_index(root, root / "src" / kind / ns) for kind in ("ox", "daisy") for ns in NAMESPACES]
    for index in indexes:
        taken |= index.names | set(index.headers) | index.enumerators
    return taken


def companion_header_first(text: str, own: str) -> str:
    """Keep a unit's companion before other quoted headers, after system includes.

    Conditional includes retain their position. Moving a conditional companion
    out of its guard would change which declarations the unit sees.
    """
    if not own.endswith('.cpp'):
        return text
    wanted = Path(own).with_suffix('.h').name
    includes, depth, offset = [], 0, 0
    for line in text.splitlines(keepends=True):
        directive = re.match(r'^\s*#\s*(if|ifdef|ifndef|endif)\b', line)
        if directive:
            depth += -1 if directive[1] == 'endif' else 1
        match = re.match(r'^#include ([<"])([^>"\n]+)[>"][^\n]*(?:\n|$)', line)
        if match:
            includes.append((offset, offset + len(line), match[1], Path(match[2]).name, depth))
        offset += len(line)
    companions = [row for row in includes if row[3] == wanted]
    if len(companions) != 1 or companions[0][4]:
        return text
    own_line = companions[0]
    first = next((row for row in includes if row[2] == '"' and not row[4]), own_line)
    if first[0] >= own_line[0]:
        return text
    return text[:first[0]] + text[own_line[0]:own_line[1]] + text[first[0]:own_line[0]] + text[own_line[1]:]


def qualify(root: Path, text: str, own: str = "") -> str:
    """Point Irrlicht names at Harvest's ox declarations. `video::SMaterial` names
    ox::video::SMaterial when Harvest declares it there and daisy does not; an
    enumerator Harvest declares in another ox namespace, such as ELL_ERROR in
    ox::event, is qualified; each daisy namespace block of the text gets a
    using-directive for its ox counterpart, so an unqualified base class such as
    ISceneNode resolves; and the header defining each ox type the text names is
    included, since Harvest's versions of Irrlicht headers often only
    forward-declare them. A `_concord_` helper marker takes the plain name a
    person would write, numbered when the unit or a header already uses it. None
    of the type qualifications changes the mangled names. The companion header
    stays before generated quoted includes: header definition order can change
    inlining and exception tables. A file of ox itself (`own`, its path under src/) needs no
    using-directive and does not include itself."""
    inside = own.startswith("ox/")
    if "_concord_" in text:
        text = name_helpers(text, helper_taken(root, text, own))
    used = set()
    opened = {ns for ns in NAMESPACES if re.search(r"^namespace " + ns + r"\s*\{", text, re.MULTILINE)}
    reachable = set()  # names visible unqualified: daisy's own and those of opened ox namespaces
    for ns in NAMESPACES:
        reachable |= set(_index(root, root / "src" / "daisy" / ns).headers)
        if ns in opened:
            reachable |= _index(root, root / "src" / "ox" / ns).names
    for ns in NAMESPACES:
        # daisy's forward declarations of ox types do not make them daisy's
        ox, daisy = _index(root, root / "src" / "ox" / ns), set(_index(root, root / "src" / "daisy" / ns).headers)
        for name in (ox.names | ox.enumerators) - daisy:
            text = _in_code(r"(?<![\w:])" + ns + "::" + name + r"\b", f"ox::{ns}::{name}", text)
        if not inside:
            # a forward declaration inside daisy's namespace would declare a second, incomplete type
            for name in set(ox.headers) - daisy:
                text = re.sub(r"^[ \t]*(?:class|struct) " + name + r";[ \t]*\n", "", text, flags=re.MULTILINE)
        if ns not in opened:
            # a bare name only an unopened ox namespace declares, such as SEvent or ELL_ERROR
            for name in ((set(ox.headers) | ox.enumerators) - reachable) & set(re.findall(r"\b\w+\b", text)):
                text = _in_code(r"(?<![\w:.>])" + name + r"\b(?!\s*\()", f"ox::{ns}::{name}", text)
        if (root / "src" / "ox" / ns).is_dir() and ns in opened and not inside:  # ox's own files are already inside
            text = re.sub(r"(namespace " + ns + r"\s*\{)", r"\1\nusing namespace ox::" + ns + ";", text, count=1)
            # a using-directive needs the namespace declared first
            text = re.sub(r"^(namespace daisy\b)", "namespace ox { namespace " + ns + " {} }\n\\1", text, count=1, flags=re.MULTILINE)
            used.add(ns)
    needed = []
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.DOTALL)  # a name in a comment needs no header
    words = set(re.findall(r"\b[A-Z]\w+\b", code))
    for ns in NAMESPACES:
        index = _index(root, root / "src" / "ox" / ns)
        for name in sorted(words & set(index.headers)):
            qualified = re.search(r"\box::" + ns + "::" + name + r"\b", text)
            if re.search(r"^\s*(?:class|struct) " + name + r";", code, re.MULTILINE):
                continue  # the text forward-declares it, so uses it only by pointer or reference
            if (ns in used or qualified or (inside and ns in opened)) and index.headers[name] != own and not _includes(text, index.headers[name]):
                needed.append(f'#include "{index.headers[name]}"\n')
    top = _index(root, root / "src" / "ox", recursive=False)
    if not inside:  # a name declared at the top of ox, such as EKEY_CODE in Keycodes.h, is qualified as ox::
        for name in (set(top.headers) | top.enumerators) - reachable:
            text = _in_code(r"(?<![\w:.>])" + name + r"\b(?!\s*\()", f"ox::{name}", text)
            text = _in_code(r"(?<![\w:])daisy::" + name + r"\b", f"ox::{name}", text)  # Irrlicht's irr::KEY_UP became daisy::KEY_UP
    for name in sorted(words & set(top.headers)):
        if re.search(r"\box::" + name + r"\b", text) and not _includes(text, top.headers[name]):
            needed.append(f'#include "{top.headers[name]}"\n')
    packing = root / COLOR_PACKING
    names = re.findall(r"inline \w+ (\w+)\(", packing.read_text()) + re.findall(r"const ECOLOR_FORMAT (\w+)\b", packing.read_text()) if packing.exists() else []
    if packing.exists() and not _includes(text, COLOR_PACKING.relative_to("src").as_posix()) and any(
        re.search(r"\b(?:ox::video::)?" + name + r"\b", text) for name in names
    ):
        needed.append('#include "ox/video/ColorPacking.h"\n')  # a helper from it is called
    if re.search(r"\box::algo::(?:sort|linearSearchPos|binarySearchPos|binarySearchIf|linearSearchIf|advanceIterator)\b", text) and not _includes(text, "ox/algo/CArrayFunctions.h"):
        needed.append('#include "ox/algo/CArrayFunctions.h"\n')
    elif (
        re.search(r"\bstd::(?:find|sort|lower_bound|upper_bound|find_if|distance)\s*\(", text)
        and "#include <algorithm>" not in text
        and not _includes(text, "ox/algo/CArrayFunctions.h")  # it includes <algorithm> itself
    ):
        needed.append("#include <algorithm>\n")  # a translated search calls the std algorithm directly
    if "ox::algo::CRand" in text and not _includes(text, "ox/algo/CRand.h"):
        needed.append('#include "ox/algo/CRand.h"\n')
    if "ox::core::GRAD_PI2" in text and not _includes(text, "ox/core/irrMath.h"):
        needed.append('#include "ox/core/irrMath.h"\n')
    if own.endswith(".cpp") and re.search(r"\bS3DVertex(?:\s+\w+)?\s*\([^;\n]*,", text) and not _includes(text, "ox/video/S3DVertexInline.h"):
        needed.append('#include "ox/video/S3DVertexInline.h"\n')  # the 9-argument S3DVertex constructor lives out of S3DVertex.h
    if not inside and own.endswith(".h"):  # a daisy class a header uses, which the provisional header did not declare or include
        for ns in sorted(opened):
            daisy = _index(root, root / "src" / "daisy" / ns)
            for name in sorted(words & set(daisy.headers)):
                if daisy.headers[name] != own and not _includes(text, daisy.headers[name]) and not re.search(r"\b(?:class|struct)\s+" + name + r"\b", text):
                    needed.append(f'#include "{daisy.headers[name]}"\n')
    if needed:
        added = "".join(dict.fromkeys(needed))
        if re.search(r"^#include ", text, flags=re.MULTILINE):
            text = re.sub(r"^(#include [^\n]*\n)", lambda m: m[1] + added, text, count=1, flags=re.MULTILINE)
        elif re.search(r"^namespace ", text, flags=re.MULTILINE):  # a file with no include yet gets its first at the top
            text = added + text
    return companion_header_first(text, own)


def _includes(text: str, header: str) -> bool:
    """Whether the text includes the header, by its path under src/ or by file name
    alone, as a file beside it does."""
    return bool(re.search(r'^#include "(?:[^"\n]*/)?' + re.escape(Path(header).name) + r'"', text, re.MULTILINE))


COLOR_PACKING = Path("src/ox/video/ColorPacking.h")


# Daisy's packing of a colour to A1R5G5B5 (CVideoNull.cpp), as a free function for ox
PACKING_HELPERS = {
    "toA1R5G5B5": "inline short toA1R5G5B5(unsigned int c)\n{\n    return (short)(((c & 0xff000000) ? 0x8000 : 0) | ((c >> 9) & 0x7c00) | ((c >> 6) & 0x3e0) | ((c >> 3) & 0x1f));\n}\n\n",
}


def _color_helpers(root: Path, text: str) -> list[str]:
    """Add to ox/video/ColorPacking.h each of Irrlicht's packed 16-bit color helpers
    (the free inline functions of include/SColor.h, such as getRed(s16)) that the
    text calls and the header lacks. ColorPacking.h is the header Harvest keeps
    them in, apart from SColor.h, so that adding one changes no other unit."""
    header = root / COLOR_PACKING
    current = header.read_text()
    irrlicht = translate((root / IRRLICHT / "include" / "SColor.h").read_text(encoding="latin-1"))
    added = []
    for name in sorted(set(re.findall(r"(?:ox::video::)?(\w+)\(", text))):
        if re.search(r"\b" + name + r"\(", current):
            continue
        if name in PACKING_HELPERS:  # a member of Irrlicht's SColor, which Harvest packs as a free function
            added.append(PACKING_HELPERS[name])
            continue
        found = re.search(r"\tinline (\w+) " + name + r"\((short|int) (\w+)\)\s*\{(.*?)\}", irrlicht, re.DOTALL)
        if found is None:
            continue
        body = " ".join(found[4].split())
        added.append(f"inline {found[1]} {name}({found[2]} {found[3]})\n{{\n    {body}\n}}\n\n")
    if re.search(r"\bECF_R5G6B5\b|\bECF_R8G8B8\b", text) and "ECF_R5G6B5" not in current:
        added.append("const ECOLOR_FORMAT ECF_R5G6B5 = (ECOLOR_FORMAT)1;\nconst ECOLOR_FORMAT ECF_R8G8B8 = (ECOLOR_FORMAT)2;\n\n")
        if '#include "ox/video/IVideoDriver.h"' not in current:
            current = '#include "ox/video/IVideoDriver.h"\n' + current  # the colour format enum
    if not added:
        return []
    marker = "} // end namespace video\n} // end namespace ox"
    header.write_text(current.replace(marker, "".join(added) + marker, 1))
    return [COLOR_PACKING.relative_to("src").as_posix()]


IRRLICHT_NOTICE = '// This file is part of the "Irrlicht Engine".\n// For conditions of distribution and use, see copyright notice in Irrlicht.h\n'


def provenance(text: str, original: str, namespace: str = "daisy") -> str:
    """Replace Irrlicht's file notice with the one Harvest's Irrlicht-derived files
    carry: where the code came from, its license, and that it is a recovery."""
    notice = (
        f"// Adapted from Irrlicht 0.7 {original} (license: third_party/irrlicht-0.7/include/irrlicht.h).\n"
        f"// Recovered for Harvest's {namespace} namespace; not the original source.\n"
    )
    return text.replace(IRRLICHT_NOTICE, notice, 1)


INCOMPLETE = re.compile(r"invalid use of incomplete type '(?:const )?(?:struct|class) (ox|daisy)::(\w+)::(\w+)'")


def _include_incomplete(root: Path, source: Path, output: str) -> bool:
    """Include in the unit the header of each ox type the compiler found incomplete,
    which the unit uses through a pointer some other header only forward-declares."""
    text = source.read_text()
    added = []
    for tree, ns, name in sorted(set(INCOMPLETE.findall(output))):
        header = _index(root, root / "src" / tree / ns).headers.get(name)
        if header and f'#include "{header}"' not in text:
            added.append(f'#include "{header}"\n')
    if added:
        source.write_text(re.sub(r"^(#include [^\n]*\n)", lambda m: m[1] + "".join(added), text, count=1, flags=re.MULTILINE))
    return bool(added)


def _bring_includes(root: Path, ox_type: str) -> bool:
    """Add to the ox header the includes its Irrlicht original has and it lacks,
    for members that use another core type: matrix4's transformPlane needs plane3d."""
    header, irrlicht_header, _ = TYPES[ox_type]
    path = root / "src" / header
    text = path.read_text()
    original = (root / IRRLICHT / "include" / irrlicht_header).read_text(encoding="latin-1")
    wanted = [HEADERS[name].split('"')[0] for name in re.findall(r'^#include "([^"]+)"', original, re.MULTILINE) if name in HEADERS]
    added = [f'#include "{name}"\n' for name in wanted if Path(name).name not in text and (root / "src" / name).exists()]
    if not added:
        return False
    first = re.search(r"^#include [^\n]*\n", text, re.MULTILINE) or re.search(r"^#define [^\n]*\n", text, re.MULTILINE)
    path.write_text(text[: first.end()] + "".join(added) + text[first.end() :])
    return True


def _compiles_header(root: Path, header: str) -> bool:
    """Whether a file that only includes the header compiles with a built unit's flags."""
    return not _compile_errors(root, header)


def _settle_completion(
    root: Path, header: str, ox_type: str, errors: str, originals: dict[str, str], depth: int = 0
) -> bool:
    """Whether the completed header compiles once its dependencies are satisfied:
    the includes of its Irrlicht original, and the members the added members
    themselves need, which complete transitively (S3DVertex's operator== compares
    SColor fields, so it needs SColor's operator==). originals records each
    changed header's text before the change, to be settled by the caller."""
    if not errors:
        return True
    if _bring_includes(root, ox_type) and not _compile_errors(root, header):
        return True
    if depth >= 4:
        return False
    for sub_type, sub_member in sorted(missing_members(errors)):
        sub_header = TYPES[sub_type][0]
        sub_before = (root / "src" / sub_header).read_text()
        if not complete(root, {(sub_type, sub_member)}):
            continue
        sub_errors = _compile_errors(root, sub_header)
        if sub_errors and not _settle_completion(root, sub_header, sub_type, sub_errors, originals, depth + 1):
            (root / "src" / sub_header).write_text(sub_before)
            continue
        originals.setdefault(sub_header, sub_before)
    return not _compile_errors(root, header)


def _compile_errors(root: Path, header: str) -> str:
    """The compiler output of a file including the header and, when the header
    declares one of the types this module completes, explicitly instantiating it
    (which compiles every member of a template), with the flags of a built unit;
    "" when it compiles, or a note when it cannot be tried."""
    ox_type = next((t for t, (h, _, _) in TYPES.items() if h == header), None)
    probe = f'#include "{header}"\n'
    text = (root / "src" / header).read_text()
    if ox_type and re.search(r"template\s*<\s*class \w+\s*>\s*(class|struct) " + ox_type + r"\b", text):
        probe += f"template class ox::{header.split('/')[1]}::{ox_type}<float>;\n"
    # compile as a unit the last hv match built, since the one being seeded may not build yet
    host = next((p.name.removesuffix(".json") for p in sorted((root / "build" / "match" / BUILD).glob("*.json"))), None)
    if host is None:
        return f"{header}: no built unit to compile with"
    try:
        harvest = Harvest(root)
        evaluation = harvest.evaluate(harvest.source(host), {"probe": probe.encode()})["probe"]
    except (OSError, RuntimeError, KeyError):
        return f"{header}: could not compile"
    return evaluation.error


def port_conflicts(root: Path, unit: str) -> list[str]:
    """Functions the unit defines that Harvest's port also defines in port/src.
    The port links every unit in units.toml except those its build.zig replaces, so
    a stub it kept for a function no unit had yet becomes a duplicate symbol once
    the unit is recovered; such a stub goes when the unit arrives."""
    port = root / "port" / "src"
    slug = unit.replace("/", "__").removesuffix(".cpp")
    obj = root / "build" / "match" / BUILD / f"{slug}.o"
    if not port.is_dir() or not obj.exists():
        return []
    with open(obj, "rb") as stream:
        elf = ELFFile(stream)
        text = {i for i, s in enumerate(elf.iter_sections()) if s.name == ".text"}
        names = [
            s.name
            for s in elf.get_section_by_name(".symtab").iter_symbols()
            if s["st_info"]["bind"] == "STB_GLOBAL" and s["st_info"]["type"] == "STT_FUNC" and s["st_shndx"] in text
        ]
    demangled = subprocess.run(["c++filt"], input="\n".join(names), capture_output=True, text=True, check=True).stdout
    sources = [(path, path.read_text(errors="replace")) for path in port.rglob("*.cpp")]
    conflicts = []
    for name in demangled.splitlines():
        parts = name.split("(")[0].split("::")
        if len(parts) >= 2 and parts[-2][:1].isupper():
            # a member: defined out of line as Class::method(
            pattern, scope = re.escape("::".join(parts[-2:])) + r"\s*\(", None
        else:
            # a free function: defined by name inside its namespace
            pattern = r"^[\w:<>*& ]*\b" + re.escape(parts[-1]) + r"\s*\([^;{]*\)\s*\{"
            scope = parts[-2] if len(parts) >= 2 else None
        for path, source in sources:
            if scope and not re.search(r"namespace " + re.escape(scope) + r"\b", source):
                continue
            if re.search(pattern, source, re.MULTILINE):
                conflicts.append(f"{name} in {path.relative_to(root).as_posix()}")
    return conflicts


def replace_in_port(root: Path, unit: str, reason: str | None = None) -> bool:
    """List the unit among those the port leaves out (`replaced` in
    port/build.zig), with `reason` as its comment, by default that port/src
    already implements it. False when it is listed."""
    path = root / "port" / "build.zig"
    text = path.read_text()
    found = re.search(r"^const replaced = \[_\]\[\]const u8\{\n(.*?)^\};", text, re.MULTILINE | re.DOTALL)
    if found is None or any(unit.startswith(entry) for entry in re.findall(r'"([^"]+)"', found[1])):  # port/build.zig matches entries as prefixes
        return False
    if reason is None:
        sources = sorted({conflict.split(" in ")[1] for conflict in port_conflicts(root, unit)})
        reason = f"the port keeps its own {', '.join(sources)}"
    entry = f"    // Recovered from Irrlicht 0.7; {reason}.\n    \"{unit}\",\n"
    path.write_text(text[: found.end(1)] + entry + text[found.end(1) :])
    return True


UNDEFINED = re.compile(r"undefined symbol: (.+)\n\s+note: referenced by ([^\s:]+)")


def port_unresolved(root: Path) -> dict[str, list[str]]:
    """Build the port and return, by source file name, the symbols the link left
    undefined there: a recovered unit can call or derive from code no unit or
    port source defines yet, such as a base class whose file is not recovered."""
    found: dict[str, list[str]] = {}
    for step in ("harvest", "census"):  # the census links whole archives, as Emscripten does, and reports what the native build drops
        done = subprocess.run(["zig", "build", step], cwd=root / "port", capture_output=True, text=True, check=False)
        for symbol, source in UNDEFINED.findall(done.stdout + done.stderr):
            found.setdefault(source, [])
            if symbol not in found[source]:
                found[source].append(symbol)
    return found


def stubbed_units(root: Path, units: list[str]) -> list[str]:
    """The units among `units` whose source, or a header only seeds wrote for
    them, still has a stub: such a unit would trap in the port when a stubbed
    function is called, so the port leaves it out until its stubs are gone."""
    found = []
    for unit in units:
        slug = unit.replace("/", "__").removesuffix(".cpp")
        depfile = root / "build" / "match" / BUILD / f"{slug}.d"
        sources = [root / "src" / unit]
        if depfile.exists():
            sources += [root / word for word in depfile.read_text().replace("\\\n", " ").split() if word.startswith("src/")]
        if any(path.exists() and b"__builtin_trap();" in path.read_bytes() for path in sources):
            found.append(unit)
    return found


def keep_port_linking(root: Path, units: list[str]) -> dict[str, list[str]]:
    """List among the port's replaced units each of `units` whose object leaves a
    symbol undefined in the port's link, and return what each needed. Units
    already in the port are left alone; their problems are not a seed's to hide."""
    listed = {}
    for unit in stubbed_units(root, units):
        if replace_in_port(root, unit, "some of its methods are stubs that trap"):
            listed[unit] = ["stubbed methods"]
    for _ in range(4):  # leaving one unit out can leave another's reference undefined
        missing = port_unresolved(root)
        added = False
        for unit in units:
            symbols = missing.get(Path(unit).name)
            if symbols and replace_in_port(root, unit, f"it needs {symbols[0]}, which nothing defines yet"):
                listed[unit] = symbols
                added = True
        if not added:
            break
    return listed


def _has_static_initializer(root: Path, unit: str) -> bool:
    with open(root / "reference" / "1.18-mac-i386" / "functions.csv") as stream:
        return any(row["unit"] == unit and row["symbol"].startswith("_GLOBAL__I") for row in csv.DictReader(stream))


def _resolvable(root: Path, directory: Path, name: str) -> bool:
    """Whether the include names a header Harvest has, other than one an earlier
    seed wrote and git does not track yet, which is translated again."""
    for base in (directory, root / "src", root / "src" / "HarvestFull"):
        path = base / name
        if path.exists() and not _seeded(root, path):
            return True
    return False


ORIGIN_COMMENT = "(license: third_party/irrlicht-0.7/include/irrlicht.h)."  # the provenance concord writes; a hand-written adaptation lacks it


def _rename_invented_structs(root: Path, written: list[str]) -> list[str]:
    """Rename a struct a provisional header invents, when a header Irrlicht adapted
    defines the same name: CVideoNull.h's own S2DVertex collides with Software's
    S2DVertex.h, which the unit's headers include too. The invented struct is renamed
    in its header, so both can be included. Returns the headers changed."""
    origin: set[str] = set()
    daisy = (root / "src" / "daisy").resolve()
    for path in (root / "src" / "daisy").rglob("*.h"):
        text = path.read_text(errors="replace")
        if ORIGIN_COMMENT in text:
            origin |= set(re.findall(r"^\s*struct (\w+)\s*\{", text, re.MULTILINE))
    src = (root / "src").resolve()
    renamed = []
    pending = [src / name for name in written]
    seen: set[Path] = set()
    while pending:  # every header the written files reach, through their includes
        path = pending.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        for include in re.findall(r'^#include "([^"]+)"', path.read_text(errors="replace"), re.MULTILINE):
            candidates = [(path.parent / include).resolve(), src / include]
            pending.extend(candidate for candidate in candidates if candidate.exists())
        if path.suffix != ".h" or daisy not in path.parents or ORIGIN_COMMENT in path.read_text(errors="replace"):
            continue
        text = path.read_text()
        original = _original(root, path.name)
        kept = set(re.findall(r"^\s*struct (\w+)\s*\{", original.read_text(encoding="latin-1"), re.MULTILINE)) if original else set()
        invented = {found for found in re.findall(r"^\s*struct (\w+)\s*\{", text, re.MULTILINE) if found in origin and found not in kept}
        if not invented:
            continue
        for struct in sorted(invented):
            text = _in_code(r"\b" + struct + r"\b", f"{struct}_", text)
        path.write_text(text)
        renamed.append(path.relative_to(src).as_posix())
    return sorted(set(renamed))


def _seeded(root: Path, path: Path) -> bool:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)], cwd=root, capture_output=True, check=False
    ).returncode == 0
    return not tracked and "// Adapted from Irrlicht 0.7 " in path.read_text(errors="replace")


ORIGINS = {header.split('"')[0]: name for name, header in HEADERS.items()}  # ox/core/CPlane3d.h -> plane3d.h


def _mac_headers(root: Path) -> dict[str, str]:
    """Header paths the Mac debug map records, by file name. daisy kept Irrlicht's
    public headers under ox, in ox's namespaces, and its implementation headers
    under daisy, and the map says which is which."""
    paths: dict[str, str] = {}
    with open(root / "reference" / "1.18-mac-i386" / "headers.csv") as stream:
        for row in csv.DictReader(stream):
            if row["header"].startswith(("ox/", "daisy/")):
                paths.setdefault(Path(row["header"]).name, row["header"])
    return paths


def _mac_namespace(root: Path, name: str) -> str | None:
    """The namespace the Mac symbols give a class, as a path: ox/scene."""
    with open(root / "reference" / "1.18-mac-i386" / "functions.csv") as stream:
        found = re.search(r"\b((?:ox|daisy)(?:::[a-z]\w*)*)::" + re.escape(name) + r"\b", stream.read())
    return found[1].replace("::", "/") if found else None


def _destination(root: Path, source: Path, name: str) -> tuple[Path, str] | None:
    """Where a header the text includes belongs and the Irrlicht header it comes
    from, or None when Irrlicht has no such header."""
    origin = ORIGINS.get(name, Path(name).name)
    if name in ORIGINS:
        destination = root / "src" / name
    elif origin in _mac_headers(root):
        destination = root / "src" / _mac_headers(root)[origin]
    elif (namespace := _mac_namespace(root, origin.removesuffix(".h"))) and namespace.startswith("ox"):
        destination = root / "src" / namespace / origin
    else:
        destination = source.parent / origin
    return (destination, origin) if _original(root, origin) else None


MISSING_MEMBER = re.compile(r"no '[^']*? [\w:]+::(\w+)\(.*?' member function declared in class '([\w:]+)'")
IMPLICIT_DESTRUCTOR = re.compile(r"definition of implicitly-declared '(?:virtual )?([\w:]+)::(~?\w+)\(\)'")


def _provisional_header(root: Path, namespace: str, name: str) -> Path | None:
    """The header of a daisy class that Harvest keeps as a provisional stand-in, in the
    class's namespace directory first, such as src/daisy/video/Null/ for a class the
    Mac map puts there."""
    candidates = sorted((root / "src" / "daisy").rglob(f"{name}.h"), key=lambda path: "/".join(path.relative_to(root / "src").parts[:-1]) != namespace)
    for path in candidates:
        text = path.read_text()
        if "Unrecovered[" in text or "PROVISIONAL" in text:
            return path
    return None


def _with_base(root: Path, header: Path, name: str, irrlicht: str, text: str) -> str:
    """Give the class in a provisional header the base class Irrlicht's class has,
    when the header has none, with an include of the header that declares the base."""
    head = re.search(r"class " + re.escape(name) + r"\b[^;{]*\{", irrlicht)
    base = re.search(r":\s*public\s+(\w+)", head[0]) if head else None
    stand_in = re.search(r"class " + re.escape(name) + r"\b[^;{]*\{", text)
    if base is None or stand_in is None or ":" in stand_in[0]:
        return text
    declared = _index(root, root / "src" / "ox").headers.get(base[1])
    if declared is None:
        return text
    base_name = qualify(root, base[1])
    text = text[: stand_in.start()] + f"class {name} : public {base_name}\n{{" + text[stand_in.end() :]
    include = f'#include "{Path(os.path.relpath(root / "src" / declared, header.parent)).as_posix()}"\n'
    lines = list(re.finditer(r'^#include "[^"]*"\n', text, re.MULTILINE))
    guard = re.search(r"^#define \w+\n", text, re.MULTILINE)
    at = lines[-1].end() if lines else guard.end()
    return text[:at] + include + text[at:]


def _complete_interfaces(root: Path, written: list[str], originals: dict[str, str]) -> bool:
    """Give an ox interface header that a class in a written header derives from any
    additional base classes and inline virtual methods its Irrlicht original declares."""
    changed = False
    src = (root / "src").resolve()
    ox = (src / "ox").resolve()
    pending = [src / item for item in written if item.endswith(".h") and (src / item).exists()]
    seen: set[Path] = set()
    included: set[str] = set()
    while pending:
        curr = pending.pop()
        if curr in seen or not curr.exists():
            continue
        seen.add(curr)
        text = curr.read_text(errors="replace")
        bases = {
            base.split("::")[-1]
            for clause in re.findall(r"class\s+\w+\s*:\s*([^{;]+)\{", text)
            for base in re.findall(r"(?:public|protected|private)\s+([\w:]+)", clause)
        }
        for raw in re.findall(r'^#include "([^"]+)"', text, re.MULTILINE):
            for candidate in ((curr.parent / raw).resolve(), (src / raw).resolve()):
                if candidate.exists() and ox in candidate.parents and candidate.stem in bases:
                    included.add(candidate.relative_to(src).as_posix())
                    pending.append(candidate)
    for inc in sorted(included):
        path = root / "src" / inc
        original = _original(root, path.name)
        if not path.exists() or original is None or not path.stem.startswith("I"):
            continue
        before = path.read_text()
        if ORIGIN_COMMENT not in before:
            continue
        name = path.stem
        irrlicht = translate(original.read_text(encoding="latin-1"), "ox")
        irr_head = re.search(r"class " + re.escape(name) + r"\s*:\s*([^{;]+)\{", irrlicht)
        ox_head = re.search(r"class " + re.escape(name) + r"\s*:\s*([^{;]+)\{", before)
        if irr_head is None or ox_head is None:
            continue
        text = before
        if irr_head[1].count(",") > ox_head[1].count(","):
            text = text[: ox_head.start()] + f"class {name} : {irr_head[1].strip()}\n{{" + text[ox_head.end() :]
            if re.search(r"virtual\s+~" + re.escape(name) + r"\s*\(\s*\)\s*\{", irrlicht) and f"~{name}(" not in text:
                ctor = re.search(r"\b" + re.escape(name) + r"\s*\([^;{}]*\)\s*:[^{}]*\{\s*\}\n", text)
                if ctor is not None:
                    text = text[: ctor.end()] + f"\n    virtual ~{name}() {{}}\n" + text[ctor.end() :]
        irr_body = irrlicht[irr_head.end() : _brace_end(irrlicht, irr_head.end() - 1)]
        for match in re.finditer(
            r"virtual\s+([\w:<>]+)\s+(\w+)\s*\(([^()]*)\)\s*(const\s*)?\{\s*(return\s+[^;{}]+;)?\s*\}",
            irr_body,
        ):
            ret, member, args, const, body = match.groups()
            if not re.search(r"\b" + re.escape(member) + r"\s*\(", text):
                ox_match = re.search(r"class " + re.escape(name) + r"\s*:\s*[^{;]+\{", text)
                close = _brace_end(text, ox_match.end() - 1)
                suffix = " const" if const else ""
                inner = f"\n        {body.strip()}\n    " if body else ""
                method = f"\n    virtual {ret} {member}({args.strip()}){suffix}\n    {{{inner}}}\n"
                text = text[:close] + method + text[close:]
        if text != before:
            text = drop_forward_declared_includes(qualify(root, text, inc))
            originals.setdefault(inc, before)
            path.write_text(text)
            changed = True
    return changed


def reconciled_forwards(text: str, symbols: str) -> str:
    """Resolve root ox forward declarations from the recorded daisy symbols.
    Keep the old name as a port-only alias for existing port implementations.
    """
    opening = re.search(r"^namespace ox\s*\{", text, re.MULTILINE)
    if opening is None:
        return text
    # the forward declarations at ox's own scope, before it opens a nested namespace
    head = re.split(r"^namespace \w+[^\n{]*\{", text[opening.end() :], maxsplit=1, flags=re.MULTILINE)[0]
    for keyword, name in re.findall(r"^\s*(class|struct) (\w+);", head, re.MULTILINE):
        if not re.search(r"(?<![\w:])daisy::" + name + r"\b", symbols):
            continue
        text = re.sub(r"^[ \t]*(?:" + keyword + r") " + name + r";[ \t]*\n", "", text, count=1, flags=re.MULTILINE)
        text = _in_code(r"(?<![\w:.])" + name + r"\b", "daisy::" + name, text)
        opening = re.search(r"^namespace ox\s*\{", text, re.MULTILINE)
        text = text[: opening.start()] + "namespace daisy { " + keyword + " " + name + "; }\n\n" + text[opening.start() :]
        opening = re.search(r"^namespace ox\s*\{", text, re.MULTILINE)
        text = text[:opening.end()] + "\n#ifdef HARVEST_PORT\nusing daisy::" + name + ";\n#endif\n" + text[opening.end():]
    return text


def reconcile_forwards(root: Path, relative: str) -> bool:
    """Resolve provisional forward declarations using the Mac symbol names."""
    path = root / "src" / relative
    functions = root / "reference" / "1.18-mac-i386" / "functions.csv"
    if not path.exists() or not functions.exists():
        return False
    before = path.read_text()
    text = reconciled_forwards(before, functions.read_text())
    if text != before:
        path.write_text(text)
    return text != before


PURE_VIRTUAL = re.compile(r"virtual\s+([\w:<> ,&*]+?)\s+(\w+)\s*\(([^()]*)\)\s*(const\s*)?=\s*0\s*;")


def _interface_header(root: Path, name: str) -> str | None:
    """The ox header defining `name`, found through the namespace indexes."""
    for ns in NAMESPACES:
        found = _index(root, root / "src" / "ox" / ns).headers.get(name)
        if found:
            return found
    return None


def _header_defined(root: Path, cls: str, member: str, relative: str) -> bool:
    """Whether the Mac symbols record `cls::member` as compiled from `relative`:
    an inline in the recovered header, as CSceneManager::removeAllMeshes was."""
    functions = root / "reference" / "1.18-mac-i386" / "functions.csv"
    if not functions.exists():
        return False
    scope = "::".join(relative.split("/")[:-1]) + "::" + cls + "::" + member + "("
    with open(functions) as stream:
        return any(row["marker"] == relative and row["name"].startswith(scope) for row in csv.DictReader(stream))


def declare_missing_overrides(root: Path, relative: str) -> bool:
    """Declare in a recovered class's header each pure virtual of a base interface
    the class lacks, so it is concrete and can be allocated: CSceneManager had no
    removeAllMeshes for the daisy-added ISceneManager pure virtual, so
    createSceneManager's `new CSceneManager` could not compile. An override the
    Mac map shows compiled out of this header is written inline."""
    path = root / "src" / relative
    text = path.read_text()
    changed = False
    for cls in re.finditer(r"class\s+(\w+)\s*:\s*([^{;]+)\{", text):
        name, bases = cls[1], cls[2]
        end = _brace_end(text, cls.end() - 1)
        body = text[cls.end() : end]
        missing = []
        for clause in bases.split(","):
            base = clause.strip().split()[-1].split("::")[-1]
            header = _interface_header(root, base)
            if header is None:
                continue
            for found in PURE_VIRTUAL.finditer((root / "src" / header).read_text()):
                ret, member, args = found[1].strip(), found[2], found[3].strip()
                if not re.search(r"\b" + re.escape(member) + r"\s*\(", body):
                    missing.append((ret, member, args, found[4]))
        if not missing:
            continue
        first = re.search(r"public:\n(?:[ \t]*\n)*([ \t]+)\S", body)
        indent = first[1] if first else "    "
        declarations = "".join(
            indent + "virtual {} {}({}){}{}\n".format(
                ret, member, args, " const" if const else "",
                " {}" if _header_defined(root, name, member, relative) else ";",
            )
            for ret, member, args, const in missing
        )
        at = text.index("public:", cls.start()) + len("public:\n")
        text = text[:at] + declarations + text[at:]
        end += len(declarations)
        changed = True
    if changed:
        path.write_text(text)
    return changed


ABSTRACT = re.compile(r"abstract class type '(?:class )?(?:[\w]+::)*(\w+)\b")


def _declare_overrides(root: Path, output: str, originals: dict[str, str]) -> bool:
    """Answer an 'abstract class' compile error by declaring the missing pure
    virtuals in the class's header, after reconciling its base interfaces'
    forward declarations against the recorded symbols (a parameter the
    provisional ox header put in the wrong namespace keeps the existing method
    from overriding). Each changed header's previous text is kept in originals
    for settling."""
    changed = False
    for cls in set(ABSTRACT.findall(output)):
        header = next(
            (
                h
                for ns in NAMESPACES
                if (h := _index(root, root / "src" / "daisy" / ns).headers.get(cls))
            ),
            None,
        )
        if header is None:
            continue
        before = (root / "src" / header).read_text()
        head = re.search(r"class\s+" + re.escape(cls) + r"\s*:\s*([^{;]+)\{", before)
        if head is None:
            continue
        for clause in head[1].split(","):
            interface = _interface_header(root, clause.strip().split()[-1].split("::")[-1])
            if interface is None or not (root / "src" / interface).exists():
                continue
            original = (root / "src" / interface).read_text()
            if reconcile_forwards(root, interface):
                originals.setdefault(interface, original)
                changed = True
        if declare_missing_overrides(root, header):
            originals.setdefault(header, before)
            changed = True
    return changed


def _declare_members(root: Path, output: str, originals: dict[str, str]) -> bool:
    """Declare in a provisional daisy class header each member function the unit
    defines but the header lacks, copying its declaration from Irrlicht's class in
    the Irrlicht order. The header's placeholder array keeps the class's size, so
    only function declarations are added. The header's text before the change goes
    into originals, to be settled with the other changes. Returns whether a header
    changed."""
    wanted: dict[str, set[str]] = {}
    for member, cls in MISSING_MEMBER.findall(output):
        wanted.setdefault(cls, set()).add(member)
    for cls, dtor in IMPLICIT_DESTRUCTOR.findall(output):
        wanted.setdefault(cls, set()).add(dtor)
    changed = False
    for cls, members in sorted(wanted.items()):
        name = cls.split("::")[-1]
        namespace = "/".join(cls.split("::")[:-1])
        original = _original(root, f"{name}.h")
        header = _provisional_header(root, namespace, name)
        if header is None or original is None:
            continue
        irrlicht = translate(original.read_text(encoding="latin-1"), "daisy")
        start = re.search(r"class " + name + r"\b[^;{]*\{", irrlicht)
        if start is None:
            continue
        end = _brace_end(irrlicht, start.end() - 1)
        body = irrlicht[start.end() : end]
        declarations = []
        for member in sorted(members):
            found = re.search(r"^[ \t]*((?:virtual\s+)?[^;{}\n]*?(?<![\w~])" + re.escape(member) + r"\s*\([^;{}]*\)\s*(?:const\s*)?;)", body, re.MULTILINE)
            if found is None:
                continue
            declarations.append((body.find(found[1]), found[1].strip()))
        before = header.read_text()
        if "Unrecovered[" in before and before.count(f"class {name}") == 1:  # not yet replaced: a replaced class keeps its stand-in under HARVEST_PORT too
            for inc in re.findall(r'^#include "(ox/[^"]+)"', before, re.MULTILINE):  # the interface it derives from is completed too
                ox_before = (root / "src" / inc).read_text() if (root / "src" / inc).exists() else ""
                ox_original = _original(root, Path(inc).name)
                if "Partial and provisional" in ox_before and ox_original is not None:
                    ox_text = qualify(root, _whole_class(root, ox_before, Path(inc).stem, translate(ox_original.read_text(encoding="latin-1"), "ox")), inc)
                    originals.setdefault(inc, ox_before)
                    (root / "src" / inc).write_text(ox_text)
            text = qualify(root, _whole_class(root, before, name, irrlicht), header.relative_to(root / "src").as_posix())
            declarations = []
        else:
            text = _with_base(root, header, name, irrlicht, before)
            present = text[text.find("class " + name) :]
            declarations = [
                d for _, d in sorted(declarations) if not re.search(r"(?<!\w)" + re.escape(d.split("(")[0].split()[-1]) + r"\s*\(", present)
            ]
        if not declarations and text == before:
            continue
        if declarations:
            lines = "".join(f"    {qualify(root, d)}\n" for d in declarations)
            at = text.index("public:", text.index("class " + name)) + len("public:\n")
            text = text[:at] + lines + text[at:]
        originals.setdefault(header.relative_to(root / "src").as_posix(), before)
        header.write_text(text)
        changed = True
    return changed


def _whole_class(root: Path, text: str, name: str, irrlicht: str) -> str:
    """The header with its class `name` replaced by Irrlicht's class of that name,
    with the names it uses qualified as Harvest declares them."""
    start = re.search(r"class " + re.escape(name) + r"\b[^;{]*\{", irrlicht)
    if start is None:
        return text
    end = _brace_end(irrlicht, start.end() - 1)
    body = irrlicht[start.start() : end + 2].expandtabs(4)  # Harvest indents and aligns with spaces
    stand_in = re.search(r"class " + re.escape(name) + r"\b[^;{]*\{", text)
    if stand_in is None:
        return text
    close = _brace_end(text, stand_in.end() - 1)
    # the port's stub implements the stand-in's declaration, and the matching build gets Irrlicht's class
    stand_in_text = text[stand_in.start() : close + 2]
    return text[: stand_in.start()] + f"#ifdef HARVEST_PORT\n{stand_in_text}\n#else\n{body}\n#endif" + text[close + 2 :]


def _brace_end(text: str, opening: int) -> int:
    """The index of the brace that closes the one at `opening`."""
    depth = 0
    for index in range(opening, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return index
    return len(text)


def _original(root: Path, name: str) -> Path | None:
    for directory in ("source/Irrlicht", "include"):
        if (root / IRRLICHT / directory / name).exists():
            return root / IRRLICHT / directory / name
    return None


def _adapted_companion(root: Path, source: Path, name: str) -> Path | None:
    """The header in src/ a unit includes as its own, or a stubbed header it
    includes, when an earlier seed translated it from Irrlicht and Irrlicht has it."""
    if _original(root, name) is None:
        return None
    found = [
        path
        for path in (root / "src").rglob(Path(name).name)
        if "// Adapted from Irrlicht 0.7 " in path.read_text(errors="replace")
        and (Path(name).stem == source.stem or "__builtin_trap();" in path.read_text(errors="replace"))
    ]
    return found[0] if len(found) == 1 else None


PORT_BLOCK = re.compile(r"^#ifdef HARVEST_PORT\n(class (\w+)\b.*?^\};)\n#else\n.*?^#endif\n?", re.MULTILINE | re.DOTALL)


def _keep_port_stand_ins(old: str, new: str) -> str:
    """The new translation of a header with each `#ifdef HARVEST_PORT` stand-in
    class of the old text kept around the new text's class of that name: the
    port's stub sources implement the stand-in's declarations, and translating the
    header again must not take them away."""
    for found in PORT_BLOCK.finditer(old):
        stand_in, name = found[1], found[2]
        if re.search(r"^#ifdef HARVEST_PORT\nclass " + name + r"\b", new, re.MULTILINE):
            continue
        start = re.search(r"^class " + re.escape(name) + r"\b[^;{]*\{", new, re.MULTILINE)
        if start is None:
            continue
        close = _brace_end(new, start.end() - 1)
        body = new[start.start() : close + 2]
        new = new[: start.start()] + f"#ifdef HARVEST_PORT\n{stand_in}\n#else\n{body}\n#endif" + new[close + 2 :]
    return new


def _translation(root: Path, original: Path, relative: str, origin: str) -> str:
    """The Irrlicht header translated for its place under src/, with its provenance."""
    ox = relative.startswith("ox/")
    translated = translate(original.read_text(encoding="latin-1"), "ox" if ox else "daisy")
    if relative.startswith("ox/core/"):
        translated = bare(translated, "ox::core::")
    where = f"{original.parent.relative_to(root / IRRLICHT).as_posix()}/{origin}"
    return _tidy(provenance(translated, where, "::".join(relative.split("/")[:-1]) if ox else "daisy"))


def _tidy(text: str) -> str:
    """A file's text with no blank lines after its last line, and one newline at the end."""
    return text.rstrip() + "\n"




def _headers(root: Path, source: Path, text: str, seen: set[str]) -> list[str]:
    """Translate each Irrlicht header the text includes that Harvest lacks, and the
    headers those include, to the path the Mac debug map records for it (the
    source's directory when it records none), and point the include there. An
    include of a header Harvest has under another directory is pointed at that
    header."""
    written = []
    for name in re.findall(r'^#include "([^"]+)"', text, flags=re.MULTILINE):
        companion = _adapted_companion(root, source, name)
        if companion is not None:  # the unit's own header, an earlier seed's translation, is translated again
            relative = companion.relative_to(root / "src").as_posix()
            if relative not in seen:
                seen.add(relative)
                translated = _keep_port_stand_ins(companion.read_text(), _translation(root, _original(root, name), relative, name))
                companion.write_text(translated)
                written.append(relative)
                written += _headers(root, companion, translated, seen)
            continue
        if _resolvable(root, source.parent, name):
            continue
        existing = sorted((root / "src" / "ox").rglob(Path(name).name)) or sorted((root / "src").rglob(Path(name).name))
        existing = [path for path in existing if not _seeded(root, path)]
        if len(existing) == 1:
            relative = existing[0].relative_to(root / "src").as_posix()
            source.write_text(source.read_text().replace(f'#include "{name}"', f'#include "{relative}"'))
            continue
        found = _destination(root, source, name)
        if found is None:
            continue
        header, origin = found
        relative = header.relative_to(root / "src").as_posix()
        if header.parent != source.parent or name != origin:
            source.write_text(source.read_text().replace(f'#include "{name}"', f'#include "{relative}"'))
        if relative in seen:
            continue
        seen.add(relative)
        translated = _translation(root, _original(root, origin), relative, origin)
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text(translated)
        written.append(relative)
        written += _headers(root, header, translated, seen)
    return written


def _register(root: Path, unit: str, placements: dict[str, int]) -> None:
    path = root / "config" / BUILD / "units.toml"
    text = path.read_text()
    block = f'["{unit}"]\n' + "".join(f'"{name}" = {address:#x}\n' for name, address in placements.items())
    pattern = re.compile(r'^\["' + re.escape(unit) + r'"\]\n(?:"[^"\n]+" = 0x[0-9a-f]+\n|#[^\n]*\n)*', re.MULTILINE)
    text = pattern.sub(block, text) if pattern.search(text) else text.rstrip("\n") + "\n\n" + block
    path.write_text(text)


def _unregister(root: Path, unit: str) -> None:
    """Drop a unit that does not compile, so the rest of the build still matches."""
    path = root / "config" / BUILD / "units.toml"
    pattern = re.compile(r'\n*^\["' + re.escape(unit) + r'"\]\n(?:"[^"\n]+" = 0x[0-9a-f]+\n|#[^\n]*\n)*', re.MULTILINE)
    path.write_text(pattern.sub("\n", path.read_text()))


def _match(root: Path, unit: str, learn: bool) -> str:
    command = ["uv", "--no-config", "run", "--locked", "hv", "match", *(["--learn"] if learn else []), unit]
    done = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    return done.stdout + done.stderr


def _place(root: Path, unit: str) -> dict[str, int]:
    """The .text address where the target holds a run of functions with the sizes
    of the object's .text functions, and the .bss address its static initializer
    passes to std::ios_base::Init."""
    slug = unit.replace("/", "__").removesuffix(".cpp")
    with open(root / "build" / "match" / BUILD / f"{slug}.o", "rb") as stream:
        elf = ELFFile(stream)
        text = elf.get_section_by_name(".text")
        index = next(i for i, s in enumerate(elf.iter_sections()) if s.name == ".text")
        ours = sorted(
            (s["st_value"], s["st_size"], s.name)
            for s in elf.get_section_by_name(".symtab").iter_symbols()
            if s["st_shndx"] == index and s["st_info"]["type"] == "STT_FUNC"
        )
        bss = elf.get_section_by_name(".bss")
        has_bss = bss is not None and bss["sh_size"] > 0
    if not ours or text["sh_size"] == 0:
        return {}
    extents = _extents(root)
    anchors = _anchors(root, slug)
    starts = [a for a, _ in extents]
    sizes = [s for _, s in extents]
    want = [size for _, size, _ in ours]
    best = None
    for i in range(len(extents) - len(want) + 1):
        score = sum(1 for k, size in enumerate(want) if sizes[i + k] == size)
        if score * 2 <= len(want):
            continue
        # ld lays out an object's .text before its inline copies' .text.* sections, so
        # the run belongs just before the first copy the matcher placed
        before = [a - starts[i] for a in anchors if a >= starts[i]]
        distance = min(before) if before else (min(abs(starts[i] - a) for a in anchors) + (1 << 40) if anchors else 0)
        key = (score, -distance)
        if best is None or key > best[0]:
            best = (key, starts[i])
    if best is None:
        return {}
    placements = {".text": best[1]}
    init = next((offset for offset, _, name in ours if name.startswith("_GLOBAL__I")), None)
    if has_bss and init is not None:
        address = _ios_init_object(root, best[1] + init)
        if address is not None:
            placements[".bss"] = address
    return placements


def _place_bss(root: Path, unit: str) -> dict[str, int]:
    """The .bss of a unit whose .text the matcher placed from a known symbol: the
    object the static initializer, at its offset in that .text, passes to
    std::ios_base::Init."""
    import json

    slug = unit.replace("/", "__").removesuffix(".cpp")
    sections = json.loads((root / "build" / "match" / BUILD / f"{slug}.json").read_text())["sections"]
    text = next((int(s["address"], 16) for s in sections if s["name"] == ".text" and s.get("address")), None)
    if text is None:
        return {}
    with open(root / "build" / "match" / BUILD / f"{slug}.o", "rb") as stream:
        elf = ELFFile(stream)
        index = next(i for i, s in enumerate(elf.iter_sections()) if s.name == ".text")
        inits = [
            s["st_value"]
            for s in elf.get_section_by_name(".symtab").iter_symbols()
            if s["st_shndx"] == index and s.name.startswith("_GLOBAL__I")
        ]
    if not inits:
        return {}
    address = _ios_init_object(root, text + inits[0])
    return {".bss": address} if address is not None else {}


def _rodata_reference(root: Path, unit: str) -> tuple[int, int, int, int] | None:
    """The first relocation in the object's .text that points into its .rodata, as
    (offset in .text, addend, start and size of the function holding it), or None."""
    return next(iter(_rodata_references(root, unit)), None)


def _rodata_references(root: Path, unit: str) -> list[tuple[int, int, int, int]]:
    """Every relocation in the object's .text that points into its .rodata, as
    (offset in .text, addend, start and size of the function holding it)."""
    slug = unit.replace("/", "__").removesuffix(".cpp")
    with open(root / "build" / "match" / BUILD / f"{slug}.o", "rb") as stream:
        elf = ELFFile(stream)
        names = [section.name for section in elf.iter_sections()]
        if ".rodata" not in names or ".rela.text" not in names:
            return []
        rodata = names.index(".rodata")
        text_index = names.index(".text")
        symbols = elf.get_section_by_name(".symtab")
        functions = sorted(
            (sym["st_value"], sym["st_size"])
            for sym in symbols.iter_symbols()
            if sym["st_shndx"] == text_index and sym["st_info"]["type"] == "STT_FUNC"
        )
        references = []
        for relocation in elf.get_section_by_name(".rela.text").iter_relocations():
            target = symbols.get_symbol(relocation["r_info_sym"])
            if target["st_shndx"] != rodata:
                continue
            for start, size in functions:
                if start <= relocation["r_offset"] < start + size:
                    references.append((relocation["r_offset"], relocation["r_addend"], start, size))
                    break
        return references


def _place_rodata(root: Path, unit: str) -> dict[str, int]:
    """The .rodata address a unit's jump table lives at, found through the target FDE
    of the same size as a function holding a table reference. Earlier functions in the
    unit may differ in size, so every .rodata reference is tried."""
    references = _rodata_references(root, unit)
    if not references:
        return {}
    anchors = _anchors(root, unit.replace("/", "__").removesuffix(".cpp"))
    extents = _extents(root)
    with open(root / "orig" / BUILD / "Harvest", "rb") as stream:
        elf = ELFFile(stream)
        text = elf.get_section_by_name(".text")
        rodata = elf.get_section_by_name(".rodata")
        for offset, addend, start, size in references:
            candidates = [(fde, fsize) for fde, fsize in extents if fsize == size]
            if anchors:
                candidates = [(fde, fsize) for fde, fsize in candidates if min(anchors) - 0x20000 <= fde <= max(anchors) + 0x20000]
            for fde, _ in sorted(candidates, key=lambda c: min((abs(c[0] - a) for a in anchors), default=0)):
                where = fde + (offset - start) - text["sh_addr"]
                value = int.from_bytes(text.data()[where : where + 4], "little", signed=True) - addend
                if rodata["sh_addr"] <= value < rodata["sh_addr"] + rodata["sh_size"]:
                    return {".rodata": value}
    return {}


def _extents(root: Path) -> list[tuple[int, int]]:
    script = (
        "from hv import extents\n"
        f"t = extents.load_target('{BUILD}')\n"
        "for a, (s, _) in sorted(t.function_extents().items()): print(a, s)\n"
    )
    done = subprocess.run(["uv", "--no-config", "run", "--locked", "python", "-c", script], cwd=root,
                          capture_output=True, text=True, check=True)  # fmt: skip
    return [tuple(map(int, line.split())) for line in done.stdout.splitlines()]


def _anchors(root: Path, slug: str) -> list[int]:
    """Target addresses of the unit's inline-copy code sections the matcher placed
    from known symbols."""
    import json

    report = root / "build" / "match" / BUILD / f"{slug}.json"
    if not report.exists():
        return []
    sections = json.loads(report.read_text())["sections"]
    return [
        int(s["address"], 16)
        for s in sections
        if s.get("address") and s["name"].startswith(".text.") and s.get("placement") != "explicit"
    ]


def _ios_init_object(root: Path, address: int) -> int | None:
    """The object the static initializer at `address` passes to std::ios_base::Init,
    or None when there is no such move there or its operand is not in the target's
    .bss: an address computed from a misplaced function (a stub stands in for a
    longer function) points at unrelated code whose immediate means nothing."""
    image = root / "orig" / BUILD / "Harvest"
    done = subprocess.run(
        ["objdump", "-d", f"--start-address={address:#x}", f"--stop-address={address + 32:#x}", str(image)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    found = re.search(r"mov\s+\$0x([0-9a-f]+),%edi", done.stdout)
    if found is None:
        return None
    value = int(found[1], 16)
    with open(image, "rb") as stream:
        bss = ELFFile(stream).get_section_by_name(".bss")
        inside = bss is not None and bss["sh_addr"] <= value < bss["sh_addr"] + bss["sh_size"]
    return value if inside else None


def _companion_header(root: Path, unit: str) -> str | None:
    """The header a unit includes under its own name, as a path under src/."""
    text = (root / "src" / unit).read_text()
    found = re.search(r'^#include "([^"]*' + re.escape(Path(unit).stem) + r'\.h)"', text, re.MULTILINE)
    if found is None:
        return None
    local = (Path(unit).parent / Path(found[1]).name).as_posix()
    if (root / "src" / local).exists():
        return local
    hits = [p.relative_to(root / "src").as_posix() for p in (root / "src").rglob(Path(found[1]).name)]
    return hits[0] if len(hits) == 1 else None


def missing_vtable_overrides(root: Path, header: str) -> dict[str, dict[str, str]]:
    """Members the Mac vtables credit to a class in `header` that the class does
    not declare, as {class: {member: symbol}}. A vtable slot naming a member of
    the class itself (`_ZN<class>len(member)` under the table's `_ZTVN<class>E`)
    is a virtual the class overrode; `declare_missing_overrides` cannot see it,
    since inheriting a non-pure virtual raises no compile error."""
    path, rows = root / "src" / header, root / "reference" / "1.18-mac-i386" / "vtables.csv"
    if not path.exists() or not rows.exists():
        return {}
    text = path.read_text()
    with rows.open(newline="") as stream:
        vtable_rows = list(csv.DictReader(stream))
    missing = {}
    for cls in re.finditer(r"class\s+(\w+)\s*:\s*[^{;]+\{", text):
        name = cls[1]
        body = text[cls.end() : _brace_end(text, cls.end() - 1)]
        for row in vtable_rows:
            vtable, symbol = row["vtable"], row["target"]
            if not (vtable.startswith("_ZTVN") and vtable.endswith(f"{len(name)}{name}E")):
                continue
            owner = vtable[5:-1]  # inside _ZTVN...E, ending with the class's name
            found = re.match("_ZN" + re.escape(owner) + r"(\d+)", symbol or "")
            if found is None:
                continue
            member = symbol[found.end() : found.end() + int(found[1])]
            if not re.search(r"\b" + re.escape(member) + r"\s*\(", body):
                missing.setdefault(name, {})[member] = symbol
    return missing


def _member_declaration(translation: str, cls: str, member: str) -> str | None:
    """The declaration for `cls::member` from a translated class body, with the
    comment lines above it, or None when the translation does not declare it."""
    start = re.search(r"class\s+" + re.escape(cls) + r"\s*:\s*[^{;]+\{", translation)
    if start is None:
        return None
    body = translation[start.end() : _brace_end(translation, start.end() - 1)]
    decl = re.search(
        r"^[ \t]*virtual\s+[^;{}\n]*?\b" + re.escape(member) + r"\s*\([^;{}\n]*\)\s*(?:const\s*)?[;{][^}\n]*\}?[ \t]*$",
        body,
        re.MULTILINE,
    )
    if decl is None:
        return None
    at = decl.start()
    while True:  # walk back over the comment lines directly above it
        head = body[:at].rstrip("\n")
        line = head[head.rfind("\n") + 1 :]
        if not line.lstrip().startswith("//"):
            break
        at = head.rfind("\n") + 1
    return body[at : decl.end()].strip("\n")


def _member_definition(translation: bytes, cls: str, member: str) -> bytes | None:
    """The `cls::member` function definition from a translated unit, with the
    comment lines above it, or None."""
    from concord.blockers import _definitions

    for key, node in _definitions(translation).items():
        if key.split("(")[0] != cls + "::" + member:
            continue
        start = node.start_byte
        head = translation[:start]
        while True:
            line = head.rstrip(b"\n").rpartition(b"\n")[2]
            if not line.strip().startswith(b"//"):
                break
            start -= len(line) + 1
            head = translation[:start]
        return translation[start : node.end_byte]
    return None


def _insert_declaration(text: str, cls: str, declaration: str) -> str | None:
    """The header with `declaration` added after the class's last virtual
    declaration, or at its end when it declares none."""
    cls_m = re.search(r"class\s+" + re.escape(cls) + r"\s*:\s*[^{;]+\{", text)
    if cls_m is None:
        return None
    end = _brace_end(text, cls_m.end() - 1)
    body = text[cls_m.end() : end]
    decls = list(re.finditer(r"^[ \t]*virtual\s+[^;{}\n]*;[ \t]*$", body, re.MULTILINE))
    at = cls_m.end() + (decls[-1].end() if decls else len(body))
    found = re.search(r"\n([ \t]+)virtual\b", body)
    indent = found[1] if found else "\t\t"
    block = "".join("\n" + indent + line.strip() for line in declaration.split("\n"))
    return text[:at] + "\n" + block + text[at:]


def _insert_definition(text: str, definition: bytes) -> bytes:
    """The unit with `definition` appended before the closing namespaces."""
    close = re.search(rb"\n\} // end namespace", text)
    at = close.start() + 1 if close else len(text)
    return text[:at] + definition.strip(b"\n") + b"\n\n" + text[at:]


def adapt_cache_iteration(definition: bytes, header: str, *, indexed: bool = False) -> bytes | None:
    """Translate a port map-value loop to its declared array-entry equivalent.
    Require one pointer member of the same value type; ambiguous layouts stay
    unsupported. The loop order, drop calls and clear call remain source-derived.
    """
    text = definition.decode()
    loops = list(re.finditer(r"std::map<std::string,\s*([\w:]+\s*\*)>::iterator\s+(\w+)\s*=\s*(\w+)\.begin\(\)", text))
    if len(loops) > 1:
        return None
    for loop in loops:
        value, iterator, container = loop.groups()
        array = re.search(r"ox::TArray<(\w+)>\s+" + re.escape(container) + r"\s*;", header)
        if array is None:
            return None
        entry = re.search(r"struct\s+" + re.escape(array[1]) + r"\s*\{", header)
        if entry is None:
            return None
        body = header[entry.end():_brace_end(header, entry.end() - 1)]
        value_name = value.replace('*', '').strip().split('::')[-1]
        members = re.findall(r"(?:[\w]+::)*" + re.escape(value_name) + r"\s*\*\s*(\w+)\s*;", body)
        if len(members) != 1:
            return None
        if indexed:
            tail = re.match(r";\s*" + re.escape(iterator) + r"\s*!=\s*" + re.escape(container) + r"\.end\(\);\s*\+\+" + re.escape(iterator), text[loop.end():])
            if tail is None:
                return None
            text = text[:loop.start()] + f"unsigned int {iterator} = 0; {iterator} < {container}.size(); ++{iterator}" + text[loop.end() + tail.end():]
            value_access = f"{container}[{iterator}].{members[0]}"
        else:
            text = text[:loop.start()] + f"ox::TArray<{array[1]}>::iterator {iterator} = {container}.begin()" + text[loop.end():]
            value_access = iterator + "->" + members[0]
        text = re.sub(r"\b" + re.escape(iterator) + r"->second\b", value_access, text)
    return text.encode()


def restore_vtable_overrides(harvest: Harvest, unit: str, apply: bool = False) -> list[str]:
    """Declare and define each member the Mac vtables credit to the unit's
    companion class that its header lacks, taking the declaration and body from
    the unit's Irrlicht translation. A candidate is kept only when it compiles,
    the member matches its target exactly, and no still-emitted function scores
    lower; an emitted copy the candidate no longer makes is reported, since the
    whole-build score shows whether another unit's copy keeps its extent."""
    root = harvest.root
    unit = harvest.source(unit)
    header = _companion_header(root, unit)
    if header is None:
        return []
    missing = missing_vtable_overrides(root, header)
    if not missing:
        return []
    htext = (root / "src" / header).read_text()
    utext = (root / "src" / unit).read_bytes()
    original_h = _original(root, Path(header).name)
    original_u = _original(root, Path(unit).name)
    if original_h is None or original_u is None:
        return []
    header_translation = translate(original_h.read_text(encoding="latin-1"))
    raw = translate(original_u.read_text(encoding="latin-1"))
    translation = qualify(root, name_helpers(raw, helper_taken(root, raw, unit)), unit).encode()
    baseline = harvest.evaluate_overlays({"baseline": (unit, {})})["baseline"]
    slug = harvest.slug(unit)
    exact_elsewhere = {
        row["symbol"]
        for report in harvest.reports.glob("*.json")
        if report.stem != slug
        for section in json.loads(report.read_text())["sections"]
        for row in section.get("functions", [])
        if row.get("exact")
    }
    applied, notes = [], []
    for cls, members in sorted(missing.items()):
        for member, symbol in sorted(members.items()):
            declaration = _member_declaration(header_translation, cls, member)
            definition = _member_definition(translation, cls, member)
            if declaration is None or definition is None:
                port = root / "port" / "src" / unit.removeprefix("daisy/")
                port_h = port.with_suffix('.h')
                if port.exists() and port_h.exists():
                    declaration = _member_declaration(translate(port_h.read_text()), cls, member)
                    body = _member_definition(translate(port.read_text()).encode(), cls, member)
                    definition = adapt_cache_iteration(body, htext, indexed=True) if body else None
            if declaration is None or definition is None:
                notes.append(f"{cls}::{member}: the translation has no source for it")
                continue
            new_h = _insert_declaration(htext, cls, declaration)
            new_u = _insert_definition(utext, definition)
            if new_h is None:
                continue
            overlays = {"src/" + header: new_h.encode(), "src/" + unit: new_u}
            symbols = (root / "reference/1.18-mac-i386/functions.csv").read_text()
            bases = re.search(r"class\s+" + re.escape(cls) + r"\s*:\s*([^{;]+)\{", htext)
            for clause in bases[1].split(',') if bases else []:
                interface = _interface_header(root, clause.strip().split()[-1].split('::')[-1])
                if interface:
                    before = (root / 'src' / interface).read_text()
                    after = reconciled_forwards(before, symbols)
                    if after != before:
                        overlays['src/' + interface] = after.encode()
            candidate = harvest.evaluate_overlays({"candidate": (unit, overlays)})["candidate"]
            verdict = candidate.verdict(symbol)
            emitted = {row["symbol"] for section in candidate.sections for row in section.get("functions", [])}
            lost = sorted(
                s for s in baseline.exact_functions() - candidate.exact_functions() - {symbol}
                if s in emitted or s not in exact_elsewhere
            )
            worse = [s for s, f in baseline.fuzzy.items() if s != symbol and s in emitted and candidate.fuzzy.get(s, 0.0) < f]
            if candidate.error or verdict is None or not verdict.row.get("exact") or lost or worse:
                notes.append(f"{cls}::{member}: not kept (exact={bool(verdict and verdict.row.get('exact'))}, lost={lost[:3]}, worse={worse[:3]})")
                continue
            if apply:
                for relative, text in overlays.items():
                    (root / relative).write_bytes(text)
                htext, utext = new_h, new_u
            applied.append(f"{cls}::{member}")
    return applied + notes


def _place_called_sections(root: Path, unit: str) -> dict[str, int]:
    """Target addresses of unplaced .text.<symbol> sections that a placed section
    references with a candidate address, or that immediately follow a placed
    .text.* section, when the target FDE has the section's size."""
    import json

    slug = unit.replace("/", "__").removesuffix(".cpp")
    report = root / "build" / "match" / BUILD / f"{slug}.json"
    obj = root / "build" / "match" / BUILD / f"{slug}.o"
    if not report.exists() or not obj.exists():
        return {}
    data = json.loads(report.read_text())
    unplaced = set(data.get("unplaced_sections", []))
    if not unplaced:
        return {}
    placed_at = {s["name"]: int(s["address"], 16) for s in data.get("sections", []) if s.get("address")}
    candidates = [
        (f".text.{ref['symbol']}", int(ref["candidate"], 16))
        for sec in data.get("sections", [])
        for ref in sec.get("bad_references", [])
        if ref.get("symbol") and ref.get("candidate") and f".text.{ref['symbol']}" in unplaced
    ]
    with open(obj, "rb") as stream:
        order = [(s.name, s["sh_size"]) for s in ELFFile(stream).iter_sections() if s.name.startswith(".text.")]
    sizes = {name: size for name, size in order if name in unplaced}
    extents = _extents(root)
    fde_sizes = dict(extents)
    next_fde = {extents[i][0]: extents[i + 1] for i in range(len(extents) - 1)}
    found = {name: dest for name, dest in candidates if fde_sizes.get(dest) == sizes.get(name)}
    for i in range(len(order) - 1):
        prev_name, prev_size = order[i]
        curr_name, curr_size = order[i + 1]
        if prev_name in placed_at and curr_name in unplaced and prev_name != curr_name:
            following = next_fde.get(placed_at[prev_name])
            if following and following[1] == curr_size and 0 <= following[0] - (placed_at[prev_name] + prev_size) < 16:
                found.setdefault(curr_name, following[0])
    return found
