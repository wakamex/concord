"""Find inexact functions whose only code differences are mirrored comparisons."""

from __future__ import annotations

import re
from dataclasses import dataclass

import capstone
from capstone import x86

from concord.diff import ObjectFunction, diff_code, read_function
from concord.harvest import FunctionVerdict, Harvest
from concord.lines import editable, inline_chains

MIRRORED_JUMPS = {
    "je": "je",
    "jne": "jne",
    "jl": "jg",
    "jg": "jl",
    "jle": "jge",
    "jge": "jle",
    "jb": "ja",
    "ja": "jb",
    "jbe": "jae",
    "jae": "jbe",
}
FUNCTION_NAME = re.compile(
    r"(?P<name>(?:(?:[A-Za-z_]\w*::)*)(?:~?[A-Za-z_]\w*|operator\s*(?:\[\]|\(\)|[^\w\s(]+)))\s*\("
)
CLASS_NAME = re.compile(r"\b(?:class|struct)\s+(?P<name>[A-Za-z_]\w*)(?=\s*(?::|\{|$))")
CONTROL_NAMES = {"catch", "for", "if", "return", "sizeof", "switch", "while"}


@dataclass(frozen=True)
class Instruction:
    offset: int
    size: int
    mnemonic: str
    operands: tuple
    text: str


def _operand(instruction, operand) -> tuple:
    if operand.type == x86.X86_OP_REG:
        return "reg", instruction.reg_name(operand.reg), operand.size
    if operand.type == x86.X86_OP_IMM:
        return "imm", operand.imm, operand.size
    if operand.type == x86.X86_OP_MEM:
        memory = operand.mem
        return (
            "mem",
            instruction.reg_name(memory.segment) if memory.segment else None,
            instruction.reg_name(memory.base) if memory.base else None,
            instruction.reg_name(memory.index) if memory.index else None,
            memory.scale,
            memory.disp,
            operand.size,
        )
    return "other", operand.type, operand.size


def _decode(code: bytes) -> list[Instruction] | None:
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    instructions = [
        Instruction(
            instruction.address,
            instruction.size,
            instruction.mnemonic,
            tuple(_operand(instruction, operand) for operand in instruction.operands),
            f"{instruction.mnemonic} {instruction.op_str}".strip(),
        )
        for instruction in decoder.disasm(code, 0)
    ]
    if not instructions or instructions[-1].offset + instructions[-1].size != len(code):
        return None
    return instructions


def _flipped_pair(target: list[Instruction], candidate: list[Instruction], index: int) -> bool:
    left, right = target[index], candidate[index]
    if (
        left.mnemonic != "cmp"
        or right.mnemonic != "cmp"
        or len(left.operands) != 2
        or left.operands != right.operands[::-1]
        or left.operands == right.operands
        or index + 1 >= len(target)
    ):
        return False
    target_jump, candidate_jump = target[index + 1], candidate[index + 1]
    return (
        MIRRORED_JUMPS.get(target_jump.mnemonic) == candidate_jump.mnemonic
        and target_jump.operands == candidate_jump.operands
    )


def _relocation_bytes(function: ObjectFunction) -> set[int] | None:
    """Mask x86-64 relocation fields, which are four-byte fields in Harvest text."""
    masked = set()
    for offset in function.relocations:
        if offset < 0 or offset + 4 > len(function.code):
            return None
        masked.update(range(offset, offset + 4))
    return masked


def _bad_function_references(verdict: FunctionVerdict, candidate: ObjectFunction) -> bool:
    start, end = candidate.start, candidate.start + len(candidate.code)
    return any(start <= int(reference["offset"]) < end for reference in verdict.section.get("bad_references", []))


def _source_inline_function(root, source: dict) -> str | None:
    path = root / source["path"]
    if path.suffix not in {".h", ".hpp", ".tcc"} or not path.is_file():
        return None
    text = path.read_text(errors="replace")

    def preserve_lines(match) -> str:
        return "\n" * match.group().count("\n")

    text = re.sub(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/',
        preserve_lines,
        text,
        flags=re.DOTALL,
    )
    lines = text.splitlines()
    target = int(source["line"]) - 1
    if not 0 <= target < len(lines):
        return None

    for start in range(target, max(-1, target - 120), -1):
        header = "\n".join(lines[start : min(target + 1, start + 20)])
        match = FUNCTION_NAME.search(header)
        if not match or match["name"].split("::")[-1].strip() in CONTROL_NAMES:
            continue
        delimiter = re.search(r"[;{}]", header[match.end() :])
        if delimiter is None or delimiter.group() != "{":
            continue
        opening = match.end() + delimiter.start()
        body_through_target = header[opening:]
        depth = body_through_target.count("{") - body_through_target.count("}")
        if depth <= 0:
            continue

        name = match["name"].replace(" ", "")
        if "::" in name:
            return name
        for class_start in range(start, max(-1, start - 400), -1):
            class_header = "\n".join(lines[class_start : min(start + 1, class_start + 20)])
            owner = CLASS_NAME.search(class_header)
            if not owner:
                continue
            owner_delimiter = re.search(r"[;{}]", class_header[owner.end() :])
            if owner_delimiter is None or owner_delimiter.group() != "{":
                continue
            class_open = owner.end() + owner_delimiter.start()
            class_to_function = class_header[class_open:]
            if class_to_function.count("{") > class_to_function.count("}"):
                return f"{owner['name']}::{name}"
        return name
    return None


def _offsets(functions: tuple[ObjectFunction, ObjectFunction], allowed: set[int]) -> list[int]:
    target, candidate = functions
    return [
        offset
        for offset, (left, right) in enumerate(zip(target.code, candidate.code, strict=True))
        if left != right and offset in allowed
    ]


def _function_flips(
    verdict: FunctionVerdict, target: ObjectFunction, candidate: ObjectFunction
) -> list[dict] | None:
    if (
        not target.code
        or len(target.code) != len(candidate.code)
        or target.relocations != candidate.relocations
        or verdict.row.get("exact_but_unknown")
        or _bad_function_references(verdict, candidate)
    ):
        return None
    relocated = _relocation_bytes(target)
    target_code, candidate_code = _decode(target.code), _decode(candidate.code)
    if relocated is None or target_code is None or candidate_code is None:
        return None
    if len(target_code) != len(candidate_code) or any(
        (left.offset, left.size) != (right.offset, right.size)
        for left, right in zip(target_code, candidate_code, strict=True)
    ):
        return None

    pairs = [index for index in range(len(target_code) - 1) if _flipped_pair(target_code, candidate_code, index)]
    used = {index + step for index in pairs for step in (0, 1)}
    if not pairs or len(used) != len(pairs) * 2:
        return None

    allowed = set(relocated)
    for index in pairs:
        for instruction in (target_code[index], target_code[index + 1]):
            allowed.update(range(instruction.offset, instruction.offset + instruction.size))
    if any(left != right and offset not in allowed for offset, (left, right) in enumerate(zip(target.code, candidate.code, strict=True))):
        return None

    flips = []
    for index in pairs:
        comparison, jump = target_code[index], target_code[index + 1]
        byte_offsets = _offsets(
            (target, candidate),
            set(range(comparison.offset, comparison.offset + comparison.size))
            | set(range(jump.offset, jump.offset + jump.size)),
        )
        flips.append(
            {
                "comparison_offset": comparison.offset,
                "jump_offset": jump.offset,
                "differing_offsets": byte_offsets,
                "target": f"{comparison.text}; {jump.text}",
                "candidate": f"{candidate_code[index].text}; {candidate_code[index + 1].text}",
                "inlined_function": None,
                "source": None,
            }
        )
    return flips


def find(harvest: Harvest) -> list[dict]:
    """Return each inexact function whose only byte differences are flipped compare/jump pairs."""
    observations = []
    for verdict in harvest.verdicts():
        if verdict.row["exact"]:
            continue
        try:
            target = read_function(harvest.objects / "target" / f"{verdict.unit}.o", verdict.symbol)
            candidate = read_function(harvest.objects / "base" / f"{verdict.unit}.o", verdict.symbol)
        except KeyError:
            continue
        flips = _function_flips(verdict, target, candidate)
        if flips:
            observations.append(
                {
                    "unit_id": verdict.unit,
                    "unit": harvest.source(verdict.unit),
                    "symbol": verdict.symbol,
                    "address": verdict.row["address"],
                    "size": len(target.code),
                    "score": diff_code(target, candidate).score,
                    "flips": flips,
                }
            )

    found = {}
    for row in observations:
        address = int(row["address"], 0)
        function = found.get(address)
        if function is None:
            row["units"] = [row["unit"]]
            row["unit_occurrences"] = 1
            row["flips"] = [dict(flip, units=[row["unit"]]) for flip in row["flips"]]
            found[address] = row
            continue
        if function["size"] != row["size"]:
            raise ValueError(f"target function at {row['address']} has inconsistent sizes")
        function["units"].append(row["unit"])
        function["unit_occurrences"] += 1
        known = {
            (flip["comparison_offset"], flip["jump_offset"], tuple(flip["differing_offsets"]), flip["target"], flip["candidate"]): flip
            for flip in function["flips"]
        }
        for flip in row["flips"]:
            key = (
                flip["comparison_offset"],
                flip["jump_offset"],
                tuple(flip["differing_offsets"]),
                flip["target"],
                flip["candidate"],
            )
            if key in known:
                known[key]["units"].append(row["unit"])
            else:
                merged = dict(flip, units=[row["unit"]])
                function["flips"].append(merged)
                known[key] = merged
    for function in found.values():
        function["units"] = sorted(set(function["units"]))
        for flip in function["flips"]:
            flip["units"] = sorted(set(flip["units"]))
    return sorted(found.values(), key=lambda row: (-row["size"], row["unit"], row["symbol"]))


def _add_sources(harvest: Harvest, functions: list[dict]) -> None:
    units = sorted({row["unit_id"] for row in functions})
    objects = harvest.debug_objects([harvest.source(unit) for unit in units])
    for row in functions:
        debug = objects.get(row["unit"])
        if debug is None:
            continue
        try:
            compiled = read_function(debug, row["symbol"])
            canonical = read_function(
                harvest.objects / "base" / f"{row['unit_id']}.o", row["symbol"]
            )
        except KeyError:
            continue
        if compiled.code != canonical.code or compiled.relocations != canonical.relocations:
            continue
        locations = [compiled.start + flip["comparison_offset"] for flip in row["flips"]]
        chains = inline_chains(debug, compiled.section, locations)
        for flip, chain in zip(row["flips"], chains, strict=True):
            frame = editable(chain)
            if frame:
                flip["source"] = {"path": frame.path, "line": frame.line}
                flip["inlined_function"] = _source_inline_function(harvest.root, flip["source"])
            if not flip["inlined_function"] and len(chain) > 1:
                flip["inlined_function"] = chain[0].function


def report(harvest: Harvest) -> dict:
    """Return the build census with Concord scores and debug-backed inline sources."""
    functions = find(harvest)
    if functions:
        _add_sources(harvest, functions)
    summary = {
        "functions": len(functions),
        "bytes": sum(row["size"] for row in functions),
        "single_flip_functions": sum(len(row["flips"]) == 1 for row in functions),
        "flips": sum(len(row["flips"]) for row in functions),
        "unit_occurrences": sum(row["unit_occurrences"] for row in functions),
    }
    return {
        "schema": "concord.comparison-flips.v1",
        "score_kind": "Concord instruction alignment",
        "summary": summary,
        "functions": functions,
    }
