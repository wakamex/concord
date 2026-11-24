"""Primary causes of a function's differences, separated from their fallout.

concord's diff attributes every differing instruction to a cause, so a function a few lines away from a
match can show hundreds of differences: one extra local grows the stack frame and moves every stack offset,
one extra out-of-line call shifts what follows, and any structural change reshuffles register choices across
the whole function. An author or a search that works through those rows one by one chases fallout.

This module reads the same findings and keeps the primary ones:

- the stack frame's size, when the prologue allocates a different amount; every stack-only displacement
  difference is then its fallout, since a frame of another size moves locals and arguments alike;
- without a frame difference, stack-only displacement differences are one cause together: the same locals in
  other slots;
- each callee called a different number of times, from the two functions' relocations; one-sided call rows are
  then fallout;
- every other structural difference (an exit or branch of another shape, a different constant, a different
  instruction), with neighbouring rows grouped into one site;
- register-only differences, whose instructions have the same shape on both sides, are fallout whenever the
  function has any structural cause, since register allocation is decided over the whole function; with none,
  register allocation is the function's one primary cause.

Each primary cause carries a prescription naming what differs, and the source line where line records exist.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from concord.diff import Instruction, ObjectFunction, disassemble, shape
from concord.model import Cause, DiffFinding

STACK = {"rsp", "rbp"}  # register families: the diff names esp and ebp by their 64-bit family
GROUP = 16  # bytes: neighbouring rows of one kind closer than this are one site
CONTROL = {"ret", "retn", "jmp"}
# the order primaries are reported in: structure that shifts everything after it first
ORDER = ["frame", "calls", "control-flow", "constant", "slots", "instruction", "register-allocation"]


@dataclass
class Primary:
    kind: str  # frame, calls, slots, control-flow, constant, instruction, register-allocation
    detail: str
    target_offset: int | None = None
    candidate_offset: int | None = None
    rows: int = 1
    line: int | None = None


@dataclass
class Analysis:
    primaries: list[Primary] = field(default_factory=list)
    fallout: Counter = field(default_factory=Counter)  # reason -> differing rows explained by a primary
    differences: int = 0


def frame_size(instructions: list[Instruction], function: ObjectFunction) -> int | None:
    """The bytes the prologue allocates: `sub esp, N`, or the size handed to a stack probe."""
    probe = {offset for offset, name in function.targets if "chkstk" in name or "alloca_probe" in name}
    for index, ins in enumerate(instructions[:16]):
        if ins.mnemonic == "sub" and len(ins.operands) == 2 and ins.operands[0] == ("reg", "rsp") and ins.operands[1][0] == "imm":
            return ins.operands[1][1]
        if ins.is_call and any(ins.offset <= offset < ins.offset + ins.size for offset in probe):
            for earlier in reversed(instructions[:index]):
                if earlier.mnemonic == "mov" and len(earlier.operands) == 2 and earlier.operands[1][0] == "imm":
                    return earlier.operands[1][1]
    return None


def calls(instructions: list[Instruction], function: ObjectFunction) -> Counter:
    """Each callee by name, as many times as the function calls it. A relocation against the function's own
    name or a section symbol is how some objects reach code in the same section, not a callee."""
    found = Counter()
    for ins in instructions:
        if ins.is_call:
            for offset, name in function.targets:
                if ins.offset <= offset < ins.offset + ins.size:
                    callee = name.split("+")[0]
                    if callee != function.name and not callee.startswith((".", "$")):
                        found[callee] += 1
                    break
    return found


def _stack_only(a: Instruction, b: Instruction) -> bool:
    """Whether two instructions differ only in the displacement of stack-based memory operands."""
    if a.mnemonic != b.mnemonic or len(a.operands) != len(b.operands):
        return False
    differs = False
    for x, y in zip(a.operands, b.operands):
        if x == y:
            continue
        if x[0] == y[0] == "mem" and x[1] == y[1] and x[1] in STACK and x[2:4] == y[2:4] and x[5] == y[5]:
            differs = True
            continue
        return False
    return differs


def _frame_shift(a: Instruction, b: Instruction, delta: int) -> bool:
    """Whether two instructions differ only where the frame's size shows: a stack-pointer adjustment, or any
    displacement or constant, off by exactly the difference in frame size."""
    if a.mnemonic != b.mnemonic or len(a.operands) != len(b.operands):
        return False
    differs = False
    for x, y in zip(a.operands, b.operands):
        if x == y:
            continue
        if x[0] == y[0] == "imm" and y[1] - x[1] == delta or x[0] == y[0] == "mem" and x[1:4] == y[1:4] and x[5] == y[5] and y[4] - x[4] == delta:
            differs = True
        else:
            return False
    return differs


def _constant_only(a: Instruction, b: Instruction) -> bool:
    if a.mnemonic != b.mnemonic or len(a.operands) != len(b.operands):
        return False
    pairs = [(x, y) for x, y in zip(a.operands, b.operands) if x != y]
    return bool(pairs) and all(x[0] == y[0] == "imm" for x, y in pairs)


def _spill(ins: Instruction) -> bool:
    """A register saved to or reloaded from the stack: a spill, reload or callee-saved push or pop."""
    if ins.mnemonic in ("push", "pop") and ins.operands and ins.operands[0][0] == "reg":
        return True
    if ins.mnemonic == "mov" and len(ins.operands) == 2:
        kinds = {o[0] for o in ins.operands}
        return kinds == {"reg", "mem"} and any(o[0] == "mem" and o[1] in STACK and o[2] is None for o in ins.operands)
    return False


def table_start(instructions: list[Instruction], function: ObjectFunction) -> int | None:
    """Where a jump table embedded after the code begins: the first relocation against the function itself that
    lies past its last return, since a switch's table holds addresses in the function."""
    rets = [i.offset for i in instructions if i.mnemonic in ("ret", "retn")]
    if not rets:
        return None
    own = sorted(offset for offset, name in function.targets if name.split("+")[0] == function.name and offset > rets[-1])
    return own[0] if own else None


def _rows(finding: DiffFinding, by_offset: dict[int, Instruction], offsets: tuple[int, ...], start: int | None) -> list[Instruction]:
    if offsets:
        return [by_offset[o] for o in offsets if o in by_offset]
    return [by_offset[start]] if start in by_offset else []


def analyse(target: ObjectFunction, candidate: ObjectFunction, findings: list[DiffFinding]) -> Analysis:
    ours, theirs = disassemble(candidate), disassemble(target)
    at_target = {i.offset: i for i in theirs}
    at_candidate = {i.offset: i for i in ours}
    result = Analysis(differences=sum(f.rows for f in findings))

    frames = frame_size(theirs, target), frame_size(ours, candidate)
    frame = frames[0] is not None and frames[1] is not None and frames[0] != frames[1]
    if frame:
        delta = frames[1] - frames[0]
        result.primaries.append(Primary(
            "frame", f"the stack frame is {abs(delta)} bytes {'larger' if delta > 0 else 'smaller'} than retail's "
            f"({frames[1]:#x} against {frames[0]:#x}): {'an extra' if delta > 0 else 'a missing'} local, temporary or "
            "aggregate home", 0, 0))

    retail_calls, our_calls = calls(theirs, target), calls(ours, candidate)
    for callee in sorted(retail_calls.keys() | our_calls.keys()):
        if retail_calls[callee] != our_calls[callee]:
            more = our_calls[callee] - retail_calls[callee]
            result.primaries.append(Primary(
                "calls", f"{abs(more)} {'more' if more > 0 else 'fewer'} calls to {callee} than retail "
                f"({our_calls[callee]} against {retail_calls[callee]}): a helper inlined or kept out of line "
                "differently, or a call added or missing"))
    call_set = any(p.kind == "calls" for p in result.primaries)

    tables = table_start(theirs, target), table_start(ours, candidate)
    register_rows, slot_rows, spill_rows, sites = 0, 0, 0, []
    for finding in findings:
        a_rows = _rows(finding, at_target, finding.target_offsets, finding.offset)
        b_rows = _rows(finding, at_candidate, finding.candidate_offsets, finding.candidate)
        if (tables[0] is not None and finding.offset >= tables[0]) or (
                tables[1] is not None and finding.candidate is not None and finding.candidate >= tables[1]):
            result.fallout["jump table data"] += finding.rows
            continue
        if finding.cause == Cause.PADDING or (a_rows + b_rows and all(i.text in ("mov edi, edi", "nop") for i in a_rows + b_rows)):
            result.fallout["padding"] += finding.rows
            continue
        if frame and any(i.offset < 0x40 and i.mnemonic == "sub" and i.operands[:1] == (("reg", "rsp"),) for i in a_rows + b_rows):
            result.fallout["frame prologue"] += finding.rows
            continue
        if frame and not finding.candidate_offsets and not finding.target_offsets and finding.cause == Cause.STACK_LAYOUT:
            result.fallout["offsets moved by the frame"] += finding.rows
            continue
        paired = len(a_rows) == len(b_rows) and a_rows
        if frame and paired and all(_frame_shift(x, y, delta) or _stack_only(x, y) or shape(x) == shape(y) for x, y in zip(a_rows, b_rows)):
            result.fallout["offsets moved by the frame"] += finding.rows
            continue
        if paired and all(shape(x) == shape(y) for x, y in zip(a_rows, b_rows)):
            register_rows += finding.rows
            continue
        if paired and all(_stack_only(x, y) or shape(x) == shape(y) for x, y in zip(a_rows, b_rows)):
            slot_rows += finding.rows
            sites.append(("slots", finding, a_rows, b_rows))
            continue
        if call_set and any(i.is_call for i in a_rows + b_rows) and not paired:
            result.fallout["one-sided calls"] += finding.rows
            continue
        if not paired and a_rows + b_rows and all(_spill(i) for i in a_rows + b_rows):
            spill_rows += finding.rows  # register pressure: fallout when anything structural differs
            continue
        if paired and all(_constant_only(x, y) or shape(x) == shape(y) for x, y in zip(a_rows, b_rows)):
            sites.append(("constant", finding, a_rows, b_rows))
        elif any(i.mnemonic.startswith("j") or i.mnemonic in CONTROL for i in a_rows + b_rows):
            sites.append(("control-flow", finding, a_rows, b_rows))
        else:
            sites.append(("instruction", finding, a_rows, b_rows))

    if slot_rows:
        first = next(f for kind, f, _, _ in sites if kind == "slots")
        result.primaries.append(Primary(
            "slots", f"{slot_rows} stack accesses use other slots with the frame the same size: locals declared in "
            "another order or scope", first.offset, first.candidate, slot_rows))
    merged: list[Primary] = []
    last_candidate = None  # a row only retail has is placed at the nearest earlier row of ours
    for kind, finding, a_rows, b_rows in sorted((s for s in sites if s[0] != "slots"), key=lambda s: s[1].offset):
        if finding.candidate is None:
            finding = DiffFinding(finding.cause, finding.offset, finding.detail, last_candidate, finding.rows,
                                  finding.target_offsets, finding.candidate_offsets)
        last_candidate = finding.candidate
        last = merged[-1] if merged else None
        if last and last.kind == kind and finding.offset - (last.target_offset or 0) < GROUP:
            last.rows += finding.rows
            continue
        retail = "; ".join(i.text for i in a_rows) or "nothing"
        own = "; ".join(i.text for i in b_rows) or "nothing"
        merged.append(Primary(kind, f"retail has `{retail}` where ours has `{own}`", finding.offset, finding.candidate, finding.rows))
    result.primaries.extend(merged)

    result.primaries.sort(key=lambda p: ORDER.index(p.kind))
    if result.primaries:
        result.fallout["register choices"] += register_rows
        result.fallout["spills, reloads and saves"] += spill_rows
    elif register_rows or spill_rows:
        result.primaries.append(Primary(
            "register-allocation", f"{register_rows + spill_rows} differing rows are register choices, spills and "
            "saves alone: the structure matches, so the cause is allocation order (for MSVC, value lifetimes and "
            "uses; see il-search)", rows=register_rows + spill_rows))
    return result


def place(analysis: Analysis, records: list[tuple[int, int]]) -> None:
    """Give each primary cause the source line of its candidate offset, from line records."""
    from concord.first_differences import at

    for primary in analysis.primaries:
        if primary.candidate_offset is not None:
            primary.line = at(records, primary.candidate_offset)


def census(rows: list[dict]) -> dict:
    """Primary causes over many functions: by kind, by count per function, and the fallout share."""
    kinds: dict[str, list[int]] = {}
    counts = Counter()
    register_only = [0, 0]
    differences = fallout = 0
    for row in rows:
        seen = {p["kind"] for p in row["primaries"]}
        for kind in seen:
            entry = kinds.setdefault(kind, [0, 0])
            entry[0] += 1
            entry[1] += row["bytes"]
        counts[min(len(row["primaries"]), 10)] += 1
        if seen == {"register-allocation"}:
            register_only[0] += 1
            register_only[1] += row["bytes"]
        differences += row["differences"]
        fallout += sum(row["fallout"].values())
    return {
        "functions": len(rows),
        "by_kind": {k: {"functions": v[0], "bytes": v[1]} for k, v in sorted(kinds.items(), key=lambda kv: -kv[1][1])},
        "primaries_per_function": {("10+" if k == 10 else str(k)): n for k, n in sorted(counts.items())},
        "register_allocation_only": {"functions": register_only[0], "bytes": register_only[1]},
        "differences": differences,
        "fallout": fallout,
    }


def function(target, verdict, records: list[tuple[int, int]] | None = None) -> dict:
    """One open function's primary causes and fallout, as a JSON-ready row."""
    from concord.diff import read_functions

    retail, ours = target.object_pair(verdict)
    pair = read_functions(retail, {verdict.symbol}).get(verdict.symbol), read_functions(ours, {verdict.symbol}).get(verdict.symbol)
    if pair[0] is None or pair[1] is None:
        raise KeyError(f"{verdict.symbol} is not a function in both objects of {verdict.unit}")
    findings = target.diff(verdict).findings
    analysis = analyse(pair[0], pair[1], findings)
    if records:
        place(analysis, records)
    return {
        "unit": verdict.unit,
        "symbol": verdict.symbol,
        "bytes": verdict.row.get("size", 0),
        "score": verdict.row.get("fuzzy"),
        "differences": analysis.differences,
        "primaries": [vars(p) for p in analysis.primaries],
        "fallout": dict(analysis.fallout),
    }


def run(target, units: list[str] | None = None, progress=None) -> dict:
    """Every function below 100% in the target's last build, or in the given units, with a census."""
    from collections import defaultdict
    from dataclasses import replace

    from concord import coff

    def below(verdicts):
        """Functions under 100% in the build, by their score where the target reports one (a dip the
        project counts exact included), else by the exact flag."""
        for verdict in verdicts:
            fuzzy = verdict.row.get("fuzzy")
            if fuzzy is None and not verdict.row.get("exact"):
                yield verdict
            elif fuzzy is not None and fuzzy < 100.0:
                yield replace(verdict, row={**verdict.row, "exact": False})

    open_ = defaultdict(list)
    for verdict in below(target.verdicts()):
        if units is None or verdict.unit in units:
            open_[verdict.unit].append(verdict)
    rows, failed = [], []
    for unit, verdicts in sorted(open_.items()):
        data = None
        if hasattr(target, "line_object"):  # line records place each cause at a source line where the target has them
            try:
                data = target.line_object(unit, target.source_path(unit).read_bytes())
            except (OSError, KeyError):
                data = None
        for verdict in verdicts:
            try:
                rows.append(function(target, verdict, coff.lines(data, verdict.symbol) if data else None))
            except (KeyError, ValueError, OSError) as error:
                failed.append(f"{unit} {verdict.symbol}: {error}")
        if progress:
            progress(unit, len(rows))
    return {"census": census(rows), "functions": rows, "failed": failed}
