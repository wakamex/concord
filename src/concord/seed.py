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
from concord.irrlicht import HEADERS, bare, translate
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
    for written in result.written:
        path = root / "src" / written
        path.write_text(qualify(root, path.read_text(), written))
    result.written += _color_helpers(root, target.read_text())
    _register(root, unit, {})
    output = _match(root, unit, learn=False)
    tried: set[tuple[str, str]] = set()
    # Completing ox headers is opt-in: each member added to SColor.h, CRect.h or
    # CVector3d.h shifts the declaration numbering of every unit including it, and
    # functions sensitive to it (concord phase) stop matching, as
    # CSpritePackage::removeAnimationState did. A completion stays only if settle
    # finds those units no worse.
    originals: dict[str, str] = {}
    names_tried: set[str] = set()
    for _ in range(8):  # each round can expose errors the previous ones hid
        progressed = _include_incomplete(root, target, output)
        missing = (missing_members(output) - tried) if complete_types else set()
        failed = set()
        if complete_types and _declare_members(root, output, originals):
            progressed = True
        for ox_type, member in sorted(missing):
            # one member at a time: one that calls an Irrlicht helper Harvest lacks
            # would break every unit including the header, and is left out
            header = TYPES[ox_type][0]
            before = (root / "src" / header).read_text()
            if not complete(root, {(ox_type, member)}):
                tried.add((ox_type, member))
                continue
            if not _compiles(root, unit, header) and not (_bring_includes(root, ox_type) and _compiles(root, unit, header)):
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
    for _ in range(2):  # a learned address can let the next run learn another
        output = _match(root, unit, learn=True)
        if "unmapped or truncated range:" in output and ".rodata" not in result.placements:
            # a jump table's function laid out past its FDE, so its table is read from the target's code
            rodata = _place_rodata(root, unit)
            if rodata:
                result.placements.update(rodata)
                _register(root, unit, result.placements)
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
    """re.sub outside preprocessor lines and string literals."""
    lines = []
    for line in text.split("\n"):
        if not line.lstrip().startswith("#"):
            parts = line.split('"')
            parts[::2] = [re.sub(pattern, replacement, part) for part in parts[::2]]
            line = '"'.join(parts)
        lines.append(line)
    return "\n".join(lines)


def qualify(root: Path, text: str, own: str = "") -> str:
    """Point Irrlicht names at Harvest's ox declarations. `video::SMaterial` names
    ox::video::SMaterial when Harvest declares it there and daisy does not; an
    enumerator Harvest declares in another ox namespace, such as ELL_ERROR in
    ox::event, is qualified; each daisy namespace block of the text gets a
    using-directive for its ox counterpart, so an unqualified base class such as
    ISceneNode resolves; and the header defining each ox type the text names is
    included, since Harvest's versions of Irrlicht headers often only
    forward-declare them. None of this changes the generated code or the mangled
    names of what the text defines. A file of ox itself (`own`, its path under
    src/) needs no using-directive and does not include itself."""
    inside = own.startswith("ox/")
    used = set()
    opened = {ns for ns in NAMESPACES if re.search(r"namespace " + ns + r"\s*\{", text)}
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
    if "ox::algo::" in text and not _includes(text, "ox/algo/CArrayFunctions.h"):  # ox::algo::sort is declared there
        needed.append('#include "ox/algo/CArrayFunctions.h"\n')
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
    return text


def _includes(text: str, header: str) -> bool:
    """Whether the text includes the header, by its path under src/ or by file name
    alone, as a file beside it does."""
    return bool(re.search(r'^#include "(?:[^"\n]*/)?' + re.escape(Path(header).name) + r'"', text, re.MULTILINE))


COLOR_PACKING = Path("src/ox/video/ColorPacking.h")


def _color_helpers(root: Path, text: str) -> list[str]:
    """Add to ox/video/ColorPacking.h each of Irrlicht's packed 16-bit color helpers
    (the free inline functions of include/SColor.h, such as getRed(s16)) that the
    text calls and the header lacks. ColorPacking.h is the header Harvest keeps
    them in, apart from SColor.h, so that adding one changes no other unit."""
    header = root / COLOR_PACKING
    current = header.read_text()
    irrlicht = translate((root / IRRLICHT / "include" / "SColor.h").read_text(encoding="latin-1"))
    added = []
    for name in sorted(set(re.findall(r"ox::video::(\w+)\(", text))):
        if re.search(r"\b" + name + r"\(", current):
            continue
        found = re.search(r"\tinline (\w+) " + name + r"\((short|int) (\w+)\)\s*\{(.*?)\}", irrlicht, re.DOTALL)
        if found is None:
            continue
        body = " ".join(found[4].split())
        added.append(f"inline {found[1]} {name}({found[2]} {found[3]})\n{{\n    {body}\n}}\n\n")
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
    host = next((p.name.removesuffix(".json") for p in sorted((root / "build" / "match" / BUILD).glob("*.json"))), None)
    if host is None:
        return False
    try:
        harvest = Harvest(root)
        evaluation = harvest.evaluate(harvest.source(host), {"probe": f'#include "{header}"\n'.encode()})["probe"]
    except (OSError, RuntimeError, KeyError):
        return False
    return not evaluation.error


def _compiles(root: Path, unit: str, header: str) -> bool:
    """Whether a file including the header and explicitly instantiating its class
    (which compiles every member of a template) compiles with the unit's flags."""
    ox_type = next(t for t, (h, _, _) in TYPES.items() if h == header)
    namespace = header.split("/")[1]
    probe = f'#include "{header}"\n'
    text = (root / "src" / header).read_text()
    if re.search(r"template\s*<\s*class \w+\s*>\s*(class|struct) " + ox_type + r"\b", text):
        probe += f"template class ox::{namespace}::{ox_type}<float>;\n"
    # compile as a unit the last hv match built, since the one being seeded may not build yet
    host = next((p.name.removesuffix(".json") for p in sorted((root / "build" / "match" / BUILD).glob("*.json"))), None)
    if host is None:
        return False
    try:
        harvest = Harvest(root)
        evaluation = harvest.evaluate(harvest.source(host), {"probe": probe.encode()})["probe"]
    except (OSError, RuntimeError, KeyError):
        return False
    return not evaluation.error


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
    done = subprocess.run(["zig", "build", "harvest"], cwd=root / "port", capture_output=True, text=True, check=False)
    found: dict[str, list[str]] = {}
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
        if "Unrecovered[" in before and "#ifdef HARVEST_PORT" not in before:  # the placeholder hides fields and nested types the unit uses, so the class is replaced whole
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
    return text[: stand_in.start()] + body + text[close + 2 :]


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
    """The header in src/ a unit includes as its own, when an earlier seed translated
    it from Irrlicht (it carries the origin comment) and Irrlicht has it."""
    if Path(name).stem != source.stem or _original(root, name) is None:
        return None
    found = [path for path in (root / "src").rglob(Path(name).name) if "// Adapted from Irrlicht 0.7 " in path.read_text(errors="replace")]
    return found[0] if len(found) == 1 else None


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
                translated = _translation(root, _original(root, name), relative, name)
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
    """The relocation in the object's .text that points into its .rodata, as
    (offset in .text, addend, start and size of the function holding it), or None."""
    slug = unit.replace("/", "__").removesuffix(".cpp")
    with open(root / "build" / "match" / BUILD / f"{slug}.o", "rb") as stream:
        elf = ELFFile(stream)
        names = [section.name for section in elf.iter_sections()]
        if ".rodata" not in names or ".rela.text" not in names:
            return None
        rodata = names.index(".rodata")
        text_index = names.index(".text")
        symbols = elf.get_section_by_name(".symtab")
        functions = sorted(
            (sym["st_value"], sym["st_size"])
            for sym in symbols.iter_symbols()
            if sym["st_shndx"] == text_index and sym["st_info"]["type"] == "STT_FUNC"
        )
        for relocation in elf.get_section_by_name(".rela.text").iter_relocations():
            target = symbols.get_symbol(relocation["r_info_sym"])
            if target["st_shndx"] != rodata:
                continue
            for start, size in functions:
                if start <= relocation["r_offset"] < start + size:
                    return relocation["r_offset"], relocation["r_addend"], start, size
    return None


def _place_rodata(root: Path, unit: str) -> dict[str, int]:
    """The .rodata address a unit's jump table lives at, found through the target FDE
    of the same size as the function holding the table's reference. A stub before the
    function can move the FDE the matcher lays out, so the table is read from the
    target's own code, where the reference is, and the address is checked against the
    target's .rodata."""
    found = _rodata_reference(root, unit)
    if found is None:
        return {}
    offset, addend, start, size = found
    anchors = _anchors(root, unit.replace("/", "__").removesuffix(".cpp"))
    candidates = [(fde, fsize) for fde, fsize in _extents(root) if fsize == size]
    if anchors:
        candidates = [(fde, fsize) for fde, fsize in candidates if min(anchors) - 0x20000 <= fde <= max(anchors) + 0x20000]
    with open(root / "orig" / BUILD / "Harvest", "rb") as stream:
        elf = ELFFile(stream)
        text = elf.get_section_by_name(".text")
        rodata = elf.get_section_by_name(".rodata")
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
        ["objdump", "-d", f"--start-address={address:#x}", f"--stop-address={address + 16:#x}", str(image)],
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