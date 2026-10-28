"""Find signedness-related instruction pairs in Harvest's inexact functions."""

from __future__ import annotations

import bisect
import difflib
from dataclasses import dataclass

import capstone
from capstone import x86

from concord.diff import FAMILY, ObjectFunction, diff_code, read_function
from concord.harvest import Harvest
from concord.lines import editable, inline_chains

SIGNED_JUMPS = {
    "jl": "jb",
    "jge": "jae",
    "jg": "ja",
    "jle": "jbe",
}
SIGNED_JUMPS.update({unsigned: signed for signed, unsigned in tuple(SIGNED_JUMPS.items())})
SIGNED_EXTENSIONS = {"movsx", "movsxd", "cbw", "cwde", "cdqe"}
ZERO_EXTENSIONS = {"movzx"}
ARITHMETIC_SHIFTS = {"sar", "sarx"}
LOGICAL_SHIFTS = {"shr", "shrx"}
SIGNED_DIVIDES = {"idiv"}
UNSIGNED_DIVIDES = {"div"}
SIGNED_MULTIPLIES = {"imul"}
UNSIGNED_MULTIPLIES = {"mul"}
NOPS = {"nop", "nopl", "nopw"}


@dataclass(frozen=True)
class Instruction:
    offset: int
    size: int
    mnemonic: str
    operands: tuple
    text: str
    branch_target: int | None = None


def _register(name: str) -> str:
    family = FAMILY.get(name, name)
    if family.startswith("xmm"):
        return "xmm"
    if family.startswith("ymm"):
        return "ymm"
    if family.startswith("zmm"):
        return "zmm"
    return "gpr"


def _decode(function: ObjectFunction) -> list[Instruction] | None:
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    result = []
    for instruction in decoder.disasm(function.code, 0):
        operands = []
        branch_target = None
        branch = instruction.group(x86.X86_GRP_JUMP) or instruction.group(x86.X86_GRP_CALL)
        for operand in instruction.operands:
            if operand.type == x86.X86_OP_REG:
                operands.append(("reg", _register(instruction.reg_name(operand.reg)), operand.size))
            elif operand.type == x86.X86_OP_IMM:
                operands.append(("imm", operand.imm, operand.size))
                if branch:
                    branch_target = operand.imm
            elif operand.type == x86.X86_OP_MEM:
                memory = operand.mem
                base = instruction.reg_name(memory.base) if memory.base else None
                index = instruction.reg_name(memory.index) if memory.index else None
                operands.append(
                    (
                        "mem",
                        _register(base) if base else None,
                        _register(index) if index else None,
                        memory.scale,
                        memory.disp,
                        operand.size,
                    )
                )
        result.append(
            Instruction(
                instruction.address,
                instruction.size,
                instruction.mnemonic,
                tuple(operands),
                f"{instruction.mnemonic} {instruction.op_str}".strip(),
                branch_target,
            )
        )
    if not result or result[-1].offset + result[-1].size != len(function.code):
        return None
    return result


def _shape(instruction: Instruction) -> tuple:
    operands = []
    for operand in instruction.operands:
        if operand[0] == "imm" and instruction.branch_target is not None:
            operands.append(("branch",))
        elif operand[0] == "reg":
            operands.append(("reg", operand[1]))
        else:
            operands.append(operand)
    return instruction.mnemonic, tuple(operands)


def _aligned(target: list[Instruction], candidate: list[Instruction]):
    target_code = [instruction for instruction in target if instruction.mnemonic not in NOPS]
    candidate_code = [instruction for instruction in candidate if instruction.mnemonic not in NOPS]
    matcher = difflib.SequenceMatcher(
        None,
        [_shape(instruction) for instruction in target_code],
        [_shape(instruction) for instruction in candidate_code],
        autojunk=False,
    )
    regions = []
    mapping = {}
    opcodes = matcher.get_opcodes()
    for tag, i1, i2, j1, j2 in opcodes:
        left, right = target_code[i1:i2], candidate_code[j1:j2]
        if tag == "equal":
            mapping.update((i, j) for i, j in zip(range(i1, i2), range(j1, j2), strict=True))
        else:
            if tag == "replace" and len(left) == len(right):
                pairs = list(zip(range(i1, i2), range(j1, j2), strict=True))
                mapping.update(pairs)
                regions.append((left, right, pairs))
    differing = 0
    for tag, i1, i2, j1, j2 in opcodes:
        left, right = target_code[i1:i2], candidate_code[j1:j2]
        if tag == "equal":
            differing += sum(
                not _same_instruction(a, b, target_code, candidate_code, mapping)
                for a, b in zip(left, right, strict=True)
            )
        else:
            differing += max(len(left), len(right))
    return target_code, candidate_code, regions, mapping, differing


def _same_instruction(
    left: Instruction,
    right: Instruction,
    target: list[Instruction],
    candidate: list[Instruction],
    mapping: dict[int, int],
) -> bool:
    if left.mnemonic != right.mnemonic:
        return False
    if left.branch_target is not None or right.branch_target is not None:
        return (
            left.branch_target is not None
            and right.branch_target is not None
            and _branch_targets_align(target, candidate, mapping, left, right)
        )
    return left.operands == right.operands


def _same_operand(left: tuple, right: tuple, ignore_register_size: bool = False) -> bool:
    if left[0] != right[0]:
        return False
    if left[0] == "reg":
        return left[1] == right[1] and (ignore_register_size or left[2] == right[2])
    return left == right


def _same_operands(left: Instruction, right: Instruction, extension: bool = False) -> bool:
    if len(left.operands) != len(right.operands):
        return False
    return all(
        _same_operand(a, b, ignore_register_size=extension and index == 0)
        for index, (a, b) in enumerate(zip(left.operands, right.operands, strict=True))
    )


def _branch_targets_align(
    target: list[Instruction], candidate: list[Instruction], mapping: dict[int, int], left: Instruction, right: Instruction
) -> bool:
    if left.branch_target is None or right.branch_target is None:
        return False
    target_offsets = [instruction.offset for instruction in target]
    candidate_offsets = [instruction.offset for instruction in candidate]
    target_index = bisect.bisect_left(target_offsets, left.branch_target)
    candidate_index = bisect.bisect_left(candidate_offsets, right.branch_target)
    if target_index == len(target_offsets) and left.branch_target == (target[-1].offset + target[-1].size):
        return candidate_index == len(candidate_offsets) and right.branch_target == (candidate[-1].offset + candidate[-1].size)
    return mapping.get(target_index) == candidate_index


def _is_zero_move(instruction: Instruction) -> bool:
    return (
        instruction.mnemonic == "mov"
        and len(instruction.operands) == 2
        and instruction.operands[0][0] == "reg"
        and instruction.operands[0][2] == 4
        and instruction.operands[1][0] == "reg"
        and instruction.operands[1][2] == 4
    )


def _extension_pair(left: Instruction, right: Instruction) -> bool:
    if left.mnemonic in SIGNED_EXTENSIONS and right.mnemonic in ZERO_EXTENSIONS:
        return _same_operands(left, right, extension=True)
    if right.mnemonic in SIGNED_EXTENSIONS and left.mnemonic in ZERO_EXTENSIONS:
        return _same_operands(left, right, extension=True)
    if left.mnemonic in SIGNED_EXTENSIONS and _is_zero_move(right):
        if left.mnemonic in {"cdqe", "cwde", "cbw"}:
            return True
        return (
            len(left.operands) == 2
            and left.operands[0][0] == "reg"
            and left.operands[0][2] == 8
            and left.operands[1][0] == "reg"
            and left.operands[1][2] == 4
            and _same_operands(left, right, extension=True)
        )
    if right.mnemonic in SIGNED_EXTENSIONS and _is_zero_move(left):
        return _extension_pair(right, left)
    return False


def _multiplication_pair(left: Instruction, right: Instruction) -> bool:
    if left.mnemonic not in SIGNED_MULTIPLIES | UNSIGNED_MULTIPLIES:
        return False
    if right.mnemonic not in SIGNED_MULTIPLIES | UNSIGNED_MULTIPLIES or left.mnemonic == right.mnemonic:
        return False
    if _same_operands(left, right):
        return True
    signed = left if left.mnemonic == "imul" else right
    unsigned = right if signed is left else left
    return (
        len(signed.operands) == 2
        and len(unsigned.operands) == 1
        and signed.operands[0] == ("reg", "gpr", 8)
        and _same_operand(signed.operands[1], unsigned.operands[0])
    )


def _zero_divide_prep(instruction: Instruction) -> bool:
    if instruction.mnemonic not in {"xor", "sub"} or len(instruction.operands) != 2:
        return False
    left, right = instruction.operands
    return left[0] == "reg" and left[1:] == right[1:] and left[2] in {4, 8}


def _signed_divide_prep(instruction: Instruction) -> bool:
    return instruction.mnemonic in {"cwd", "cdq", "cqo"}


def _comparison_width_pair(left: Instruction, right: Instruction) -> bool:
    if left.mnemonic != "cmp" or right.mnemonic != "cmp" or len(left.operands) != 2:
        return False
    for index, (a, b) in enumerate(zip(left.operands, right.operands, strict=True)):
        if a[0] == b[0] == "reg":
            if a[1] != b[1] or {a[2], b[2]} != {4, 8}:
                return False
        elif a[0] == b[0] == "imm":
            if a[1] != b[1]:
                return False
        elif a != b:
            return False
    return any(a[0] == b[0] == "reg" and a[2] != b[2] for a, b in zip(left.operands, right.operands, strict=True))


def _pair_kind(left: Instruction, right: Instruction, target, candidate, mapping) -> str | None:
    unsigned_jump = SIGNED_JUMPS.get(left.mnemonic)
    if unsigned_jump == right.mnemonic and _branch_targets_align(target, candidate, mapping, left, right):
        return "conditional-jump"
    if SIGNED_JUMPS.get(right.mnemonic) == left.mnemonic and _branch_targets_align(target, candidate, mapping, left, right):
        return "conditional-jump"
    if (
        left.mnemonic in ARITHMETIC_SHIFTS | LOGICAL_SHIFTS
        and right.mnemonic in ARITHMETIC_SHIFTS | LOGICAL_SHIFTS
        and left.mnemonic != right.mnemonic
        and _same_operands(left, right)
    ):
        return "shift"
    if _extension_pair(left, right):
        return "extension"
    if (
        left.mnemonic in SIGNED_DIVIDES | UNSIGNED_DIVIDES
        and right.mnemonic in SIGNED_DIVIDES | UNSIGNED_DIVIDES
        and left.mnemonic != right.mnemonic
        and _same_operands(left, right)
    ):
        return "division"
    if _multiplication_pair(left, right):
        return "multiplication"
    return None


def _pair_offsets(
    target_function: ObjectFunction,
    candidate_function: ObjectFunction,
    target_instructions: list[Instruction],
    candidate_instructions: list[Instruction],
) -> tuple[list[int], list[int]]:
    target_offsets, candidate_offsets = set(), set()
    for left, right in zip(target_instructions, candidate_instructions, strict=True):
        if left.size == right.size:
            for byte in range(left.size):
                if target_function.code[left.offset + byte] != candidate_function.code[right.offset + byte]:
                    target_offsets.add(left.offset + byte)
                    candidate_offsets.add(right.offset + byte)
        else:
            target_offsets.update(range(left.offset, left.offset + left.size))
            candidate_offsets.update(range(right.offset, right.offset + right.size))
    return sorted(target_offsets), sorted(candidate_offsets)


def _make_pair(kind, target_function, candidate_function, target_instructions, candidate_instructions):
    target_offsets, candidate_offsets = _pair_offsets(
        target_function, candidate_function, target_instructions, candidate_instructions
    )
    return {
        "kind": kind,
        "target_offsets": [instruction.offset for instruction in target_instructions],
        "candidate_offsets": [instruction.offset for instruction in candidate_instructions],
        "differing_target_offsets": target_offsets,
        "differing_candidate_offsets": candidate_offsets,
        "target": [instruction.text for instruction in target_instructions],
        "candidate": [instruction.text for instruction in candidate_instructions],
        "source": None,
    }


def _function_pairs(target_function: ObjectFunction, candidate_function: ObjectFunction):
    target = _decode(target_function)
    candidate = _decode(candidate_function)
    if target is None or candidate is None:
        return None
    target_code, candidate_code, regions, mapping, differing = _aligned(target, candidate)
    found = []
    explained = 0
    for left, right, pairs in regions:
        used: set[int] = set()
        for position, (target_index, candidate_index) in enumerate(pairs):
            if position in used:
                continue
            target_instruction, candidate_instruction = left[position], right[position]
            kind = _pair_kind(target_instruction, candidate_instruction, target_code, candidate_code, mapping)
            if kind == "conditional-jump" and position > 0:
                previous_target, previous_candidate = left[position - 1], right[position - 1]
                if _comparison_width_pair(previous_target, previous_candidate):
                    used.add(position - 1)
                    used.add(position)
                    found.append(
                        _make_pair(
                            "conditional-jump",
                            target_function,
                            candidate_function,
                            [previous_target, target_instruction],
                            [previous_candidate, candidate_instruction],
                        )
                    )
                    explained += 2
                    continue
            if kind == "division" and position > 0 and position - 1 not in used:
                previous_target, previous_candidate = left[position - 1], right[position - 1]
                prep_pair = (
                    _signed_divide_prep(previous_target) and _zero_divide_prep(previous_candidate)
                ) or (
                    _signed_divide_prep(previous_candidate) and _zero_divide_prep(previous_target)
                )
                if prep_pair:
                    used.add(position - 1)
                    found.append(
                        _make_pair(
                            "division",
                            target_function,
                            candidate_function,
                            [previous_target, target_instruction],
                            [previous_candidate, candidate_instruction],
                        )
                    )
                    explained += 2
                    used.add(position)
                    continue
            if kind:
                used.add(position)
                found.append(
                    _make_pair(
                        kind,
                        target_function,
                        candidate_function,
                        [target_instruction],
                        [candidate_instruction],
                    )
                )
                explained += 1
    return found, explained, differing


def find(harvest: Harvest, unit: str | None = None) -> list[dict]:
    """Return each inexact function with aligned signedness-related instructions."""
    rows = []
    for verdict in harvest.verdicts(unit):
        if verdict.row["exact"]:
            continue
        try:
            target = read_function(harvest.objects / "target" / f"{verdict.unit}.o", verdict.symbol)
            candidate = read_function(harvest.objects / "base" / f"{verdict.unit}.o", verdict.symbol)
        except KeyError:
            continue
        if target.code == candidate.code and target.relocations == candidate.relocations:
            continue
        result = _function_pairs(target, candidate)
        if result is None:
            continue
        pairs, explained, differing = result
        if not pairs:
            continue
        rows.append(
            {
                "unit_id": verdict.unit,
                "unit": harvest.source(verdict.unit),
                "symbol": verdict.symbol,
                "address": verdict.row["address"],
                "size": len(target.code),
                "score": diff_code(target, candidate).score,
                "explained_instruction_slots": explained,
                "differing_instruction_slots": differing,
                "explanation": explained / differing if differing else 1.0,
                "only_signedness": explained == differing,
                "pairs": pairs,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -row["explanation"],
            -row["explained_instruction_slots"],
            -row["size"],
            row["unit"],
            row["symbol"],
        ),
    )


def _add_sources(harvest: Harvest, rows: list[dict], sources: dict[str, bytes] | None = None) -> None:
    units = sorted({row["unit_id"] for row in rows})
    debug_objects = harvest.debug_objects([harvest.source(unit) for unit in units], sources)
    for row in rows:
        unit = row["unit"]
        debug = debug_objects.get(unit)
        if debug is None:
            continue
        try:
            compiled = read_function(debug, row["symbol"])
            canonical = read_function(harvest.objects / "base" / f"{row['unit_id']}.o", row["symbol"])
        except KeyError:
            continue
        if compiled.code != canonical.code or compiled.relocations != canonical.relocations:
            continue
        locations = [compiled.start + pair["candidate_offsets"][0] for pair in row["pairs"]]
        chains = inline_chains(debug, compiled.section, locations)
        for pair, chain in zip(row["pairs"], chains, strict=True):
            frame = editable(chain)
            if frame:
                pair["source"] = {"path": frame.path, "line": frame.line}
            if len(chain) > 1:
                pair["inlined_function"] = chain[0].function


def report(harvest: Harvest, unit: str | None = None) -> dict:
    """Return the signedness census with differences located through debug info."""
    rows = find(harvest, unit)
    if rows:
        _add_sources(harvest, rows)
    return {
        "schema": "concord.signedness.v1",
        "score_kind": "Concord instruction alignment",
        "functions": rows,
        "summary": {
            "functions": len(rows),
            "pairs": sum(len(row["pairs"]) for row in rows),
            "only_signedness": sum(row["only_signedness"] for row in rows),
        },
    }
