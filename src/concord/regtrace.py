"""Explain a gruntz-decomp function's register differences from MSVC 5.0's own register choices.

c2.exe gives each temporary its register while it selects a block's instructions (its local
allocator, REGTRACE_GRANT in gruntz_driver.py): a register the temporary prefers (a copy partner's)
when that is free, otherwise the next free one of eax, ecx, edx in round-robin order from a cursor
reset at the start of each function, otherwise the first free one in a fixed order. Under winedbg the
driver logs every function start and every grant. Grants come in selection order, which is not the
order of the code, so each grant of the function is tied to its instruction by running c2 again with
that grant given another register and seeing which instruction of the function changes. A round-robin
grant one step past retail's register means our code makes one more round-robin grant before it than
the original did; one step short, one fewer. Running c2 once more with every differing grant given
retail's register shows how much of the function's difference the allocator's choices account for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from concord import coff
from concord.diff import _family, disassemble, read_function

REGISTERS = {1: "eax", 2: "ecx", 3: "edx", 4: "ebx", 5: "esp", 6: "ebp", 7: "esi", 8: "edi"}
NUMBERS = {name: number for number, name in REGISTERS.items()}
ROTATION = ["eax", "ecx", "edx"]
# return addresses into c2's register picker (0x435C37) naming the rule that chose the register
PATHS = {0x435CF9: "round-robin", 0x435DB5: "first free"}
WORD = re.compile(r"^\s*([0-9a-f]{8})\s*$")
DUMP = re.compile(r"^\s*0x[0-9a-f]+(?: \S+)?:\s+((?:[0-9a-f]{8}\s*)+)$")


@dataclass
class Grant:
    index: int  # the grant's place among all of the unit's grants, counting from 1, for regforce
    function: int  # the function's place in c2's compile order
    register: str
    value: int  # the temporary's number
    path: str
    hint: int  # register number the temporary prefers, 0 for none


def parse(log: str) -> tuple[list[Grant], int]:
    """The grants in winedbg's log, each tagged with the function c2 was generating code for, and how many
    functions it generated. A function is known by its record's address; records are counted in order of
    first appearance, which is the order the object defines the functions in."""
    grants, records, current = [], {}, None
    for block in log.split("Stopped on breakpoint ")[1:]:
        if "has terminated" in block:
            block = block[: block.find("has terminated")]
        breakpoint_, rest = block.split(None, 1)
        lines = rest.splitlines()
        words = [m[1] for line in lines if (m := WORD.match(line))]
        if breakpoint_ == "1":
            current = records.setdefault(int(words[0], 16), len(records)) if words else current
            continue
        registers = dict(re.findall(r"\b(E[A-Z]X):([0-9a-f]{8})", rest))
        dump = [int(w, 16) for line in lines if (m := DUMP.match(line)) for w in m[1].split()]
        returned = int(words[1], 16) if len(words) > 1 else 0
        if current is None or "ECX" not in registers or len(dump) < 12:
            continue
        grants.append(Grant(len(grants) + 1, current, REGISTERS.get(int(registers["ECX"], 16), "?"), dump[7],
                            PATHS.get(returned, f"other ({returned:#x})"), dump[11]))  # fmt: skip
    return grants, len(records)


def changed(before: list, after: list) -> list:
    """Our instructions whose text differs between two compiles of one function, when its length holds."""
    return [a for a, b in zip(before, after, strict=False) if a.text != b.text] if len(before) == len(after) else []


def writes(instruction, register: str) -> bool:
    """Whether the instruction's first operand is the register, as a destination."""
    return instruction.mnemonic not in {"cmp", "test", "push", "call", "jmp"} and instruction.operands[:1] == (("reg", _family(register)),)


def retail_registers(findings) -> dict[int, dict[str, str]]:
    """Our instruction offset -> {our register: retail's}, from concord diff's register findings
    ("eax->ecx" is retail's eax where ours has ecx)."""
    found: dict[int, dict[str, str]] = {}
    for finding in findings:
        if finding.cause.value != "register-allocation":
            continue
        swaps = dict(reversed(pair.split("->")) for pair in re.findall(r"\b[a-z]{2,3}->[a-z]{2,3}\b", finding.detail))
        if not swaps:
            continue
        for offset in finding.candidate_offsets or (finding.candidate,):
            found.setdefault(offset, {}).update(swaps)
    return found


def step(ours: str, retail: str) -> int | None:
    """How many places ours lies past retail's register in the round-robin order."""
    if ours not in ROTATION or retail not in ROTATION:
        return None
    return (ROTATION.index(ours) - ROTATION.index(retail)) % 3 or None


def other(register: str) -> str:
    """A register to give a grant in place of its own, to see which instruction holds it."""
    return ROTATION[(ROTATION.index(register) + 1) % 3] if register in ROTATION else "eax"


def run(target, unit: str, symbol: str, source: bytes, progress=None) -> dict:
    """Trace c2's register grants for one function of the unit, tie each to its instruction, compare with
    retail's registers, and score the function with every differing grant given retail's register."""
    log, evaluation, il = target.regtrace(unit, source)
    if evaluation.error or evaluation.object is None or il is None:
        raise RuntimeError(f"the unit did not compile: {evaluation.error[-2000:]}")
    grants, generated = parse(log)
    raw = evaluation.object.with_name("capture.raw.obj")
    order = list(coff.functions((raw if raw.exists() else evaluation.object).read_bytes()))
    if generated != len(order):
        raise RuntimeError(f"c2 generated {generated} functions and the object defines {len(order)}")
    if symbol not in order:
        raise KeyError(f"{symbol} is not a function of {unit}'s object")
    ours = disassemble(read_function(evaluation.object, symbol))
    verdict = evaluation.verdict(symbol)
    findings = target.diff(verdict, evaluation.object).findings if verdict and not verdict.row["exact"] else []
    retail = retail_registers(findings)
    rows = []
    for grant in (g for g in grants if g.function == order.index(symbol)):
        moved = target.regforce(unit, il, {grant.index: other(grant.register)})
        hit = changed(ours, disassemble(read_function(moved.object, symbol))) if moved.object else []
        first = next((ins for ins in hit if writes(ins, grant.register)), hit[0] if hit else None)
        theirs = retail.get(first.offset, {}).get(grant.register) if first else None
        rotation = step(grant.register, theirs) if theirs and grant.path == "round-robin" else None
        rows.append({
            "grant": grant.index, "value": grant.value, "register": grant.register, "path": grant.path,
            "hint": REGISTERS.get(grant.hint), "offset": first.offset if first else None,
            "instruction": first.text if first else None, "retail": theirs,
            "reading": None if rotation is None else
            "one more round-robin grant before this one than the original" if rotation == 1 else
            "one fewer round-robin grant before this one than the original",
        })  # fmt: skip
        if progress:
            progress(rows[-1])
    retail_choice = {row["grant"]: row["retail"] for row in rows if row["retail"]}
    with_retail = None
    if retail_choice:
        forced = target.regforce(unit, il, retail_choice).verdict(symbol)
        with_retail = {"fuzzy": forced.row["fuzzy"], "exact": forced.row["exact"]} if forced else None
    return {"symbol": symbol, "score": verdict.row["fuzzy"] if verdict else None, "grants": rows,
            "forced": retail_choice, "with_retail_registers": with_retail}  # fmt: skip
