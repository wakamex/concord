"""Cause-attributing object diff.

A plain byte or fuzzy score tells you how far a candidate is from the target but
not why, so the search cannot act on it. This diff disassembles one function from
each side, aligns the instructions, and attributes each mismatch to a cause that
maps to a fix: a swapped operand order, a block laid out in the wrong place, a
differently allocated register, an unexpected inline.

Alignment compares instruction shapes, in which registers are reduced to their
class and relocated operands and branch targets are masked, so a register choice
or a moved branch does not break the alignment of everything after it.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

import capstone
from capstone import x86
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from concord.model import Cause, DiffFinding, DiffResult


@dataclass(frozen=True)
class ObjectFunction:
    """One function's bytes in a relocatable object, with the offsets (relative to
    the function) where relocation fields start."""

    name: str
    section: str
    start: int  # offset of the function in its section
    code: bytes
    relocations: frozenset[int]


def read_function(path: Path, symbol: str) -> ObjectFunction:
    with open(path, "rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        found = [
            s for s in table.iter_symbols() if s.name == symbol and s["st_info"]["type"] == "STT_FUNC"
        ]
        if not found:
            raise KeyError(f"{symbol} is not a function in {path}")
        index, start, size = found[0]["st_shndx"], found[0]["st_value"], found[0]["st_size"]
        section = elf.get_section(index)
        code = section.data()[start : start + size]
        fields = {
            r["r_offset"] - start
            for section in elf.iter_sections()
            if isinstance(section, RelocationSection) and section["sh_info"] == index
            for r in section.iter_relocations()
            if start <= r["r_offset"] < start + size
        }
    return ObjectFunction(symbol, section.name, start, code, frozenset(fields))


def _register_families() -> dict[str, str]:
    families = {}
    for x in "abcd":
        for name in (f"r{x}x", f"e{x}x", f"{x}x", f"{x}l", f"{x}h"):
            families[name] = f"r{x}x"
    for x in ("si", "di", "bp", "sp"):
        for name in (f"r{x}", f"e{x}", x, f"{x}l"):
            families[name] = f"r{x}"
    for n in range(8, 16):
        for suffix in ("", "d", "w", "b"):
            families[f"r{n}{suffix}"] = f"r{n}"
    return families


FAMILY = _register_families()
# Registers the allocator does not choose, kept literal in shapes.
FIXED = {"rsp", "rip"}
MIRRORED = {"jl": "jg", "jle": "jge", "jb": "ja", "jbe": "jae", "je": "je", "jne": "jne"}
MIRRORED |= {v: k for k, v in MIRRORED.items()}
INVERTED = {"jl": "jge", "jle": "jg", "jb": "jae", "jbe": "ja", "je": "jne", "js": "jns", "jp": "jnp", "jo": "jno"}
INVERTED |= {v: k for k, v in INVERTED.items()}
SWAPPABLE = {"cmp", "test", "add", "imul", "and", "or", "xor", "addss", "addsd", "mulss", "mulsd"}
STACK = {"rsp", "rbp"}


@dataclass(frozen=True)
class Instruction:
    offset: int
    mnemonic: str
    operands: tuple  # ("reg", family) | ("imm", value) | ("mem", base, index, scale, disp, size) | ("sym",) | ("label",)
    text: str

    @property
    def is_call(self) -> bool:
        return self.mnemonic == "call"


def _family(name: str | None) -> str | None:
    return None if name is None else FAMILY.get(name, name)


def disassemble(function: ObjectFunction) -> list[Instruction]:
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    out = []
    for i in md.disasm(function.code, 0):
        relocated = any(i.address <= f < i.address + i.size for f in function.relocations)
        branch = i.group(x86.X86_GRP_JUMP) or i.group(x86.X86_GRP_CALL)
        operands = []
        for o in i.operands:
            if o.type == x86.X86_OP_REG:
                operands.append(("reg", _family(i.reg_name(o.reg))))
            elif o.type == x86.X86_OP_IMM:
                operands.append(("sym",) if relocated else ("label",) if branch else ("imm", o.imm))
            elif o.type == x86.X86_OP_MEM:
                base = _family(i.reg_name(o.mem.base)) if o.mem.base else None
                index = _family(i.reg_name(o.mem.index)) if o.mem.index else None
                if relocated and (base in (None, "rip") or o.mem.disp == 0):
                    operands.append(("sym",))
                else:
                    operands.append(("mem", base, index, o.mem.scale, o.mem.disp, o.size))
        text = f"{i.mnemonic} {i.op_str}".strip()
        out.append(Instruction(i.address, i.mnemonic, tuple(operands), text))
    return out


def _reg_shape(family: str | None) -> str | None:
    if family is None or family in FIXED:
        return family
    return "xmm" if family.startswith("xmm") else "gpr"


def shape(instruction: Instruction) -> tuple:
    """The instruction with registers reduced to their class, for alignment."""
    parts = []
    for o in instruction.operands:
        if o[0] == "reg":
            parts.append(("reg", _reg_shape(o[1])))
        elif o[0] == "mem":
            parts.append(("mem", _reg_shape(o[1]), _reg_shape(o[2]), o[3], o[4], o[5]))
        else:
            parts.append(o)
    return (instruction.mnemonic, tuple(parts))


def _registers(instruction: Instruction) -> list[str | None]:
    regs = []
    for o in instruction.operands:
        if o[0] == "reg":
            regs.append(o[1])
        elif o[0] == "mem":
            regs.extend((o[1], o[2]))
    return regs


def _swapped(a: Instruction, b: Instruction) -> bool:
    return (
        a.mnemonic == b.mnemonic
        and a.mnemonic in SWAPPABLE
        and len(a.operands) == 2
        and a.operands != b.operands
        and a.operands == b.operands[::-1]
    )


def _classify_pair(a: Instruction, b: Instruction) -> Cause:
    """Cause for two aligned instructions of different shape."""
    if a.mnemonic in MIRRORED and b.mnemonic in MIRRORED and MIRRORED[a.mnemonic] == b.mnemonic:
        return Cause.OPERAND_ORDER
    if a.mnemonic in INVERTED and INVERTED[a.mnemonic] == b.mnemonic:
        return Cause.BLOCK_ORDER
    if _swapped(a, b) or (a.mnemonic == b.mnemonic and a.operands == b.operands[::-1]):
        return Cause.OPERAND_ORDER
    if a.mnemonic != b.mnemonic:
        return Cause.INSTRUCTION_SELECTION
    if len(a.operands) == len(b.operands):
        causes = set()
        for x, y in zip(a.operands, b.operands):
            if x == y or x[0] != y[0]:
                continue
            if x[0] == "mem" and x[:4] == y[:4] and x[5] == y[5]:
                causes.add(Cause.STACK_LAYOUT if x[1] in STACK else Cause.STRUCT_LAYOUT)
            elif x[0] == "imm":
                causes.add(Cause.IMMEDIATE_OR_RELOC)
        if len(causes) == 1:
            return causes.pop()
    return Cause.INSTRUCTION_SELECTION


def _run(instructions: list[Instruction]) -> str:
    return "; ".join(i.text for i in instructions)


def diff_code(target: ObjectFunction, candidate: ObjectFunction) -> DiffResult:
    """Align the two functions' instructions and attribute every mismatch."""
    if target.code == candidate.code and target.relocations == candidate.relocations:
        return DiffResult(score=100.0, exact=True)
    # Alignment padding follows from where code lands, so it is left out of the
    # alignment rather than reported as a cause of its own.
    t = [i for i in disassemble(target) if i.mnemonic != "nop"]
    c = [i for i in disassemble(candidate) if i.mnemonic != "nop"]
    matcher = difflib.SequenceMatcher(None, [shape(i) for i in t], [shape(i) for i in c], autojunk=False)
    findings: list[DiffFinding] = []
    same = 0
    mismatched: list[tuple[list[Instruction], list[Instruction]]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            mapping: dict[str, str] = {}
            run_start = run_candidate = None
            for a, b in zip(t[i1:i2], c[j1:j2]):
                if a.operands == b.operands:
                    same += 1
                    continue
                if _swapped(a, b):
                    findings.append(DiffFinding(Cause.OPERAND_ORDER, a.offset, f"{a.text}  vs  {b.text}", b.offset))
                    continue
                for x, y in zip(_registers(a), _registers(b)):
                    if x != y and x is not None and y is not None:
                        mapping[x] = y
                if run_start is None:
                    run_start, run_candidate = a.offset, b.offset
            if mapping:
                pairs = ", ".join(f"{x}->{y}" for x, y in sorted(mapping.items()))
                findings.append(DiffFinding(Cause.REGISTER_ALLOCATION, run_start, pairs, run_candidate))
        else:
            mismatched.append((t[i1:i2], c[j1:j2]))
    findings.extend(_mismatched(mismatched))
    if not findings:
        findings.append(DiffFinding(Cause.UNKNOWN, 0, "only alignment padding or relocation fields differ"))
    findings.sort(key=lambda f: f.offset)
    score = 100.0 * 2 * same / (len(t) + len(c)) if t or c else 100.0
    return DiffResult(score=round(score, 2), exact=False, findings=findings)


TRANSFERS = {"jmp", "ret"} | set(INVERTED)


def _pieces(run: list[Instruction]) -> list[list[Instruction]]:
    """Split a run after every jump or return, approximating basic blocks."""
    pieces, current = [], []
    for i in run:
        current.append(i)
        if i.mnemonic in TRANSFERS:
            pieces.append(current)
            current = []
    return pieces + [current] if current else pieces


def _content(piece: list[Instruction], exact: bool) -> list:
    """A piece's instructions, unordered and without its jumps: when GCC moves a
    block it also rewrites the jumps into and out of it."""
    body = [i for i in piece if i.mnemonic not in TRANSFERS or i.mnemonic == "ret"]
    return sorted(((i.mnemonic, i.operands) if exact else shape(i) for i in body), key=repr)


def _mismatched(regions: list[tuple[list[Instruction], list[Instruction]]]) -> list[DiffFinding]:
    """Attribute the regions the alignment could not pair. Basic-block pieces that
    reappear elsewhere, or in another order, are block order; regions of equal
    length are classified instruction by instruction; the rest is one-sided."""
    findings = []
    target_pieces = [(r, p) for r, (a, _) in enumerate(regions) for p in _pieces(a)]
    candidate_pieces = [(r, p) for r, (_, b) in enumerate(regions) for p in _pieces(b)]
    used_t: set[int] = set()
    used_c: set[int] = set()
    for exact in (True, False):
        for ti, (_, piece) in enumerate(target_pieces):
            key = _content(piece, exact)
            if ti in used_t or len(key) < 2:
                continue
            for ci, (_, other) in enumerate(candidate_pieces):
                if ci not in used_c and _content(other, exact) == key:
                    used_t.add(ti)
                    used_c.add(ci)
                    detail = f"{len(piece)} instructions at target +{piece[0].offset:#x} sit at candidate +{other[0].offset:#x}"
                    if [(i.mnemonic, i.operands) for i in piece] != [(i.mnemonic, i.operands) for i in other]:
                        detail += ", reordered within the block" if exact else ", with different registers"
                    findings.append(DiffFinding(Cause.BLOCK_ORDER, piece[0].offset, detail, other[0].offset))
                    break
    pairs: list[tuple[Instruction, Instruction]] = []
    for r, (a, b) in enumerate(regions):
        left = [i for ti, (pr, p) in enumerate(target_pieces) if pr == r and ti not in used_t for i in p]
        right = [i for ci, (pr, p) in enumerate(candidate_pieces) if pr == r and ci not in used_c for i in p]
        if left and right and len(_content(left, True)) > 1 and _content(left, True) == _content(right, True):
            detail = f"the same {len(left)} instructions in a different order (candidate +{right[0].offset:#x})"
            findings.append(DiffFinding(Cause.BLOCK_ORDER, left[0].offset, detail, right[0].offset))
            continue
        if left and len(left) == len(right):
            pairs.extend(zip(left, right))
            continue
        if left:
            findings.append(_one_sided(left, "target", [right]))
        if right:
            findings.append(_one_sided(right, "candidate", [left]))
    # Two instructions that trade places across regions, such as two case tests in
    # the opposite order, are order rather than selection.
    key = lambda i: (i.mnemonic, i.operands)  # noqa: E731
    swapped = {
        n for n, (x, y) in enumerate(pairs) for m, (u, v) in enumerate(pairs)
        if n != m and key(x) == key(v) and key(y) == key(u)
    }
    for n, (x, y) in enumerate(pairs):
        cause = Cause.BLOCK_ORDER if n in swapped else _classify_pair(x, y)
        findings.append(DiffFinding(cause, x.offset, f"{x.text}  vs  {y.text}", y.offset))
    return findings


MOVES = {"mov", "movss", "movsd", "movaps", "movapd", "movq", "movd", "movzx", "movsx", "movsxd", "push", "pop"}


def _moves_data(i: Instruction) -> bool:
    """A copy, spill, reload or save that a different register allocation adds or
    drops, including the stack adjustment for a larger frame."""
    if i.mnemonic in MOVES:
        return True
    return i.mnemonic in ("add", "sub") and i.operands[:1] == (("reg", "rsp"),)


def _one_sided(run: list[Instruction], side: str, others: list[list[Instruction]]) -> DiffFinding:
    calls = [i for i in run if i.is_call]
    other_calls = any(i.is_call for o in others for i in o)
    if calls and not other_calls:
        cause = Cause.INLINING
        detail = f"{side} calls where the other side has inline code: {_run(calls)}"
    elif not calls and len(run) >= 3 and other_calls:
        cause = Cause.INLINING
        detail = f"{side} has {len(run)} inline instructions where the other side calls"
    elif all(_moves_data(i) for i in run):
        cause = Cause.REGISTER_ALLOCATION
        detail = f"{side} only, spills, reloads or copies: {_run(run[:4])}{' ...' if len(run) > 4 else ''}"
    elif len(run) <= 2:
        cause = Cause.INSTRUCTION_SELECTION
        detail = f"{side} only: {_run(run)}"
    else:
        cause = Cause.UNKNOWN
        detail = f"{side} only, {len(run)} instructions: {_run(run[:4])}{' ...' if len(run) > 4 else ''}"
    return DiffFinding(cause, run[0].offset, detail, run[0].offset if side == "candidate" else None)


class ObjectDiff:
    def diff(self, target: ObjectFunction, candidate: ObjectFunction) -> DiffResult:
        return diff_code(target, candidate)
