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
ORDER = ["frame", "calls", "control-flow", "constant", "field", "operation", "slots", "instruction", "register-allocation"]


@dataclass
class Primary:
    kind: str  # frame, calls, slots, control-flow, constant, instruction, register-allocation
    detail: str
    target_offset: int | None = None
    candidate_offset: int | None = None
    rows: int = 1
    line: int | None = None
    file: str | None = None  # the line's file, relative to the checkout


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


def _callee(ins: Instruction, function: ObjectFunction) -> str | None:
    """The function a call instruction calls by relocation. A relocation against the function's own name or a
    section symbol is how some objects reach code in the same section, not a callee."""
    if not ins.is_call:
        return None
    for offset, name in function.targets:
        if ins.offset <= offset < ins.offset + ins.size:
            callee = name.split("+")[0]
            return None if callee == function.name or callee.startswith((".", "$")) else callee
    return None


def calls(instructions: list[Instruction], function: ObjectFunction) -> Counter:
    """Each callee by name, as many times as the function calls it."""
    return Counter(callee for ins in instructions if (callee := _callee(ins, function)) is not None)


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


def _move(ins: Instruction) -> bool:
    """A copy between registers, or between a register and a stack slot: allocation, not computation."""
    if ins.mnemonic in ("push", "pop") and ins.operands and ins.operands[0][0] == "reg":
        return True
    if ins.mnemonic != "mov" or len(ins.operands) != 2:
        return False
    kinds = [o[0] for o in ins.operands]
    if kinds == ["reg", "reg"]:
        return True
    return sorted(kinds) == ["mem", "reg"] and any(o[0] == "mem" and o[1] in STACK for o in ins.operands)


def _field(a: Instruction, b: Instruction) -> bool:
    """The same instruction reaching another member: a non-stack displacement differs, registers aside."""
    if a.mnemonic != b.mnemonic or len(a.operands) != len(b.operands):
        return False
    differs = False
    for x, y in zip(a.operands, b.operands):
        if x[0] == y[0] == "mem" and x[1] not in STACK and x[4] != y[4] and x[3] == y[3] and x[5] == y[5]:
            differs = True
        elif x[0] != y[0] or (x[0] == "imm" and x != y):
            return False
    return differs


def _site(a_rows: list[Instruction], b_rows: list[Instruction]) -> str:
    """The kind of a differing site that is not a frame, call count, branch, constant or slot difference."""
    rows = a_rows + b_rows
    if rows and all(_move(i) for i in rows):
        return "moves"
    if len(a_rows) == len(b_rows) and a_rows and all(_field(x, y) or shape(x) == shape(y) for x, y in zip(a_rows, b_rows)):
        return "field"
    work = Counter(i.mnemonic for i in a_rows if not _move(i)), Counter(i.mnemonic for i in b_rows if not _move(i))
    if work[0] != work[1]:
        return "operation"
    return "instruction"


def table_start(instructions: list[Instruction], function: ObjectFunction) -> int | None:
    """Where a jump table embedded after the code begins: the first relocation against the function itself that
    lies past its last return, since a switch's table holds addresses in the function."""
    rets = [i.offset for i in instructions if i.mnemonic in ("ret", "retn")]
    if not rets:
        return None
    own = sorted(offset for offset, name in function.targets if name.split("+")[0] == function.name and offset > rets[-1])
    return own[0] if own else None


def _rows(by_offset: dict[int, Instruction], offsets: tuple[int, ...], start: int | None, other: tuple[int, ...]) -> list[Instruction]:
    """A finding's instructions on one side: its listed offsets, none when only the other side lists any (a
    one-sided finding), else the instruction at its start."""
    if offsets:
        return [by_offset[o] for o in offsets if o in by_offset]
    if other:
        return []
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
    rows = [(f, _rows(at_target, f.target_offsets, f.offset, f.candidate_offsets),
             _rows(at_candidate, f.candidate_offsets, f.candidate, f.target_offsets)) for f in findings]
    anchors, last = {}, None  # a finding only retail has is placed at our nearest earlier differing row
    for f in sorted(findings, key=lambda f: f.offset):
        last = f.candidate if f.candidate is not None else last
        anchors[id(f)] = last
    for primary in result.primaries:
        if primary.kind == "calls":  # placed at the first differing call to the callee on the side with more
            callee = primary.detail.split(" calls to ", 1)[1].split(" than retail", 1)[0]
            more = " more calls " in primary.detail
            at = next((f for f, a_rows, b_rows in rows
                       if any(_callee(i, candidate if more else target) == callee for i in (b_rows if more else a_rows))), None)
            if at is not None:
                primary.target_offset = at.offset
                primary.candidate_offset = anchors[id(at)]
                primary.rows = 0

    tables = table_start(theirs, target), table_start(ours, candidate)
    register_rows, slot_rows, spill_rows, sites = 0, 0, 0, []
    first_register = None  # the first finding of register choices or copies, where allocation-only differences start
    for finding, a_rows, b_rows in rows:
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
            first_register = first_register or finding
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
            first_register = first_register or finding
            continue
        if paired and all(_constant_only(x, y) or shape(x) == shape(y) for x, y in zip(a_rows, b_rows)):
            sites.append(("constant", finding, a_rows, b_rows))
        elif any(i.mnemonic.startswith("j") or i.mnemonic in CONTROL for i in a_rows + b_rows):
            sites.append(("control-flow", finding, a_rows, b_rows))
        else:
            kind = _site(a_rows, b_rows)
            if kind == "moves":
                spill_rows += finding.rows  # copies and reloads: allocation, fallout when anything structural differs
                first_register = first_register or finding
            else:
                sites.append((kind, finding, a_rows, b_rows))

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
        result.fallout["copies, spills, reloads and saves"] += spill_rows
    elif register_rows or spill_rows:
        at = first_register
        result.primaries.append(Primary(
            "register-allocation", f"{register_rows + spill_rows} differing rows are register choices, spills and "
            "saves alone: the structure matches, so the cause is allocation order (for MSVC, value lifetimes and "
            "uses; see il-search), starting at the first of them; the edit is often some lines away from there",
            at.offset if at else None, anchors[id(at)] if at else None,
            register_rows + spill_rows))
    return result


def place(analysis: Analysis, places: dict[int, list[tuple[str, int]]], own: str, span: tuple[int, int] | None) -> None:
    """Give each primary cause the first line of its candidate offset in the unit's own file, inside the
    function's span; or, for a function defined outside the unit's source (a header's inline method, a library
    template), the line of its own body that the code comes from."""
    for primary in analysis.primaries:
        found = places.get(primary.candidate_offset, []) if primary.candidate_offset is not None else []
        mine = [line for path, line in found if path == own and span and span[0] <= line <= span[1]]
        if mine:
            primary.file, primary.line = own, min(mine)
        elif span is None and found and found[-1][0] != own:  # cl's line records name no file: no header lines on MSVC
            primary.file, primary.line = found[-1]
        else:
            primary.file, primary.line = None, None


def sites(primaries: list[dict], within: int = 2) -> list[list[int]]:
    """The primary causes grouped into source sites: causes whose lines are within `within` lines of the
    previous one share a site, and causes without a line form one more site, listed last. One source change
    usually explains one site, so a function's sites estimate the edits it needs. Sites with more causes come
    first: on 80 fixes from Harvest's history the site with the most causes held the fix in 63, the first
    cause in concord's kind order in 39. Indexes into `primaries`."""
    placed = sorted((p.get("file") or "", p["line"], i) for i, p in enumerate(primaries) if p.get("line") is not None)
    groups: list[list[int]] = []
    last = None
    for path, line, index in placed:
        if groups and last[0] == path and line - last[1] <= within:
            groups[-1].append(index)
        else:
            groups.append([index])
        last = (path, line)
    groups.sort(key=lambda group: -len(group))  # stable: equal sites stay in line order
    unplaced = [i for i, p in enumerate(primaries) if p.get("line") is None]
    return groups + ([unplaced] if unplaced else [])


def census(rows: list[dict]) -> dict:
    """Primary causes over many functions: by kind, by count per function, and the fallout share."""
    kinds: dict[str, list[int]] = {}
    counts, site_counts = Counter(), Counter()
    unplaced = [0, 0]
    header_symbols: dict[str, list[int]] = {}  # symbol -> [open copies, bytes]
    library_symbols: dict[str, list[int]] = {}
    register_only = [0, 0]
    differences = fallout = 0
    for row in rows:
        seen = {p["kind"] for p in row["primaries"]}
        for kind in seen:
            entry = kinds.setdefault(kind, [0, 0])
            entry[0] += 1
            entry[1] += row["bytes"]
        counts[min(len(row["primaries"]), 10)] += 1
        if row.get("header"):
            files = {p.get("file") for p in row["primaries"] if p.get("file")}
            library = bool(files) and all(f.startswith("/") for f in files)  # outside the checkout: a toolchain header
            counts_into = library_symbols if library else header_symbols
            counts_into.setdefault(row["symbol"], [0, row["bytes"]])[0] += 1
        if row["primaries"] and all(p.get("line") is None for p in row["primaries"]):
            unplaced[0] += 1  # no line in the unit's source: code from a header or a library template
            unplaced[1] += row["bytes"]
        else:
            site_counts[min(len(row["sites"]), 10)] += 1
        if seen == {"register-allocation"}:
            register_only[0] += 1
            register_only[1] += row["bytes"]
        differences += row["differences"]
        fallout += sum(row["fallout"].values())
    return {
        "functions": len(rows),
        "by_kind": {k: {"functions": v[0], "bytes": v[1]} for k, v in sorted(kinds.items(), key=lambda kv: -kv[1][1])},
        "primaries_per_function": {("10+" if k == 10 else str(k)): n for k, n in sorted(counts.items())},
        "sites_per_function": {("10+" if k == 10 else str(k)): n for k, n in sorted(site_counts.items())},
        "without_source_lines": {"functions": unplaced[0], "bytes": unplaced[1]},
        "in_project_headers": {"copies": sum(v[0] for v in header_symbols.values()), "functions": len(header_symbols),
                               "bytes": sum(v[1] for v in header_symbols.values())},
        "in_library_headers": {"copies": sum(v[0] for v in library_symbols.values()), "functions": len(library_symbols),
                               "bytes": sum(v[1] for v in library_symbols.values())},
        "register_allocation_only": {"functions": register_only[0], "bytes": register_only[1]},
        "differences": differences,
        "fallout": fallout,
    }


def analysis_of(target, verdict) -> Analysis:
    """One open function's primary causes and fallout, from the build's objects."""
    from concord.diff import read_functions

    retail, ours = target.object_pair(verdict)
    pair = read_functions(retail, {verdict.symbol}).get(verdict.symbol), read_functions(ours, {verdict.symbol}).get(verdict.symbol)
    if pair[0] is None or pair[1] is None:
        raise KeyError(f"{verdict.symbol} is not a function in both objects of {verdict.unit}")
    return analyse(pair[0], pair[1], target.diff(verdict).findings)


def row(verdict, analysis: Analysis, header: bool = False) -> dict:
    """A function's analysis as a JSON-ready row; `header` when the function is defined outside the unit's
    source, so the same function may be open in several units."""
    primaries = [vars(p) for p in analysis.primaries]
    return {
        "header": header,
        "unit": verdict.unit,
        "symbol": verdict.symbol,
        "bytes": verdict.row.get("size", 0),
        "score": verdict.row.get("fuzzy"),
        "differences": analysis.differences,
        "primaries": primaries,
        "sites": sites(primaries),
        "fallout": dict(analysis.fallout),
    }


def run(target, units: list[str] | None = None, progress=None) -> dict:
    """Every function below 100% in the target's last build, or in the given units, with a census."""
    from collections import defaultdict
    from dataclasses import replace

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
        analysed = []
        for verdict in verdicts:
            try:
                analysed.append((verdict, analysis_of(target, verdict)))
            except (KeyError, ValueError, OSError) as error:
                failed.append(f"{unit} {verdict.symbol}: {error}")
        try:  # one line compile per unit places every cause it can
            source = target.source_path(unit).read_bytes()
            wanted = {v.symbol: sorted({p.candidate_offset for p in a.primaries if p.candidate_offset is not None}) for v, a in analysed}
            lines = target.line_map(unit, source, wanted) if any(wanted.values()) else {}
        except (OSError, KeyError, RuntimeError) as error:
            failed.append(f"{unit}: no source lines: {error}")
            source, lines = None, {}
        own = target.unit_file(unit)
        for verdict, analysis in analysed:
            span = None
            if source is not None:
                try:
                    scope = target.find_function(source, verdict.symbol)
                    span = (scope.start_point[0] + 1, scope.end_point[0] + 1)
                except (KeyError, ValueError):
                    span = None
            place(analysis, lines.get(verdict.symbol, {}), own, span)
            rows.append(row(verdict, analysis, header=source is not None and span is None))
        if progress:
            progress(unit, len(rows))
    return {"census": census(rows), "functions": rows, "failed": failed}
