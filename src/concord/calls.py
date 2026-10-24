"""Learn callee addresses from aligned calls in already placed callers.

This supplements Harvest's exact-function learning. Candidate addresses need
agreement at every aligned call site, a target extent of the compiled size,
and no competing symbol claim. Source and section placements are unchanged.
"""

from __future__ import annotations

import csv
import difflib
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import capstone
from capstone import x86
from elftools.elf.elffile import ELFFile

from concord.diff import ObjectFunction, disassemble, shape
from concord.harvest import BUILD
from concord.seed import _extents


@dataclass(frozen=True)
class CallSite:
    symbol: str
    address: int
    unit: str
    caller: str
    offset: int
    target_offset: int


def aligned_calls(target: ObjectFunction, candidate: ObjectFunction, address: int, references: dict[int, str], unit: str) -> list[CallSite]:
    """Calls in matching instruction runs with at least two context instructions.

    The runs use the diff's instruction shapes. A run must be unique in each
    function, and context may not cross a branch or return. Only a five-byte
    direct call with a relocation at its displacement can supply an address.
    """
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    raw_target = {i.address: i for i in md.disasm(target.code, 0)}
    raw_candidate = {i.address: i for i in md.disasm(candidate.code, 0)}

    def instructions(function, raw):
        return [replace(i, operands=(("sym",),)) if i.is_call and raw[i.offset].bytes[:1] == b"\xe8" else i
                for i in disassemble(function) if i.mnemonic != "nop"]

    t, c = instructions(target, raw_target), instructions(candidate, raw_candidate)
    ts, cs = [shape(i) for i in t], [shape(i) for i in c]
    sites = []
    for block in difflib.SequenceMatcher(None, ts, cs, autojunk=False).get_matching_blocks():
        for j in range(block.size):
            a, b = t[block.a + j], c[block.b + j]
            symbol = references.get(b.offset + 1)
            if not symbol or not a.is_call or not b.is_call:
                continue
            ta, ca = raw_target[a.offset], raw_candidate[b.offset]
            if ta.size != 5 or ca.size != 5 or ta.bytes[0] != 0xE8 or ca.bytes[0] != 0xE8:
                continue
            if len(ta.operands) != 1 or ta.operands[0].type != x86.X86_OP_IMM:
                continue
            left, right = j, j + 1
            while left > 0 and not t[block.a + left - 1].mnemonic.startswith(("j", "ret")):
                left -= 1
            while right < block.size and not t[block.a + right - 1].mnemonic.startswith(("j", "ret")):
                right += 1
            context = ts[block.a + left:block.a + right]
            if sum(token[0] != "call" for token in context) < 2:
                continue
            if any(sum(seq[k:k + len(context)] == context for k in range(len(seq) - len(context) + 1)) != 1 for seq in (ts, cs)):
                continue
            sites.append(CallSite(symbol, address + ta.operands[0].imm, unit, candidate.name, b.offset, a.offset))
    return sites


def proven(sites: list[CallSite], sizes: dict[str, set[int]], extents: dict[int, int], known: dict[str, int]) -> dict[str, tuple[int, str]]:
    """Keep unanimous, size-checked, unclaimed addresses; reject competing new claims."""
    grouped = defaultdict(list)
    for site in sites:
        grouped[site.symbol].append(site)
    agreed = {name: rows[0].address for name, rows in grouped.items() if len({s.address for s in rows}) == 1}
    claims = Counter(agreed.values())
    claimed = set(known.values())
    found = {}
    for name, address in agreed.items():
        if name in known or address in claimed or claims[address] != 1 or sizes.get(name) != {extents.get(address)}:
            continue
        site = grouped[name][0]
        found[name] = (address, f"aligned-call:{site.unit}:{site.caller}+{site.offset:#x}=>+{site.target_offset:#x}")
    return found


def object_sites(path: Path, target: bytes, base: int, extents: dict[int, int], known: dict[str, int], unit: str) -> tuple[list[CallSite], dict[str, set[int]]]:
    """Read real object relocations and compare only callers with named target addresses."""
    sites, sizes = [], defaultdict(set)
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        sections = list(elf.iter_sections())
        symbols = list(elf.get_section_by_name(".symtab").iter_symbols())
        relocations = defaultdict(dict)
        fields = defaultdict(set)
        for section in sections:
            if section["sh_type"] != "SHT_RELA":
                continue
            table = list(sections[section["sh_link"]].iter_symbols())
            for rel in section.iter_relocations():
                fields[section["sh_info"]].add(rel["r_offset"])
                if rel["r_info_type"] in (2, 4) and rel["r_addend"] == -4:
                    relocations[section["sh_info"]][rel["r_offset"]] = table[rel["r_info_sym"]].name
        for symbol in symbols:
            index, start, size = symbol["st_shndx"], symbol["st_value"], symbol["st_size"]
            if symbol["st_info"]["type"] != "STT_FUNC" or not isinstance(index, int) or not size:
                continue
            sizes[symbol.name].add(size)
            address = known.get(symbol.name)
            extent = extents.get(address)
            if address is None or extent is None or not 0 <= address - base <= len(target) - extent:
                continue
            code = sections[index].data()[start:start + size]
            references = {offset - start: name for offset, name in relocations[index].items() if start <= offset < start + size}
            if not references:
                continue
            candidate = ObjectFunction(symbol.name, sections[index].name, start, code, frozenset(f - start for f in fields[index] if start <= f < start + size))
            original = ObjectFunction(symbol.name, ".text", address, target[address - base:address - base + extent], frozenset())
            sites.extend(aligned_calls(original, candidate, address, references, unit))
    return sites, dict(sizes)


def discover(root: Path, units: list[str]) -> tuple[list[CallSite], dict[str, tuple[int, str]]]:
    with (root / "config" / BUILD / "symbols.tsv").open() as stream:
        known = {row["symbol"]: int(row["address"], 16) for row in csv.DictReader(stream, delimiter="\t")}
    extents = dict(_extents(root))
    with (root / "orig" / BUILD / "Harvest").open("rb") as stream:
        section = ELFFile(stream).get_section_by_name(".text")
        target, base = section.data(), section["sh_addr"]
    directory = root / "build" / "match" / BUILD
    selected = {u.removesuffix(".cpp").replace("/", "__") for u in units}
    reports = sorted(directory.glob("*.json"))
    missing = selected - {p.stem for p in reports}
    if missing:
        raise FileNotFoundError(f"missing match reports: {', '.join(sorted(missing))}")
    sites, sizes = [], defaultdict(set)
    allowed = set()
    for report in reports:
        data = json.loads(report.read_text())
        if data.get("object_sha256") != hashlib.sha256(report.with_suffix(".o").read_bytes()).hexdigest():
            raise ValueError(f"{report}: object differs from its match report; run hv match")
        found, compiled = object_sites(report.with_suffix(".o"), target, base, extents, known, data["unit"])
        sites.extend(found)
        for name, values in compiled.items():
            sizes[name].update(values)
            if not selected or report.stem in selected:
                allowed.add(name)
    return sites, {name: row for name, row in proven(sites, dict(sizes), extents, known).items() if name in allowed}


def input_hashes(root: Path) -> dict[str, str]:
    """Record the image, symbol claims, extents and object/report inputs for an audit."""
    files = [root / "orig" / BUILD / "Harvest", *(root / "config" / BUILD).glob("*.tsv")]
    for report in (root / "build" / "match" / BUILD).glob("*.json"):
        files.extend((report, report.with_suffix(".o")))
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def apply(root: Path, learned: dict[str, tuple[int, str]]) -> None:
    """Use Harvest's symbol writer, rechecking existing claims before saving."""
    if not learned:
        return
    script = """import json, sys
from hv import extents, symbols
from hv.cli import add_learned
build = sys.argv[1]
learned = {name: tuple(row) for name, row in json.load(sys.stdin).items()}
known = symbols.by_name(symbols.load(build))
if any(name in known or row[0] in known.values() for name, row in learned.items()):
    raise ValueError('symbol claims changed during discovery')
add_learned(build, learned, extents.load_target(build))
"""
    subprocess.run(["uv", "--no-config", "run", "--locked", "python", "-c", script, BUILD], cwd=root, input=json.dumps(learned), text=True, check=True, capture_output=True)
