"""Sort a gruntz-decomp checkout's open functions by the least normalization that makes them equal to retail.

A byte score counts every instruction a difference touches, so one swapped callee-saved register or one
shifted stack slot can drag a function far below the near misses while the cause is a single source choice.
Each open function's instructions are compared with retail's under progressively looser views, and the
function is filed under the first view in which the two agree:

- registers: the same instructions with the registers renamed by one consistent mapping (a swap of esi and
  edi throughout), or with registers differing in a way no single renaming explains;
- stack: also every displacement from esp or ebp ignored (locals laid out in another order);
- branches: also every conditional jump taken as one mnemonic (a condition written the other way round);
- opcodes: the same mnemonic sequence with every operand ignored.

The rest differ in their instruction sequences. For those, the differing stretches of the opcode sequence
are counted, and a function with one stretch gets a signature (the two sides' mnemonics in that stretch) so
that functions sharing one difference can be grouped and fixed together.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher

from concord.diff import STACK, Instruction, disassemble, read_functions

LEVELS = ("registers", "stack", "branches", "opcodes")
FRAME = {"rsp", "rbp"} | STACK


def _operand(operand: tuple, registers: bool, stack: bool) -> tuple:
    if operand[0] == "reg":
        return ("reg",) if registers else operand
    if operand[0] == "mem":
        _, base, index, scale, disp, size = operand
        if stack and base in FRAME:
            disp = None
        if registers:
            base = "frame" if base in FRAME else (None if base is None else "gpr")
            index = None if index is None else "gpr"
        return ("mem", base, index, scale, disp, size)
    return operand


def view(instruction: Instruction, level: str | None) -> tuple:
    """The instruction as `level` compares it; None compares everything."""
    if level == "opcodes":
        return (instruction.mnemonic,)
    depth = -1 if level is None else LEVELS.index(level)
    mnemonic = "jcc" if depth >= 2 and instruction.mnemonic.startswith("j") and instruction.mnemonic != "jmp" else instruction.mnemonic
    return (mnemonic, tuple(_operand(o, depth >= 0, depth >= 1) for o in instruction.operands))


def renaming(ours: list[Instruction], retail: list[Instruction]) -> dict[str, str] | None:
    """The one register mapping that turns our instructions into retail's, when the two differ only in
    registers and one consistent mapping explains every difference; None otherwise."""
    mapping, used = {}, {}
    for a, b in zip(ours, retail):
        left = [r for o in a.operands for r in ((o[1],) if o[0] == "reg" else o[1:3] if o[0] == "mem" else ()) if r]
        right = [r for o in b.operands for r in ((o[1],) if o[0] == "reg" else o[1:3] if o[0] == "mem" else ()) if r]
        if len(left) != len(right):
            return None
        for x, y in zip(left, right):
            if mapping.setdefault(x, y) != y or used.setdefault(y, x) != x:
                return None
    return {x: y for x, y in mapping.items() if x != y}


def classify(ours: list[Instruction], retail: list[Instruction]) -> dict:
    """The first view under which the function agrees with retail, the registers' renaming when one explains
    a register-only difference, and for the rest the differing stretches of the opcode sequence."""
    if len(ours) == len(retail):
        for level in LEVELS:
            if all(view(a, level) == view(b, level) for a, b in zip(ours, retail)):
                found = {"level": level}
                if level == "registers":
                    swap = renaming(ours, retail)
                    found["renaming"] = swap
                return found
    left, right = [view(i, "opcodes")[0] for i in ours], [view(i, "opcodes")[0] for i in retail]
    stretches = [op for op in SequenceMatcher(None, left, right, autojunk=False).get_opcodes() if op[0] != "equal"]
    found = {"level": "sequence", "stretches": len(stretches),
             "instructions": sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in stretches)}  # fmt: skip
    if len(stretches) == 1:
        _, i1, i2, j1, j2 = stretches[0]
        found["signature"] = f"{' '.join(left[i1:i2]) or '-'} / {' '.join(right[j1:j2]) or '-'}"
    return found


def census(target) -> dict:
    """Every open function of the checkout's last build, classified, with totals per view and the
    single-stretch signatures shared by more than one function."""
    pair = (target.root / "build/objdiff/compare-new")
    rows, wanted = [], defaultdict(list)
    for verdict in target.verdicts():
        if not verdict.row["exact"]:
            wanted[verdict.unit].append(verdict)
    for unit, verdicts in sorted(wanted.items()):
        symbols = {v.symbol for v in verdicts}
        try:
            retail = read_functions(pair / "target" / f"{unit}.c.obj", symbols)
            ours = read_functions(pair / "base" / f"{unit}.obj", symbols)
        except (OSError, ValueError, KeyError):
            retail, ours = {}, {}
        for verdict in verdicts:
            row = {"unit": unit, "symbol": verdict.symbol, "size": verdict.row["size"], "score": verdict.row["fuzzy"]}
            if verdict.symbol in retail and verdict.symbol in ours:
                row |= classify(disassemble(ours[verdict.symbol]), disassemble(retail[verdict.symbol]))
            else:
                row["level"] = "missing"
            rows.append(row)
    levels = Counter(row["level"] for row in rows)
    signatures = Counter(row["signature"] for row in rows if "signature" in row)
    return {
        "functions": len(rows),
        "levels": {level: {"functions": n, "bytes": sum(r["size"] for r in rows if r["level"] == level)} for level, n in levels.most_common()},
        "shared": [{"signature": s, "functions": n} for s, n in signatures.most_common() if n > 1],
        "rows": sorted(rows, key=lambda r: (r["level"] != "registers", r["level"], -r["score"])),
    }
