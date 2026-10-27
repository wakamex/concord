"""Compare each inexact function's calls and constants with the target's copy.

Before a near miss is attributed to unit context (headers, declaration order,
inline copies), this check asks whether what the function calls and loads
differs at all. For every inexact function in the build it compares, against
the target's copy of the same symbol:

- the multiset of direct callees, matched by name first and by resolved target
  address after, so our call to a placed symbol and the target's call to its
  `sub_` name are not a difference;
- the multiset of immediate operands with relocation fields masked, and the
  multiset of data references the relocations point at, normalized to section
  or symbol plus offset on both sides, and for GCC merged string pools to the
  string bytes at that offset so a pool layout shift is not reported.

Functions where either multiset differs are ranked by target bytes. Left-over
callees, immediates and references are count differences, not proof that a
particular source statement or an inline decision differs; an inlined string
constructor can reference one literal several times, and a shared compiled
tail can also change counts without changing the source operations. A
callee left over on both sides is reported as a substitution ("ours calls
resize where the target calls reserve") unless one leftover is an out-of-line
copy whose body calls the other leftover (inline-copy noise, as when ours calls
a CString constructor and the target calls operator new[] because one side
inlined that constructor). A callee left over on one side only is reported as a
possible inline or shared tail.
"""

from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import capstone
from capstone import x86
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from concord.diff import _family
from concord.harvest import Harvest

CALL, TAIL_JMP = 0xE8, 0xE9  # a 5-byte direct call or tail jump's field starts at +1
PC32, PLT32 = 2, 4           # R_X86_64 relocation types: PC-relative fields read at field end


@dataclass(frozen=True)
class Callee:
    offset: int
    symbol: str


@dataclass(frozen=True)
class Constant:
    offset: int
    value: int
    context: tuple  # (mnemonic, signature of the non-immediate operands)
    site: str       # the non-immediate operands' text
    text: str


@dataclass(frozen=True)
class DataRef:
    offset: int
    base: str       # canonical section or symbol name
    dest: int       # offset into it
    mnemonic: str
    text: str


@dataclass(frozen=True)
class FunctionRefs:
    size: int
    code: bytes
    callees: tuple[Callee, ...]
    constants: tuple[Constant, ...]
    datarefs: tuple[DataRef, ...]
    indirect: int


def _canonical(name: str) -> str:
    """A section name's comparable identity: a COMDAT section stands for its
    symbol, pooled constants and strings stay the section."""
    for prefix in (".rodata._Z", ".data._Z", ".bss._Z", ".text._Z"):
        if name.startswith(prefix):
            return name[len(prefix) - 2 :]
    return name


def _string_section_encoding(name: str) -> str | None:
    """How to read a pooled literal in a GCC merged string section."""
    if not name.startswith(".rodata.str"):
        return None
    tag = name.split(".", 3)[2]
    if tag == "str1":
        return "utf8"
    if tag == "str4":
        return "utf32le"
    return None


def read_string_sections(path: Path) -> dict[str, bytes]:
    """The `.rodata.str*` section bodies from one object file."""
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        return {
            section.name: section.data()
            for section in elf.iter_sections()
            if _string_section_encoding(section.name)
        }


def _pooled_string(data: bytes, offset: int, encoding: str) -> bytes | None:
    if offset < 0 or offset >= len(data):
        return None
    if encoding == "utf8":
        end = data.find(b"\x00", offset)
        return None if end < 0 else data[offset:end]
    if encoding == "utf32le":
        units = []
        pos = offset
        while pos + 4 <= len(data):
            unit = int.from_bytes(data[pos : pos + 4], "little")
            if unit == 0:
                break
            units.append(unit)
            pos += 4
        else:
            return None
        return b"".join(unit.to_bytes(4, "little") for unit in units)
    return None


def _ref_data_key(ref: DataRef, sections: dict[str, bytes]) -> tuple:
    encoding = _string_section_encoding(ref.base)
    data = sections.get(ref.base) if encoding else None
    if encoding and data is not None and (text := _pooled_string(data, ref.dest, encoding)) is not None:
        return ("string", ref.base, text)
    return ("offset", ref.base, ref.dest)


def _ref_label(ref: DataRef, sections: dict[str, bytes]) -> str:
    encoding = _string_section_encoding(ref.base)
    data = sections.get(ref.base) if encoding else None
    if encoding and data is not None and (text := _pooled_string(data, ref.dest, encoding)) is not None:
        if encoding == "utf8":
            return f'{ref.base}:"{text.decode("utf-8", "backslashreplace")}"'
        wide = text.decode("utf-32le")
        return f'{ref.base}:L"{wide}"'
    return f"{ref.base}+{ref.dest:#x}"


def _operand_sig(i, o) -> tuple:
    if o.type == x86.X86_OP_REG:
        return ("reg", _family(i.reg_name(o.reg)))
    if o.type == x86.X86_OP_MEM:
        base = _family(i.reg_name(o.mem.base)) if o.mem.base else None
        index = _family(i.reg_name(o.mem.index)) if o.mem.index else None
        return ("mem", base, index, o.mem.scale, o.mem.disp, o.size)
    return ("imm",)


def _ref_key(sym, names: dict[int, str], kind: int, addend: int, func_at: dict, object_at: dict,
             trailing: int = 0) -> tuple[str, int]:
    """What a relocation points at: (canonical base, offset into it).

    Section symbols and local labels compare as their section plus offset, so
    the delinked target's `.rodata.cst4+N` and our `.LCn` label at offset N are
    the same reference; defined symbols compare the same way, so our `m_keys`
    in `.bss` and the target's `.bss+N` agree when they land on the same slot.
    A relocation against a defined object symbol names that object at the
    offset within it, so GCC's jump-table load through `.rodata+N` and the
    target's load through `Base64Alphabet2` at offset zero compare the same when
    the object sits at N. PC-relative fields read at the instruction's end, so
    their addend sits 4 bytes plus any trailing field below zero; absolute
    fields read the stored value. A reference landing on a function symbol takes
    that function's name.
    """
    dest = addend + 4 + trailing if kind in (PC32, PLT32) else addend
    index = sym["st_shndx"]
    section = names.get(index, "") if isinstance(index, int) else ""
    if sym["st_info"]["type"] == "STT_OBJECT" and sym.name:
        return _canonical(sym.name), dest
    if isinstance(index, int):
        at = sym["st_value"] + dest
        if (function := func_at.get((index, at))) is not None:
            return function, 0
        if (name := object_at.get((index, at))) is not None:
            return _canonical(name), 0
        return _canonical(section), at
    return sym.name or section, dest


def _function_refs(md, code: bytes, fields: dict[int, tuple], names: dict[int, str], func_at: dict,
                   object_at: dict) -> FunctionRefs:
    callees, constants, datarefs = [], [], []
    indirect = 0
    for i in md.disasm(code, 0):
        local = {o: f for o, f in fields.items() if i.address <= o < i.address + i.size}
        text = f"{i.mnemonic} {i.op_str}".strip()
        branch = i.group(x86.X86_GRP_JUMP) or i.group(x86.X86_GRP_CALL)
        for offset, (kind, addend, sym) in local.items():
            if kind in (PC32, PLT32) and addend == -4 and offset > 0 and code[offset - 1] in (CALL, TAIL_JMP):
                callees.append(Callee(offset, sym.name or func_at.get((sym["st_shndx"], sym["st_value"]), "")))
            if i.mnemonic == "call" and i.operands and i.operands[0].type == x86.X86_OP_MEM:
                indirect += 1
        def hit(field_offset: int, field_size: int, insn=i, local=local):
            for o, f in local.items():
                if field_offset and field_offset <= o - insn.address < field_offset + field_size:
                    kind, addend, sym = f
                    trailing = insn.address + insn.size - (o + 4)
                    return kind, addend, sym, trailing
            return None

        imm_hit = hit(i.imm_offset, i.imm_size)
        disp_hit = hit(i.disp_offset, i.disp_size)
        for o in i.operands:
            if o.type == x86.X86_OP_IMM:
                if branch:
                    continue
                if imm_hit is not None:
                    base, dest = _ref_key(imm_hit[2], names, imm_hit[0], imm_hit[1], func_at, object_at, imm_hit[3])
                    datarefs.append(DataRef(i.address, base, dest, i.mnemonic, text))
                    continue
                width = i.imm_size * 8
                context = (i.mnemonic, tuple(_operand_sig(i, x) for x in i.operands if x is not o))
                site = i.op_str.rsplit(",", 1)[0].strip()
                constants.append(Constant(i.address, o.imm & ((1 << width) - 1) if width else o.imm, context, site, text))
            elif o.type == x86.X86_OP_MEM:
                if disp_hit is not None:
                    base, dest = _ref_key(disp_hit[2], names, disp_hit[0], disp_hit[1], func_at, object_at, disp_hit[3])
                    datarefs.append(DataRef(i.address, base, dest, i.mnemonic, text))
                elif not o.mem.base and not o.mem.index and o.mem.disp:
                    datarefs.append(DataRef(i.address, "<absolute>", o.mem.disp, i.mnemonic, text))
    return FunctionRefs(len(code), code, tuple(callees), tuple(constants), tuple(datarefs), indirect)


def read_refs(path: Path, only: set[str] | None = None) -> dict[str, FunctionRefs]:
    """Each defined function symbol's callees, constants and data references."""
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        sections = list(elf.iter_sections())
        names = {i: s.name for i, s in enumerate(sections)}
        rels = defaultdict(dict)
        for section in sections:
            if not isinstance(section, RelocationSection):
                continue
            table = sections[section["sh_link"]]
            for r in section.iter_relocations():
                rels[section["sh_info"]][r["r_offset"]] = (r["r_info_type"], r["r_addend"], table.get_symbol(r["r_info_sym"]))
        symtab = elf.get_section_by_name(".symtab")
        symbols = [s for s in symtab.iter_symbols()
                   if s["st_info"]["type"] == "STT_FUNC" and isinstance(s["st_shndx"], int) and s["st_size"]]
        func_at = {(s["st_shndx"], s["st_value"]): s.name for s in symbols}
        object_at = {
            (s["st_shndx"], s["st_value"]): s.name
            for s in symtab.iter_symbols()
            if s["st_info"]["type"] == "STT_OBJECT" and s.name and isinstance(s["st_shndx"], int)
        }
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        md.detail = True
        functions = {}
        for sym in symbols:
            if only is not None and sym.name not in only:
                continue
            index, start, size = sym["st_shndx"], sym["st_value"], sym["st_size"]
            code = sections[index].data()[start : start + size]
            fields = {o - start: f for o, f in rels[index].items() if start <= o < start + size}
            functions[sym.name] = _function_refs(md, code, fields, names, func_at, object_at)
        return functions


def _cancel(ours: list[str], target: list[str], resolve_ours, resolve_target) -> tuple[list, list]:
    """Left-over names after matching by name, then by resolved target address."""
    ours_rest = [(e, resolve_ours(e)) for e in (Counter(ours) - Counter(target)).elements()]
    they_rest = [(e, resolve_target(e)) for e in (Counter(target) - Counter(ours)).elements()]
    avail = Counter(a for _, a in they_rest if a is not None)
    ours_only = []
    for e, a in ours_rest:
        if a is not None and avail[a]:
            avail[a] -= 1
        else:
            ours_only.append((e, a))
    avail = Counter(a for _, a in ours_rest if a is not None)
    target_only = []
    for e, a in they_rest:
        if a is not None and avail[a]:
            avail[a] -= 1
        else:
            target_only.append((e, a))
    return ours_only, target_only


def _plt_names(elf: ELFFile) -> dict[int, str]:
    """Each PLT stub's address to the dynamic symbol it calls, so a body read
    from the stripped original still names its external callees."""
    rela = elf.get_section_by_name(".rela.plt")
    if rela is None:
        return {}
    symbols = elf.get_section(rela["sh_link"])
    got = {r["r_offset"]: symbols.get_symbol(r["r_info_sym"]).name for r in rela.iter_relocations()}
    names = {}
    for plt in elf.iter_sections():
        if not plt.name.startswith(".plt"):
            continue
        data, base = plt.data(), plt["sh_addr"]
        offset = 0
        while (hit := data.find(b"\xff\x25", offset)) >= 0:  # jmp *disp(%rip)
            offset = hit + 2
            disp = int.from_bytes(data[offset : offset + 4], "little", signed=True)
            slot = base + offset + 4 + disp
            entry = hit - 4 if data[hit - 4 : hit] == b"\xf3\x0f\x1e\xfa" else hit  # skip an IBT endbr64
            if slot in got:
                names[base + entry] = got[slot]
    return names


def original_refs(path: Path, learned: list[tuple[int, int, str]], named: dict[int, str]) -> dict[str, FunctionRefs]:
    """The call lists of learned functions, read from the original executable.

    A delinked object holds only the extents its unit owns, so an out-of-line
    copy the matcher credits to no unit's span (list::_M_erase, or an
    insert_aux the target places beside a different unit) has a name and a
    call target but no body for the inline-copy check. The original has it:
    each learned function's bytes disassembled, direct calls and tail jumps
    out of its range resolved by the learned table, the PLT's dynamic names,
    or a sub_ name carrying the address."""
    if not path.is_file() or not learned:
        return {}
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        plt_names = _plt_names(elf)
        sections = [
            s for s in elf.iter_sections()
            if s["sh_type"] == "SHT_PROGBITS" and s["sh_flags"] & 0x4  # SHF_EXECINSTR
        ]
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        md.detail = True
        catalog = {}
        for address, size, name in learned:
            for section in sections:
                start = section["sh_addr"]
                if start <= address < start + section["sh_size"]:
                    break
            else:
                continue
            code = section.data()[address - start : address - start + size]
            callees = []
            for i in md.disasm(code, address):
                if not (i.group(x86.X86_GRP_CALL) or i.group(x86.X86_GRP_JUMP)):
                    continue
                ops = i.operands
                if len(ops) != 1 or ops[0].type != x86.X86_OP_IMM:
                    continue
                target = ops[0].imm
                if address <= target < address + len(code):
                    continue  # a branch inside the function, not a tail call
                callee = named.get(target) or plt_names.get(target) or f"sub_{target:x}"
                callees.append(Callee(i.address - address, callee))
            catalog[name] = FunctionRefs(len(code), code, tuple(callees), (), (), 0)
        return catalog


def _body_calls(outer: str, inner: str, ours_catalog: dict[str, FunctionRefs],
                target_catalog: dict[str, FunctionRefs], resolve_ours, resolve_target,
                original_catalog: dict[str, FunctionRefs] | None = None) -> bool:
    """True when an out-of-line copy of outer calls inner by name or address."""
    inner_addrs = {a for a in (resolve_ours(inner), resolve_target(inner)) if a is not None}
    for catalog in (ours_catalog, target_catalog, original_catalog or {}):
        body = catalog.get(outer)
        if body is None:
            continue
        for call in body.callees:
            if call.symbol == inner:
                return True
            addr = resolve_ours(call.symbol) or resolve_target(call.symbol)
            if addr is not None and addr in inner_addrs:
                return True
    return False


def _inline_copy_pairs(ours_only: list, target_only: list, ours_catalog: dict[str, FunctionRefs],
                       target_catalog: dict[str, FunctionRefs], resolve_ours, resolve_target,
                       original_catalog: dict[str, FunctionRefs] | None = None) -> tuple[list, list, list]:
    """Pair leftover callees explained by one side's out-of-line copy calling the other."""
    target_rest = list(target_only)
    used = set()
    explained, ours_rest = [], []
    for entry in sorted(ours_only):
        name = entry[0]
        match = None
        for index, other in enumerate(target_rest):
            if index in used:
                continue
            other_name = other[0]
            if _body_calls(name, other_name, ours_catalog, target_catalog, resolve_ours, resolve_target, original_catalog):
                match = (name, other_name, "ours", "target", entry, other)
            elif _body_calls(other_name, name, ours_catalog, target_catalog, resolve_ours, resolve_target, original_catalog):
                match = (other_name, name, "target", "ours", other, entry)
            if match:
                explained.append(match)
                used.add(index)
                break
        if not match:
            ours_rest.append(entry)
    target_rest = [entry for index, entry in enumerate(target_rest) if index not in used]
    return explained, ours_rest, target_rest


def _pair(ours_only: list, target_only: list, key) -> tuple[list, list, list]:
    """Match left-over entries that plausibly replace each other."""
    ours_by, target_by = defaultdict(list), defaultdict(list)
    for e in ours_only:
        ours_by[key(e)].append(e)
    for e in target_only:
        target_by[key(e)].append(e)
    pairs, ours_left, target_left = [], [], []
    for k in sorted(set(ours_by) | set(target_by), key=repr):
        o, t = ours_by.get(k, []), target_by.get(k, [])
        pairs.extend(zip(o, t))
        ours_left.extend(o[len(t):])
        target_left.extend(t[len(o):])
    return pairs, ours_left, target_left


def _hex(value: int | None) -> str | None:
    return None if value is None else f"{value:#x}"


def _const_key(c: Constant) -> tuple:
    return c.value, c.context, c.text


def _const_entry(c: Constant, ours: Counter, target: Counter) -> dict:
    key = _const_key(c)
    # When both sides contain the same instruction, the multiset difference
    # does not identify which of its occurrences is the extra one.
    offset = None if ours[key] and target[key] else f"{c.offset:#x}"
    return {"value": c.value, "offset": offset, "site": c.site, "instruction": c.text,
            "ours_count": ours[key], "target_count": target[key]}


def _ref_entry(r: DataRef, ours: Counter, target: Counter, sections: dict[str, bytes]) -> dict:
    key = _ref_data_key(r, sections)
    offset = None if ours[key] and target[key] else f"{r.offset:#x}"
    return {"ref": _ref_label(r, sections), "offset": offset, "instruction": r.text,
            "ours_count": ours[key], "target_count": target[key]}


def compare_function(ours: FunctionRefs, target: FunctionRefs, *,
                     resolve_ours, resolve_target, display,
                     ours_catalog: dict[str, FunctionRefs] | None = None,
                     target_catalog: dict[str, FunctionRefs] | None = None,
                     ours_sections: dict[str, bytes] | None = None,
                     target_sections: dict[str, bytes] | None = None,
                     original_catalog: dict[str, FunctionRefs] | None = None) -> list[dict]:
    """The named differences between the two copies, structured."""
    differences = []
    ours_only, target_only = _cancel([c.symbol for c in ours.callees], [c.symbol for c in target.callees],
                                     resolve_ours, resolve_target)
    ours_catalog = ours_catalog or {}
    target_catalog = target_catalog or {}
    original_catalog = original_catalog or {}
    ours_sections = ours_sections or {}
    target_sections = target_sections or {}

    def callee(name_addr, side):
        name, addr = name_addr
        return {"symbol": name, "name": display(side, name, addr), "address": _hex(addr)}

    def append_inline_noise(outer_entry, outer_side, inner_entry, inner_side):
        if outer_side == "ours":
            differences.append({"kind": "call", "classification": "called-out-of-line",
                                "ours": callee(outer_entry, "ours")})
            differences.append({"kind": "call", "classification": "original-called",
                                "target": callee(inner_entry, "target")})
        else:
            differences.append({"kind": "call", "classification": "called-out-of-line",
                                "ours": callee(inner_entry, "ours")})
            differences.append({"kind": "call", "classification": "original-called",
                                "target": callee(outer_entry, "target")})

    if ours_only and target_only:
        explained, ours_only, target_only = _inline_copy_pairs(
            ours_only, target_only, ours_catalog, target_catalog, resolve_ours, resolve_target, original_catalog)
        for _outer, _inner, outer_side, inner_side, outer_entry, inner_entry in explained:
            append_inline_noise(outer_entry, outer_side, inner_entry, inner_side)
    if ours_only and target_only:
        # Both sides have higher counts for at least one callee. These are
        # candidates for a substitution, not proof of a source difference.
        differences.append({"kind": "call", "classification": "different-callees",
                            "ours": [callee(e, "ours") for e in sorted(ours_only)],
                            "target": [callee(e, "target") for e in sorted(target_only)]})
    elif ours_only or target_only:
        # One side only: the other inlined the callee; inline-copy noise.
        for name, addr in sorted(ours_only):
            differences.append({"kind": "call", "classification": "called-out-of-line",
                                "ours": callee((name, addr), "ours")})
        for name, addr in sorted(target_only):
            differences.append({"kind": "call", "classification": "original-called",
                                "target": callee((name, addr), "target")})

    # Constants compare as a multiset of values. Cancel identical instruction
    # contexts first, so an unrelated occurrence of the same value cannot be
    # reported as the difference. Repeated identical contexts remain ambiguous.
    ours_count = Counter(c.value for c in ours.constants)
    target_count = Counter(c.value for c in target.constants)
    ours_keys = Counter(_const_key(c) for c in ours.constants)
    target_keys = Counter(_const_key(c) for c in target.constants)

    def unmatched(constants, foreign):
        remaining = foreign.copy()
        pool = defaultdict(list)
        for c in constants:
            key = _const_key(c)
            if remaining[key]:
                remaining[key] -= 1
            else:
                pool[c.value].append(c)
        return pool

    ours_pool = unmatched(ours.constants, target_keys)
    target_pool = unmatched(target.constants, ours_keys)
    ours_excess = ours_count - target_count
    target_excess = target_count - ours_count
    ours_ctx = Counter(c.context for value in ours_excess for c in ours_pool[value])
    target_ctx = Counter(c.context for value in target_excess for c in target_pool[value])

    def take(pool, value, foreign):
        choices = pool[value]
        shared = [c for c in choices if foreign[c.context]]
        chosen = shared[0] if shared else choices[0]
        choices.remove(chosen)
        return chosen

    ours_rest = [take(ours_pool, v, target_ctx) for v in ours_excess.elements()]
    target_rest = [take(target_pool, v, ours_ctx) for v in target_excess.elements()]
    pairs, ours_left, target_left = _pair(ours_rest, target_rest, key=lambda c: c.context)
    shape = lambda c: (c.context[0], tuple(s[0] for s in c.context[1]))
    extra, ours_left, target_left = _pair(ours_left, target_left, key=shape)
    for o, t in pairs + extra:
        differences.append({"kind": "constant", "classification": "different-constants",
                            "ours": _const_entry(o, ours_keys, target_keys),
                            "target": _const_entry(t, ours_keys, target_keys)})
    for c in ours_left:
        differences.append({"kind": "constant", "classification": "ours-only",
                            "ours": _const_entry(c, ours_keys, target_keys)})
    for c in target_left:
        differences.append({"kind": "constant", "classification": "target-only",
                            "target": _const_entry(c, ours_keys, target_keys)})

    ours_by_key, target_by_key = defaultdict(list), defaultdict(list)
    for r in ours.datarefs:
        ours_by_key[_ref_data_key(r, ours_sections)].append(r)
    for r in target.datarefs:
        target_by_key[_ref_data_key(r, target_sections)].append(r)
    ours_keys = Counter(k for k, rows in ours_by_key.items() for _ in rows)
    target_keys = Counter(k for k, rows in target_by_key.items() for _ in rows)
    # A surplus of references to an item present on both sides does not mean
    # that item is absent from either side. Keep it separate from unshared
    # references so it cannot be paired with a different item.
    for key in ours_by_key:
        if target_keys[key] and ours_keys[key] != target_keys[key]:
            differences.append({"kind": "data", "classification": "reference-count-difference",
                                "ours": _ref_entry(ours_by_key[key][0], ours_keys, target_keys, ours_sections),
                                "target": _ref_entry(target_by_key[key][0], ours_keys, target_keys, target_sections)})
    ours_rest = [ours_by_key[k].pop() for k in (ours_keys - target_keys).elements() if not target_keys[k]]
    target_rest = [target_by_key[k].pop() for k in (target_keys - ours_keys).elements() if not ours_keys[k]]

    def split_pooled(refs, sections):
        pooled, rest = [], []
        for ref in refs:
            if _ref_data_key(ref, sections)[0] == "string":
                pooled.append(ref)
            else:
                rest.append(ref)
        return pooled, rest

    ours_pooled, ours_rest = split_pooled(ours_rest, ours_sections)
    target_pooled, target_rest = split_pooled(target_rest, target_sections)
    pairs, ours_rest, target_rest = _pair(ours_rest, target_rest, key=lambda r: r.base)
    extra, ours_rest, target_rest = _pair(ours_rest, target_rest, key=lambda r: r.mnemonic)
    ours_rest.extend(ours_pooled)
    target_rest.extend(target_pooled)
    for o, t in pairs + extra:
        differences.append({"kind": "data", "classification": "different-references",
                            "ours": _ref_entry(o, ours_keys, target_keys, ours_sections),
                            "target": _ref_entry(t, ours_keys, target_keys, target_sections)})
    for r in ours_rest:
        differences.append({"kind": "data", "classification": "ours-only",
                            "ours": _ref_entry(r, ours_keys, target_keys, ours_sections)})
    for r in target_rest:
        differences.append({"kind": "data", "classification": "target-only",
                            "target": _ref_entry(r, ours_keys, target_keys, target_sections)})

    if ours.indirect != target.indirect:
        differences.append({"kind": "indirect", "ours": ours.indirect, "target": target.indirect})
    return differences


def load_symbols(root: Path, build: str) -> tuple[dict[int, str], dict[str, int], list[tuple[int, int, str]]]:
    """The learned symbol table: target address -> symbol, symbol -> address,
    and each (address, size, symbol) row."""
    named, name_to_addr, learned = {}, {}, []
    symbols_tsv = root / "config" / build / "symbols.tsv"
    if symbols_tsv.exists():
        for row in csv.DictReader(symbols_tsv.open(newline=""), delimiter="\t"):
            address, symbol = int(row["address"], 16), row["symbol"]
            named[address] = symbol
            name_to_addr[symbol] = address
            learned.append((address, int(row["size"]), symbol))
    return named, name_to_addr, learned


def resolvers(rows: dict[str, dict], named: dict[int, str], name_to_addr: dict[str, int]):
    """The callee address resolution for one unit's report rows.

    Our callees resolve through the learned symbol table first and the unit's
    placed addresses after; the target's `sub_<hex>` names carry their own
    address. `display` renders a callee with its address when one is known.
    """
    placed_addr = {name: int(row["address"], 16) for name, row in rows.items() if row.get("address")}

    def resolve_ours(name):
        return name_to_addr.get(name) or placed_addr.get(name)

    def resolve_target(name):
        if name.startswith("sub_"):
            try:
                return int(name[4:], 16)
            except ValueError:
                pass
        return name_to_addr.get(name)

    def display(side, name, addr):
        if addr is not None:
            return f"{addr:#x} {named.get(addr, name)}"
        return name

    return resolve_ours, resolve_target, display


def gather(harvest: Harvest, units: Iterable[str] = ()) -> dict:
    """Run the comparison over every inexact function of the given units."""
    root, build = harvest.root, harvest.build
    named, name_to_addr, learned = load_symbols(root, build)
    original_catalog = original_refs(root / "orig" / build / "Harvest", learned, named)

    selected = {harvest.slug(u) for u in units}
    findings, skipped, compared = [], [], 0
    for path in sorted(harvest.reports.glob("*.json")):
        slug = path.stem
        if selected and slug not in selected:
            continue
        report = json.loads(path.read_text())
        rows = {f["symbol"]: f for s in report["sections"] for f in s.get("functions", [])}
        inexact = {name: row for name, row in rows.items() if not row.get("exact")}
        if not inexact:
            continue
        target_path = harvest.objects / "target" / f"{slug}.o"
        ours_path = harvest.reports / f"{slug}.o"
        if not target_path.exists() or not ours_path.exists():
            skipped.extend({"unit": report["unit"], "symbol": n, "reason": "no object"} for n in inexact)
            continue
        ours = read_refs(ours_path, set(inexact))
        target = read_refs(target_path, set(inexact))
        ours_catalog = read_refs(ours_path)
        target_catalog = read_refs(target_path)
        ours_sections = read_string_sections(ours_path)
        target_sections = read_string_sections(target_path)
        resolve_ours, resolve_target, display = resolvers(rows, named, name_to_addr)

        for name, row in inexact.items():
            if name not in ours or name not in target:
                skipped.append({"unit": report["unit"], "symbol": name, "reason": "not emitted on both sides"})
                continue
            compared += 1
            differences = compare_function(ours[name], target[name], resolve_ours=resolve_ours,
                                           resolve_target=resolve_target, display=display,
                                           ours_catalog=ours_catalog, target_catalog=target_catalog,
                                           ours_sections=ours_sections, target_sections=target_sections,
                                           original_catalog=original_catalog)
            if not differences:
                continue
            address = int(row["address"], 16) if row.get("address") else None
            findings.append({
                "unit": report["unit"], "symbol": name, "address": _hex(address),
                "verified_address": named.get(address) == name if address is not None else False,
                "stub": len(ours[name].code) <= 4,
                "ours_bytes": ours[name].size, "target_bytes": target[name].size,
                "differences": differences,
            })
    findings.sort(key=lambda r: (-r["target_bytes"], r["unit"], r["symbol"]))
    return {"build": build, "harvest": str(root), "compared": compared,
            "functions": findings, "skipped": skipped}


def _demangle_all(names: set[str]) -> dict[str, str]:
    """One c++filt run for every name the report will print."""
    plain = sorted(n for n in names if n and not n.startswith(("sub_", "<", ".")))
    done = subprocess.run(["c++filt", *plain], capture_output=True, text=True, check=False)
    pretty = dict(zip(plain, done.stdout.splitlines()))
    return {n: pretty.get(n, n) for n in names}


def show(result: dict) -> list[str]:
    """Render the ranked findings for the terminal."""
    names = set()
    for f in result["functions"]:
        for d in f["differences"]:
            for side in ("ours", "target"):
                for entry in d.get(side, []) if isinstance(d.get(side), list) else [d.get(side)]:
                    if not entry:
                        continue
                    if entry.get("symbol"):
                        names.add(entry["name"].rsplit(" ", 1)[-1])
                    if entry.get("ref"):
                        names.add(entry["ref"].rsplit("+", 1)[0])
    pretty = _demangle_all(names)

    def prettify(entry: dict | list | None) -> str:
        if isinstance(entry, list):
            return ", ".join(prettify(e) for e in entry)
        if not entry:
            return "?"
        if "symbol" in entry:
            label = pretty.get(entry["name"].rsplit(" ", 1)[-1], entry["name"])
            return f"{entry['address']} {label}" if entry.get("address") else label
        if "ref" in entry:
            ref = entry["ref"]
            if ":" in ref:
                return ref
            base, off = ref.rsplit("+", 1)
            return f"{pretty.get(base, base)}+{off}"
        return "?"

    def render(d: dict) -> str:
        if d["kind"] == "call":
            if d["classification"] == "different-callees":
                return f"calls: ours has additional calls to {prettify(d['ours'])}; the target has additional calls to {prettify(d['target'])}"
            if d["classification"] == "called-out-of-line":
                return f"calls: ours calls {prettify(d['ours'])} more often than the target"
            return f"calls: the target calls {prettify(d['target'])} more often than ours"
        if d["kind"] == "constant":
            def count(e):
                return f"`{e['instruction']}` occurs {e['ours_count']} times in ours and {e['target_count']} times in the target"

            if d["classification"] == "different-constants":
                o, t = d["ours"], d["target"]
                return f"constants: possible pairing: {count(o)}; {count(t)}"
            e = d.get("ours") or d.get("target")
            return f"constants: {count(e)}"
        if d["kind"] == "data":
            if d["classification"] == "different-references":
                return f"references: possible pairing: ours uses {prettify(d['ours'])}; the target uses {prettify(d['target'])}"
            if d["classification"] == "reference-count-difference":
                e = d["ours"]
                return (f"references: {prettify(e)} appears in both; ours has {e['ours_count']} references, "
                        f"the target has {e['target_count']}")
            side = "ours" if "ours" in d else "the target"
            e = d.get("ours") or d.get("target")
            other = "the target" if side == "ours" else "ours"
            return f"references: {side} uses {prettify(e)} more often than {other}"
        return f"indirect calls: ours {d['ours']}, the original {d['target']}"

    out = []
    for f in result["functions"]:
        flags = [flag for flag, on in (("unverified address", not f["verified_address"]), ("stub", f["stub"])) if on]
        mark = f"  [{', '.join(flags)}]" if flags else ""
        out.append(f"{f['target_bytes']:7}B  {f['unit']}  {f['symbol']}{mark}")
        counts = Counter(render(d) for d in f["differences"])
        out.extend(f"    {line}" + (f" x{n}" if n > 1 else "") for line, n in counts.items())
    return out
