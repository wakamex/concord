"""Learn callee addresses from aligned calls in already placed callers.

This supplements Harvest's exact-function learning. Candidate addresses need
agreement at every aligned call site, a target extent of the compiled size,
and no competing symbol claim. Source and section placements are unchanged.
"""

from __future__ import annotations

import bisect
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
    proof: str = "named"
    target_caller: int | None = None


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


def window_calls(candidate: ObjectFunction, references: dict[int, str], target: bytes, base: int,
                 extents: dict[int, int], unit: str) -> list[CallSite]:
    """Find call blocks unique in the complete target image, with all physical
    registers and non-relocation bytes exact. Require 32 fixed bytes and eight
    non-padding instructions, a real target function and instruction boundary.
    """
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    blocks, block = [], []
    for instruction in md.disasm(candidate.code, 0):
        if instruction.mnemonic.startswith(('j', 'ret')):
            if block:
                blocks.append(block)
            block = []
        else:
            block.append(instruction)
    if block:
        blocks.append(block)
    starts, sites = sorted(extents), []
    for block in blocks:
        calls = [i for i in block if i.mnemonic == 'call' and i.bytes[:1] == b'\xe8' and i.address + 1 in references]
        if not calls or sum(i.mnemonic != 'nop' for i in block) < 8:
            continue
        start, end = block[0].address, block[-1].address + block[-1].size
        code = candidate.code[start:end]
        masked = {i - start for field in candidate.relocations for i in range(field, field + 4) if start <= i < end}
        runs, left = [], 0
        for right in range(len(code) + 1):
            if right == len(code) or right in masked:
                if right > left:
                    runs.append((left, code[left:right]))
                left = right + 1
        if len(code) - len(masked) < 32:
            continue
        anchor, needle = max(runs, key=lambda r: len(r[1]))
        if len(needle) < 8:
            continue
        matches, pos = [], target.find(needle)
        while pos >= 0:
            offset = pos - anchor
            if 0 <= offset <= len(target) - len(code) and all(i in masked or a == b for i, (a, b) in enumerate(zip(code, target[offset:offset + len(code)], strict=True))):
                matches.append(offset)
                if len(matches) > 1:
                    break
            pos = target.find(needle, pos + 1)
        if len(matches) != 1:
            continue
        location = base + matches[0]
        owner_index = bisect.bisect_right(starts, location) - 1
        if owner_index < 0:
            continue
        owner = starts[owner_index]
        if owner < base or owner + extents[owner] > base + len(target) or location + len(code) > owner + extents[owner]:
            continue
        instructions = {i.address: i for i in md.disasm(target[owner - base:owner - base + extents[owner]], owner)}
        if location not in instructions:
            continue
        for call in calls:
            address = location + call.address - start
            if address not in instructions or instructions[address].bytes[:1] != b'\xe8':
                continue
            displacement = int.from_bytes(instructions[address].bytes[1:5], 'little', signed=True)
            sites.append(CallSite(references[call.address + 1], address + 5 + displacement, unit,
                                  candidate.name, call.address, address - owner, 'exact-window', owner))
    return sites


def proven(sites: list[CallSite], sizes: dict[str, set[int]], extents: dict[int, int], known: dict[str, int], exact_callers: bool = False) -> dict[str, tuple[int, str]]:
    """Keep unanimous, size-checked, unclaimed addresses; reject competing new claims."""
    grouped = defaultdict(list)
    for site in sites:
        grouped[site.symbol].append(site)
    agreed = {name: rows[0].address for name, rows in grouped.items() if len({s.address for s in rows}) == 1}
    claims = Counter(agreed.values())
    claimed = set(known.values())
    found = {}
    for name, address in agreed.items():
        strong = exact_callers and any(s.proof in ("unique-caller", "exact-caller", "exact-window") for s in grouped[name])
        if name in known or address in claimed or claims[address] != 1 or address not in extents:
            continue
        if sizes.get(name) != {extents[address]} and not strong:
            continue
        site = grouped[name][0]
        if strong:
            site = next(s for s in grouped[name] if s.proof in ("unique-caller", "exact-caller", "exact-window"))
        basis = ("exact-window" if site.proof == "exact-window" else "exact-caller") if strong else ("aligned-unique-call" if any(s.proof == "unique-caller" for s in grouped[name]) else "aligned-call")
        target_owner = f"{site.target_caller:#x}" if site.target_caller is not None else ""
        found[name] = (address, f"{basis}:{site.unit}:{site.caller}+{site.offset:#x}=>{target_owner}+{site.target_offset:#x}")
    return found


def unique_caller(code: bytes, fields: set[int], target: bytes, base: int, extents: dict[int, int]) -> int | None:
    """An unnamed caller is usable only at a unique same-size target extent
    whose every non-relocation byte is identical to the compiled function."""
    if len(code) < 16 or not fields:
        return None
    masked = {i for start in fields for i in range(start, start + 4)}
    matches = []
    for address, size in extents.items():
        offset = address - base
        if size != len(code) or not 0 <= offset <= len(target) - size:
            continue
        original = target[offset:offset + size]
        if all(i in masked or a == b for i, (a, b) in enumerate(zip(code, original, strict=True))):
            matches.append(address)
            if len(matches) > 1:
                return None
    return matches[0] if matches else None


def object_sites(path: Path, target: bytes, base: int, extents: dict[int, int], known: dict[str, int], unit: str, *, windows: bool = False) -> tuple[list[CallSite], dict[str, set[int]]]:
    """Compare callers with named addresses or uniquely matching masked code."""
    sites, sizes = [], defaultdict(set)
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        sections = list(elf.iter_sections())
        symbols = list(elf.get_section_by_name(".symtab").iter_symbols())
        relocations = defaultdict(dict)
        fields = defaultdict(set)
        unsupported = defaultdict(set)
        for section in sections:
            if section["sh_type"] != "SHT_RELA":
                continue
            table = list(sections[section["sh_link"]].iter_symbols())
            for rel in section.iter_relocations():
                fields[section["sh_info"]].add(rel["r_offset"])
                if rel["r_info_type"] not in (2, 4, 10, 11):
                    unsupported[section["sh_info"]].add(rel["r_offset"])
                if rel["r_info_type"] in (2, 4) and rel["r_addend"] == -4:
                    relocations[section["sh_info"]][rel["r_offset"]] = table[rel["r_info_sym"]].name
        for symbol in symbols:
            index, start, size = symbol["st_shndx"], symbol["st_value"], symbol["st_size"]
            if symbol["st_info"]["type"] != "STT_FUNC" or not isinstance(index, int) or not size:
                continue
            sizes[symbol.name].add(size)
            code = sections[index].data()[start:start + size]
            if windows and not any(start <= f < start + size for f in unsupported[index]):
                references = {offset - start: name for offset, name in relocations[index].items() if start <= offset < start + size and name not in known}
                function = ObjectFunction(symbol.name, sections[index].name, start, code, frozenset(f - start for f in fields[index] if start <= f < start + size))
                sites.extend(window_calls(function, references, target, base, extents, unit))
            address = known.get(symbol.name)
            if address is None and not any(start <= f < start + size for f in unsupported[index]):
                address = unique_caller(code, {f - start for f in fields[index] if start <= f < start + size}, target, base, extents)
            extent = extents.get(address)
            if address is None or extent is None or not 0 <= address - base <= len(target) - extent:
                continue
            references = {offset - start: name for offset, name in relocations[index].items() if start <= offset < start + size}
            if not references:
                continue
            candidate = ObjectFunction(symbol.name, sections[index].name, start, code, frozenset(f - start for f in fields[index] if start <= f < start + size))
            original = ObjectFunction(symbol.name, ".text", address, target[address - base:address - base + extent], frozenset())
            found = aligned_calls(original, candidate, address, references, unit)
            proof = "named" if symbol.name in known else "unique-caller"
            if (proof == "named" and not any(start <= f < start + size for f in unsupported[index])
                    and unique_caller(code, {f - start for f in fields[index] if start <= f < start + size}, target, base, {address: extent}) == address):
                proof = "exact-caller"
            sites.extend(replace(site, proof=proof) for site in found)
    return sites, dict(sizes)


def discover(root: Path, units: list[str], overrides: dict[str, Path] | None = None, exact_callers: bool = False, *, windows: bool = False) -> tuple[list[CallSite], dict[str, tuple[int, str]]]:
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
        obj = (overrides or {}).get(data["unit"], report.with_suffix(".o"))
        if data["unit"] not in (overrides or {}) and data.get("object_sha256") != hashlib.sha256(obj.read_bytes()).hexdigest():
            raise ValueError(f"{report}: object differs from its match report; run hv match")
        found, compiled = object_sites(obj, target, base, extents, known, data["unit"], windows=windows and (not selected or report.stem in selected))
        sites.extend(found)
        for name, values in compiled.items():
            sizes[name].update(values)
            if not selected or report.stem in selected:
                allowed.add(name)
    return sites, {name: row for name, row in proven(sites, dict(sizes), extents, known, exact_callers=exact_callers or windows).items() if name in allowed}


def learn_chain(root: Path, unit: str, obj: Path) -> dict[str, tuple[int, str]]:
    """Learn until newly named callers expose no further callee addresses.
    Every round uses the same object and the existing proof and clash checks.
    """
    learned = {}
    while True:
        _, additional = discover(root, [unit], overrides={unit: obj}, exact_callers=True)
        if not additional:
            return learned
        if learned.keys() & additional.keys():
            raise RuntimeError("call learning repeated an already applied claim")
        apply(root, additional)
        learned.update(additional)


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


def alias_claims(sites: list[CallSite], known: dict[str, int], identical: set[tuple[str, str]]) -> dict[str, tuple[int, str]]:
    """Swap constructor aliases only when compiled bodies are proven identical
    and all observed calls agree with the swapped binding."""
    grouped = defaultdict(list)
    for site in sites:
        grouped[site.symbol].append(site)
    found = {}
    for first, second in sorted(identical):
        if first not in known or second not in known or known[first] == known[second]:
            continue
        old = {known[first], known[second]}
        if any(name not in (first, second) and address in old for name, address in known.items()):
            continue
        desired = {first: known[second], second: known[first]}
        observed = [s for name in desired for s in grouped[name]]
        if not observed or any(s.address != desired[s.symbol] for s in observed):
            continue
        witness = observed[0]
        for name, address in desired.items():
            found[name] = (address, f"aligned-alias:{witness.unit}:{witness.caller}+{witness.offset:#x}:previous={known[name]:#x}")
    return found


def constructor_aliases(root: Path, sites: list[CallSite]) -> dict[str, tuple[int, str]]:
    """Recognize exact complete/base constructor copies with identical code and
    identical relocation targets before considering an exchange of their names."""
    from concord.diff import read_function
    from concord.transforms import demangle

    with (root / "config" / BUILD / "symbols.tsv").open() as stream:
        known = {row["symbol"]: int(row["address"], 16) for row in csv.DictReader(stream, delimiter="\t")}
    identical = set()
    for report in (root / "build" / "match" / BUILD).glob("*.json"):
        data = json.loads(report.read_text())
        exact = {f["symbol"] for s in data["sections"] for f in s.get("functions", []) if f["exact"] and f.get("extent") == "fde"}
        for first in exact:
            if "C1E" not in first:
                continue
            second = first.replace("C1E", "C2E", 1)
            if second not in exact or demangle(first) != demangle(second):
                continue
            obj = report.with_suffix(".o")
            a, b = read_function(obj, first), read_function(obj, second)
            if a.code != b.code or a.relocations != b.relocations:
                continue
            with obj.open("rb") as stream:
                elf = ELFFile(stream)
                refs = []
                for section in elf.iter_sections():
                    if section["sh_type"] != "SHT_RELA" or elf.get_section(section["sh_info"]).name != a.section:
                        continue
                    table = elf.get_section(section["sh_link"])
                    refs.extend((r["r_offset"], r["r_info_type"], r["r_addend"], table.get_symbol(r["r_info_sym"]).name) for r in section.iter_relocations())
                def signature(function, references=refs):
                    return [(offset - function.start, kind, addend, name) for offset, kind, addend, name in references if function.start <= offset < function.start + len(function.code)]
                if signature(a) == signature(b):
                    identical.add((first, second))
    return alias_claims(sites, known, identical)


def apply_aliases(root: Path, learned: dict[str, tuple[int, str]]) -> None:
    """Replace a bijection of existing alias claims through Harvest's symbol API."""
    if not learned:
        return
    script = """import json, sys
from hv import symbols
build = sys.argv[1]
learned = json.load(sys.stdin)
rows = symbols.load(build)
known = symbols.by_name(rows)
if any(name not in known for name in learned) or {known[name] for name in learned} != {row[0] for row in learned.values()}:
    raise ValueError('constructor claims changed during verification')
if any(s.name not in learned and s.address in {row[0] for row in learned.values()} for s in rows):
    raise ValueError('another symbol claims a constructor address')
symbols.save(build, [symbols.Symbol(learned[s.name][0], s.size, s.name, learned[s.name][1]) if s.name in learned else s for s in rows])
"""
    subprocess.run(["uv", "--no-config", "run", "--locked", "python", "-c", script, BUILD], cwd=root, input=json.dumps(learned), text=True, check=True, capture_output=True)
