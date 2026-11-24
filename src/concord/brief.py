"""A brief of one open function for whoever writes its source: each source site of its primary causes, most
causes first, with the differing instructions, the source around the site, the known idioms for those kinds of
cause, and the commands that check a change.

The sites come from concord causes. On real fixes from the targets' histories, the first site held the fix in
about three cases of four where something structural differs; where only register allocation differs, the place
the registers start to differ does not show where to edit, and the brief says so (docs/causes.md).
"""

from __future__ import annotations

from pathlib import Path

from concord import causes, knowledge
from concord.model import Cause

# the kinds of cause concord causes names, as the knowledge base's causes
KIND_CAUSES = {
    "frame": Cause.STACK_LAYOUT,
    "slots": Cause.STACK_LAYOUT,
    "calls": Cause.INLINING,
    "register-allocation": Cause.REGISTER_ALLOCATION,
    "control-flow": Cause.BLOCK_ORDER,
    "field": Cause.STRUCT_LAYOUT,
    "constant": Cause.IMMEDIATE_OR_RELOC,
    "operation": Cause.INSTRUCTION_SELECTION,
    "instruction": Cause.INSTRUCTION_SELECTION,
}


def _text(root: Path, path: str) -> list[str]:
    file = Path(path) if Path(path).is_absolute() else root / path
    try:
        return file.read_text(errors="replace").splitlines()
    except OSError:
        return []


def excerpt(lines: list[str], marked: set[int], context: int) -> list[str]:
    """The lines around the marked ones, numbered, with the marked lines flagged."""
    if not lines or not marked:
        return []
    first, last = max(1, min(marked) - context), min(len(lines), max(marked) + context)
    return [f"{'>' if n in marked else ' '} {n:5}  {lines[n - 1]}" for n in range(first, last + 1)]


def window(instructions: list, offsets: list[int], around: int = 3) -> list[str]:
    """The instructions from `around` before the first of `offsets` to `around` after the last, as text, with
    the instructions at the offsets flagged."""
    if not offsets:
        return []
    at = {i.offset: n for n, i in enumerate(instructions)}
    found = [at[o] for o in offsets if o in at]
    if not found:
        return []
    first, last = max(0, min(found) - around), min(len(instructions), max(found) + around + 1)
    flagged = set(found)
    return [f"{'>' if n in flagged else ' '} {instructions[n].offset:5x}  {instructions[n].text}" for n in range(first, last)]


def side_by_side(left: list[str], right: list[str], width: int = 46) -> list[str]:
    rows = [f"{'retail':{width}}  ours"]
    for n in range(max(len(left), len(right))):
        a = left[n] if n < len(left) else ""
        rows.append(f"{a:{width}}  {right[n] if n < len(right) else ''}".rstrip())
    return rows


def render(row: dict, root: Path, compiler: str, target_flag: str, context: int = 3,
           code: tuple[list, list] | None = None, dip: bool = False) -> str:
    """The brief of one row of concord causes, as text. `code` is retail's and our instructions, for the
    instructions around each site; `dip` marks a function the project counts as matched that differs now."""
    counts = (f"{len(row['primaries'])} primary causes at {len(row['sites'])} sites; {row['differences']} differing rows, "
              f"{sum(row['fallout'].values())} of them fallout")
    out = [f"{row['symbol']} in {row['unit']}: {row['bytes']} bytes, score {row['score']}", counts]
    if dip:
        out.append("a dip: the project counts this function as matched because its source compiled to the original "
                   "in an earlier build, so the difference likely comes from outside the function (the unit's "
                   "declarations or a header) rather than from its body")
    if row.get("header"):
        out.append("defined outside the unit's source: the same function may be open in other units that compile it")
    only_registers = [p["kind"] for p in row["primaries"]] == ["register-allocation"]
    if only_registers:
        out.append("only register allocation differs: the line below is where registers start to differ, and the edit "
                   "is often some lines away (a temporary named or inlined, a statement moved)")
    for number, site in enumerate(row["sites"], 1):
        found = [row["primaries"][i] for i in site]
        placed = {p["line"] for p in found if p["line"]}
        file = next((p["file"] for p in found if p["file"]), None)
        where = f"{file} line {min(placed)}" + (f" to {max(placed)}" if len(placed) > 1 else "") if file else "no source line"
        out += ["", f"site {number}: {where}"]
        out += [f"  {p['kind']}: {p['detail']}" for p in found]
        out += [f"  {line}" for line in excerpt(_text(root, file), placed, context)] if file else []
        if code:
            retail = window(code[0], sorted({p["target_offset"] for p in found if p.get("target_offset") is not None}))
            ours = window(code[1], sorted({p["candidate_offset"] for p in found if p.get("candidate_offset") is not None}))
            if retail or ours:
                out += [f"  {line}" for line in side_by_side(retail, ours)]
    kinds = {p["kind"] for p in row["primaries"]}
    idioms = knowledge.for_causes(knowledge.load(compiler), {KIND_CAUSES[k] for k in kinds if k in KIND_CAUSES})
    if idioms:
        out += ["", f"known {compiler} idioms for these kinds of cause:"]
        out += [f"  {i.id} ({i.cause.value}, {i.status}): {i.fix}" for i in idioms]
    out += ["", "to check a change:",
            f"  concord causes {target_flag} --unit {row['unit']} '{row['symbol']}'",
            f"  concord diff {target_flag} --unit {row['unit']} '{row['symbol']}'"]
    return "\n".join(out) + "\n"


def brief(target, unit: str, symbol: str, compiler: str, target_flag: str, context: int = 3) -> str:
    """The brief of one open function of the target's last build."""
    from concord.diff import disassemble, read_functions

    verdict = target.verdict(unit, symbol)
    rows = [row for row in causes.run(target, [verdict.unit])["functions"] if row["symbol"] == symbol]
    if not rows:
        raise KeyError(f"{symbol} is not open in {unit}")
    retail, ours = target.object_pair(verdict)
    code = tuple(disassemble(read_functions(path, {symbol})[symbol]) for path in (retail, ours))
    fuzzy = verdict.row.get("fuzzy")
    dip = bool(verdict.row.get("exact")) and fuzzy is not None and fuzzy < 100.0
    return render(rows[0], target.root, compiler, target_flag, context, code, dip)
