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
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from elftools.elf.elffile import ELFFile

from concord.complete import TYPES, complete, missing_members, settle
from concord.harvest import BUILD, Harvest
from concord.irrlicht import HEADERS, bare, translate

IRRLICHT = Path("third_party/irrlicht-0.7")


@dataclass
class SeedResult:
    unit: str
    exact: int = 0
    functions: int = 0
    port_conflicts: list[str] = field(default_factory=list)  # functions port/src also defines
    placements: dict[str, int] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)
    error: str = ""


def seed(root: Path, unit: str, complete_types: bool = False, allowed: frozenset[str] = frozenset()) -> SeedResult:
    """Seed `unit` (a path under src/, such as daisy/video/Software/CTRFlat.cpp)."""
    result = SeedResult(unit)
    irrlicht = root / IRRLICHT / "source" / "Irrlicht" / Path(unit).name
    if not irrlicht.exists():
        result.error = f"no Irrlicht source {irrlicht.name}"
        return result
    target = root / "src" / unit
    target.parent.mkdir(parents=True, exist_ok=True)
    text = provenance(translate(irrlicht.read_text(encoding="latin-1")), f"source/Irrlicht/{irrlicht.name}")
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
    for _ in range(8):  # each round can expose errors the previous ones hid
        progressed = _include_incomplete(root, target, output)
        missing = (missing_members(output) - tried) if complete_types else set()
        failed = set()
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
        if not progressed:
            tried |= failed  # else tried again next round: it may call a member this round added
        if not progressed:
            break
        output = _match(root, unit, learn=False)
    unsettled = []
    for header, text in sorted(originals.items()):  # one at a time: each has its own includers
        if settle(Harvest(root), {header: text}, allowed):
            result.written.append(header)
        else:
            (root / "src" / header).write_text(text)
            unsettled.append(header)
    if unsettled:
        result.error = "completing " + ", ".join(unsettled) + " makes functions in other units worse"
        _unregister(root, unit)
        return result
    if "error:" in output or "returned non-zero" in output:
        result.error = "\n".join(line for line in output.splitlines() if "error" in line)[:3000]
        _unregister(root, unit)
        return result
    if ".text: not placed" in output:
        result.placements = _place(root, unit)
    elif ".bss: not placed" in output:
        result.placements = _place_bss(root, unit)
    if result.placements:
        _register(root, unit, result.placements)
    for _ in range(2):  # a learned address can let the next run learn another
        output = _match(root, unit, learn=True)
    result.port_conflicts = port_conflicts(root, unit)
    found = re.search(r"functions (\d+)/(\d+)", output)
    if found:
        result.exact, result.functions = int(found[1]), int(found[2])
    else:
        result.error = output.strip().splitlines()[-1] if output.strip() else "hv match printed no result"
    return result


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
        for header in sorted(directory.rglob("*.h") if recursive else directory.glob("*.h")):
            text = header.read_text(errors="replace")
            index.names |= {found[1] or found[2] for found in DECLARATION.finditer(text)}
            for found in DEFINITION.finditer(text):
                name = found[1] or found[2]
                index.headers.setdefault(name, header.relative_to(root / "src").as_posix())
                if found[3]:
                    enumerators = set(re.findall(r"^\s*(\w+)", found[3], re.MULTILINE))
                    index.enumerators |= enumerators
                    for enumerator in enumerators:
                        index.headers.setdefault(enumerator, header.relative_to(root / "src").as_posix())
            for constant in CONSTANT.findall(text):
                index.enumerators.add(constant)
                index.headers.setdefault(constant, header.relative_to(root / "src").as_posix())
        _indexes[(directory, recursive)] = index
    return _indexes[(directory, recursive)]


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
    words = set(re.findall(r"\b[A-Z]\w+\b", text))
    for ns in NAMESPACES:
        index = _index(root, root / "src" / "ox" / ns)
        for name in sorted(words & set(index.headers)):
            qualified = re.search(r"\box::" + ns + "::" + name + r"\b", text)
            if (ns in used or qualified or (inside and ns in opened)) and index.headers[name] != own and f'#include "{index.headers[name]}"' not in text:
                needed.append(f'#include "{index.headers[name]}"\n')
    top = _index(root, root / "src" / "ox", recursive=False)
    for name in sorted(words & set(top.headers)):
        if re.search(r"\box::" + name + r"\b", text) and f'#include "{top.headers[name]}"' not in text:
            needed.append(f'#include "{top.headers[name]}"\n')
    if needed:
        text = re.sub(r"^(#include [^\n]*\n)", lambda m: m[1] + "".join(dict.fromkeys(needed)), text, count=1, flags=re.MULTILINE)
    return text


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


INCOMPLETE = re.compile(r"invalid use of incomplete type '(?:const )?(?:struct|class) ox::(\w+)::(\w+)'")


def _include_incomplete(root: Path, source: Path, output: str) -> bool:
    """Include in the unit the header of each ox type the compiler found incomplete,
    which the unit uses through a pointer some other header only forward-declares."""
    text = source.read_text()
    added = []
    for ns, name in sorted(set(INCOMPLETE.findall(output))):
        header = _index(root, root / "src" / "ox" / ns).headers.get(name)
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


def _original(root: Path, name: str) -> Path | None:
    for directory in ("source/Irrlicht", "include"):
        if (root / IRRLICHT / directory / name).exists():
            return root / IRRLICHT / directory / name
    return None


def _headers(root: Path, source: Path, text: str, seen: set[str]) -> list[str]:
    """Translate each Irrlicht header the text includes that Harvest lacks, and the
    headers those include, to the path the Mac debug map records for it (the
    source's directory when it records none), and point the include there. An
    include of a header Harvest has under another directory is pointed at that
    header."""
    written = []
    for name in re.findall(r'^#include "([^"]+)"', text, flags=re.MULTILINE):
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
        original = _original(root, origin)
        ox = relative.startswith("ox/")
        translated = translate(original.read_text(encoding="latin-1"), "ox" if ox else "daisy")
        if relative.startswith("ox/core/"):
            translated = bare(translated, "ox::core::")
        where = f"{original.parent.relative_to(root / IRRLICHT).as_posix()}/{origin}"
        translated = provenance(translated, where, "::".join(relative.split("/")[:-1]) if ox else "daisy")
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
    done = subprocess.run(
        ["objdump", "-d", f"--start-address={address:#x}", f"--stop-address={address + 16:#x}",
         str(root / "orig" / BUILD / "Harvest")],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    found = re.search(r"mov\s+\$0x([0-9a-f]+),%edi", done.stdout)
    return int(found[1], 16) if found else None
