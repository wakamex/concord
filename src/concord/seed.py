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

from concord.harvest import BUILD
from concord.irrlicht import translate

IRRLICHT = Path("third_party/irrlicht-0.7")


@dataclass
class SeedResult:
    unit: str
    exact: int = 0
    functions: int = 0
    placements: dict[str, int] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)
    error: str = ""


def seed(root: Path, unit: str) -> SeedResult:
    """Seed `unit` (a path under src/, such as daisy/video/Software/CTRFlat.cpp)."""
    result = SeedResult(unit)
    irrlicht = root / IRRLICHT / "source" / "Irrlicht" / Path(unit).name
    if not irrlicht.exists():
        result.error = f"no Irrlicht source {irrlicht.name}"
        return result
    target = root / "src" / unit
    text = translate(irrlicht.read_text(encoding="latin-1"))
    if _has_static_initializer(root, unit):
        text = re.sub(r"^#include", "#include <iostream>\n#include", text, count=1, flags=re.MULTILINE)
    target.write_text(text)
    result.written.append(unit)
    result.written += _color_helpers(root, text)
    result.written += _headers(root, target, text, set())
    _register(root, unit, {})
    output = _match(root, unit, learn=False)
    if "error:" in output or "returned non-zero" in output:
        result.error = "\n".join(line for line in output.splitlines() if "error" in line)[:3000]
        return result
    if ".text: not placed" in output:
        result.placements = _place(root, unit)
        if result.placements:
            _register(root, unit, result.placements)
    for _ in range(2):  # a learned address can let the next run learn another
        output = _match(root, unit, learn=True)
    found = re.search(r"functions (\d+)/(\d+)", output)
    if found:
        result.exact, result.functions = int(found[1]), int(found[2])
    return result


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


def _has_static_initializer(root: Path, unit: str) -> bool:
    with open(root / "reference" / "1.18-mac-i386" / "functions.csv") as stream:
        return any(row["unit"] == unit and row["symbol"].startswith("_GLOBAL__I") for row in csv.DictReader(stream))


def _resolvable(root: Path, directory: Path, name: str) -> bool:
    return any((base / name).exists() for base in (directory, root / "src", root / "src" / "HarvestFull"))


def _headers(root: Path, source: Path, text: str, seen: set[str]) -> list[str]:
    """Translate each Irrlicht header the text includes that Harvest lacks into the
    source's directory, and the headers those include, and point an include of a
    header Harvest has under another directory at that header."""
    written = []
    for name in re.findall(r'^#include "([^"]+)"', text, flags=re.MULTILINE):
        if name in seen or _resolvable(root, source.parent, name):
            continue
        seen.add(name)
        existing = [p for p in (root / "src").rglob(name)]
        if len(existing) == 1:
            relative = existing[0].relative_to(root / "src").as_posix()
            source.write_text(source.read_text().replace(f'#include "{name}"', f'#include "{relative}"'))
            continue
        for directory in ("source/Irrlicht", "include"):
            original = root / IRRLICHT / directory / name
            if original.exists():
                header = source.parent / name
                translated = translate(original.read_text(encoding="latin-1"))
                header.write_text(translated)
                written.append(header.relative_to(root / "src").as_posix())
                written += _headers(root, header, translated, seen)
                break
    return written


def _register(root: Path, unit: str, placements: dict[str, int]) -> None:
    path = root / "config" / BUILD / "units.toml"
    text = path.read_text()
    block = f'["{unit}"]\n' + "".join(f'"{name}" = {address:#x}\n' for name, address in placements.items())
    pattern = re.compile(r'^\["' + re.escape(unit) + r'"\]\n(?:"[^"\n]+" = 0x[0-9a-f]+\n|#[^\n]*\n)*', re.MULTILINE)
    text = pattern.sub(block, text) if pattern.search(text) else text.rstrip("\n") + "\n\n" + block
    path.write_text(text)


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
    if has_bss and ours[0][2].startswith("_GLOBAL__I"):
        address = _ios_init_object(root, best[1])
        if address is not None:
            placements[".bss"] = address
    return placements


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
