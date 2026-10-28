"""Classify the remaining differences across an inexact Harvest build."""

from __future__ import annotations

from collections import Counter, defaultdict

from concord.calls_constants import gather as calls_constants
from concord.diff import (
    ObjectFunction,
    _padding,
    diff_code,
    disassemble,
    read_functions,
)
from concord.enumswitch import (
    enum_members,
    missing_range_check_offsets,
    source_candidates,
)
from concord.harvest import FunctionVerdict, Harvest
from concord.model import Cause, DiffResult
from concord.signedness import _function_pairs as signedness_pairs

NO_SOURCE_KINDS = {
    "comparison-flip",
    "register-choice",
    "instruction-order",
    "instruction-selection",
    "block-order",
    "padding",
    "inline-distribution",
}
SOURCE_KINDS = {"data", "calls", "size", "enum-check"}


def _comparison_flip(finding) -> bool:
    if finding.cause not in {Cause.OPERAND_ORDER, Cause.BLOCK_ORDER}:
        return False
    pair = finding.detail.split("  vs  ", 1)
    if len(pair) != 2:
        return False
    left, right = pair
    left_mnemonic, _, left_operands = left.partition(" ")
    right_mnemonic, _, right_operands = right.partition(" ")
    if left_mnemonic == right_mnemonic == "cmp":
        left_values = [item.strip() for item in left_operands.split(",")]
        right_values = [item.strip() for item in right_operands.split(",")]
        return len(left_values) == len(right_values) == 2 and left_values == right_values[::-1]
    mirrored = {"jl": "jg", "jg": "jl", "jle": "jge", "jge": "jle", "jb": "ja", "ja": "jb", "jbe": "jae", "jae": "jbe"}
    return mirrored.get(left_mnemonic) == right_mnemonic


def _finding_kind(finding) -> str:
    if _comparison_flip(finding):
        return "comparison-flip"
    return {
        Cause.REGISTER_ALLOCATION: "register-choice",
        Cause.INSTRUCTION_ORDER: "instruction-order",
        Cause.BLOCK_ORDER: "block-order",
        Cause.INSTRUCTION_SELECTION: "instruction-selection",
        Cause.INLINING: "calls",
        Cause.IMMEDIATE_OR_RELOC: "data",
        Cause.REFERENCE: "data",
        Cause.STRUCT_LAYOUT: "data",
        Cause.PADDING: "padding",
    }.get(finding.cause, "other")


def _padding_size(function: ObjectFunction) -> int:
    return sum(instruction.size for instruction in disassemble(function) if _padding(instruction))


def _read_build_functions(
    harvest: Harvest, verdicts: list[FunctionVerdict]
) -> dict[tuple[str, str], tuple[ObjectFunction, ObjectFunction]]:
    by_unit: dict[str, set[str]] = defaultdict(set)
    for verdict in verdicts:
        by_unit[verdict.unit].add(verdict.symbol)
    functions = {}
    for unit, symbols in by_unit.items():
        try:
            target = read_functions(harvest.objects / "target" / f"{unit}.o", symbols)
            candidate = read_functions(harvest.objects / "base" / f"{unit}.o", symbols)
        except FileNotFoundError:
            continue
        for symbol in symbols & target.keys() & candidate.keys():
            functions[(unit, symbol)] = (target[symbol], candidate[symbol])
    return functions


def _enum_offsets(
    harvest: Harvest,
    verdicts: list[FunctionVerdict],
    functions: dict[tuple[str, str], tuple[ObjectFunction, ObjectFunction]],
) -> dict[tuple[str, str], set[int]]:
    members = enum_members(harvest.root)
    source_cache: dict[str, bytes] = {}
    offsets = {}
    for verdict in verdicts:
        pair = functions.get((verdict.unit, verdict.symbol))
        if pair is None:
            continue
        target, candidate = pair
        missing = missing_range_check_offsets(target, candidate)
        if not missing:
            continue
        unit = harvest.source(verdict.unit)
        if unit not in source_cache:
            path = harvest.root / "src" / unit
            # A stale match report can name a unit whose source has moved.
            source_cache[unit] = path.read_bytes() if path.is_file() else None
        if source_cache[unit] is not None and source_candidates(source_cache[unit], verdict.symbol, members):
            offsets[(verdict.unit, verdict.symbol)] = set(missing)
    return offsets


def _pattern_signals(
    harvest: Harvest,
    verdicts: list[FunctionVerdict],
    functions: dict[tuple[str, str], tuple[ObjectFunction, ObjectFunction]],
) -> tuple[dict, dict, dict]:
    signals: dict[tuple[str, str], set[str]] = defaultdict(set)
    pattern_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)

    references = calls_constants(harvest, {verdict.unit for verdict in verdicts})
    for function in references["functions"]:
        key = (function["unit"], function["symbol"])
        counts, source_pointing = _pattern_counts(function["differences"])
        pattern_counts[key].update(counts)
        if source_pointing:
            signals[key].add("calls-constants")

    for (unit, symbol), (target, candidate) in functions.items():
        if target.code == candidate.code and target.relocations == candidate.relocations:
            continue
        result = signedness_pairs(target, candidate)
        if result is not None and result[0]:
            signals[(harvest.source(unit), symbol)].add("signedness")

    enum_offsets = _enum_offsets(harvest, verdicts, functions)
    for unit_id, symbol in enum_offsets:
        key = (harvest.source(unit_id), symbol)
        signals[key].add("enum-switch")

    return signals, pattern_counts, enum_offsets


def _pattern_counts(differences: list[dict]) -> tuple[Counter, bool]:
    counts: Counter = Counter()
    source_pointing = False
    for difference in differences:
        kind = difference["kind"]
        if kind == "call":
            if difference["classification"] == "different-callees":
                counts["calls"] += len(difference.get("ours", [])) + len(difference.get("target", []))
                source_pointing = True
            elif difference.get("count_difference"):
                counts["calls"] += 1
                source_pointing = True
            else:
                counts["inline-distribution"] += 1
        elif kind == "indirect":
            counts["calls"] += abs(difference["ours"] - difference["target"])
            source_pointing = True
        else:
            counts["data"] += 1
            source_pointing = True
    return counts, source_pointing


def _kind_rows(
    result: DiffResult,
    enum_offsets: set[int],
    pattern_counts: Counter,
) -> tuple[Counter, dict[str, list[str]]]:
    counts: Counter = Counter()
    evidence: dict[str, list[str]] = defaultdict(list)
    inlining_findings = []
    for finding in result.findings:
        if finding.cause == Cause.INLINING:
            inlining_findings.append(finding)
            continue

        kind = _finding_kind(finding)
        for index in range(finding.rows):
            offset = finding.target_offsets[index] if index < len(finding.target_offsets) else None
            row_kind = "enum-check" if offset in enum_offsets else kind
            counts[row_kind] += 1
            evidence[row_kind].append(finding.detail)

    inlining_rows = sum(finding.rows for finding in inlining_findings)
    if pattern_counts["calls"]:
        call_rows = min(inlining_rows, pattern_counts["calls"])
        block_rows = inlining_rows - call_rows
        if call_rows:
            counts["calls"] += call_rows
            evidence["calls"].append("calls-constants identified the remaining callee multiset difference")
        if block_rows:
            counts["block-order"] += block_rows
            evidence["block-order"].append("unaligned rows beyond the callee multiset difference")
    elif pattern_counts["inline-distribution"]:
        if inlining_rows:
            counts["inline-distribution"] += inlining_rows
            evidence["inline-distribution"].extend(finding.detail for finding in inlining_findings)
    elif inlining_rows:
        counts["block-order"] += inlining_rows
        evidence["block-order"].extend(finding.detail for finding in inlining_findings)
    return counts, evidence


def _group(kinds: set[str], signals: set[str]) -> str:
    if kinds & SOURCE_KINDS or signals:
        return "source-pointing"
    if kinds and kinds <= NO_SOURCE_KINDS:
        return "no-source-change"
    return "other"


def _occurrence(
    harvest: Harvest,
    verdict: FunctionVerdict,
    functions: dict[tuple[str, str], tuple[ObjectFunction, ObjectFunction]],
    signals: set[str],
    pattern_counts: Counter,
    enum_offsets: set[int],
) -> dict:
    source_unit = harvest.source(verdict.unit)
    try:
        target, candidate = functions[(verdict.unit, verdict.symbol)]
        result = (
            diff_code(target, candidate)
            if target.code != candidate.code
            else DiffResult(score=100.0, exact=False, findings=harvest._unproven(verdict, candidate.start))
        )
    except (KeyError, FileNotFoundError) as error:
        return {
            "unit": source_unit,
            "score": None,
            "size": int(verdict.row.get("size", 0)),
            "candidate_size": None,
            "size_delta": None,
            "kinds": [{"kind": "other", "rows": 0, "evidence": [str(error)]}],
            "source_signals": sorted(signals),
        }

    rows, evidence = _kind_rows(result, enum_offsets, pattern_counts)
    target_offsets_in_findings = {
        offset for finding in result.findings for offset in finding.target_offsets
    }
    missing_enum_rows = enum_offsets - target_offsets_in_findings
    if missing_enum_rows:
        rows["enum-check"] += len(missing_enum_rows)
        evidence["enum-check"].append("enum-switch found target-only range-check rows")

    target_size = len(target.code)
    candidate_size = len(candidate.code)
    byte_delta = target_size - candidate_size
    padding_delta = _padding_size(target) - _padding_size(candidate)
    if byte_delta and byte_delta != padding_delta:
        rows["size"] = 0
        evidence["size"].append(f"target {target_size} bytes, candidate {candidate_size} bytes")

    for kind, count in pattern_counts.items():
        if count:
            rows.setdefault(kind, 0)
            evidence[kind].append(f"calls-constants reported {count} differences")
    if "enum-switch" in signals:
        rows.setdefault("enum-check", 0)

    kinds = [
        {"kind": kind, "rows": count, "evidence": list(dict.fromkeys(evidence.get(kind, [])))}
        for kind, count in sorted(rows.items())
    ]
    return {
        "unit": source_unit,
        "score": result.score,
        "size": target_size,
        "candidate_size": candidate_size,
        "size_delta": candidate_size - target_size,
        "kinds": kinds,
        "source_signals": sorted(signals),
    }


def report(harvest: Harvest) -> dict:
    """Classify every inexact emitted function and summarize distinct target extents."""
    verdicts = [verdict for verdict in harvest.verdicts() if not verdict.row["exact"]]
    functions_by_unit = _read_build_functions(harvest, verdicts)
    signals, pattern_counts, enum_offsets = _pattern_signals(harvest, verdicts, functions_by_unit)

    occurrences = []
    for verdict in verdicts:
        unit = harvest.source(verdict.unit)
        key = (unit, verdict.symbol)
        occurrence = _occurrence(
            harvest,
            verdict,
            functions_by_unit,
            signals.get(key, set()),
            pattern_counts.get(key, Counter()),
            enum_offsets.get((verdict.unit, verdict.symbol), set()),
        )
        occurrence.update({"unit_id": verdict.unit, "symbol": verdict.symbol, "address": verdict.row.get("address")})
        occurrences.append(occurrence)

    grouped = {}
    for occurrence in occurrences:
        address = occurrence["address"] or f"{occurrence['unit_id']}:{occurrence['symbol']}"
        row = grouped.setdefault(
            address,
            {
                "address": occurrence["address"],
                "symbol": occurrence["symbol"],
                "size": occurrence["size"],
                "score": occurrence["score"],
                "candidate_sizes": set(),
                "unit_occurrences": [],
                "kinds": {},
                "source_signals": set(),
            },
        )
        if row["size"] != occurrence["size"]:
            raise ValueError(f"target function at {address} has inconsistent sizes")
        if occurrence["score"] is not None:
            row["score"] = min(row["score"], occurrence["score"]) if row["score"] is not None else occurrence["score"]
        if occurrence["candidate_size"] is not None:
            row["candidate_sizes"].add(occurrence["candidate_size"])
        row["unit_occurrences"].append(occurrence)
        row["source_signals"].update(occurrence["source_signals"])
        for item in occurrence["kinds"]:
            current = row["kinds"].setdefault(item["kind"], {"rows": 0, "evidence": []})
            current["rows"] = max(current["rows"], item["rows"])
            current["evidence"].extend(item["evidence"])

    functions = []
    for row in grouped.values():
        row["candidate_sizes"] = sorted(row["candidate_sizes"])
        row["unit_occurrences"].sort(key=lambda item: (item["unit"], item["symbol"]))
        row["units"] = sorted({item["unit"] for item in row["unit_occurrences"]})
        row["unit_count"] = len(row["units"])
        row["source_signals"] = sorted(row["source_signals"])
        row["kinds"] = [
            {"kind": kind, "rows": item["rows"], "evidence": list(dict.fromkeys(item["evidence"]))}
            for kind, item in sorted(row["kinds"].items())
        ]
        row["group"] = _group({item["kind"] for item in row["kinds"]}, set(row["source_signals"]))
        functions.append(row)
    functions.sort(key=lambda row: (-row["size"], row["address"] or "", row["symbol"]))

    group_summary = {}
    for name in ("no-source-change", "source-pointing", "other"):
        selected = [row for row in functions if row["group"] == name]
        group_summary[name] = {"functions": len(selected), "bytes": sum(row["size"] for row in selected)}

    kind_summary = {}
    for kind in sorted({item["kind"] for row in functions for item in row["kinds"]}):
        selected = [row for row in functions if kind in {item["kind"] for item in row["kinds"]}]
        kind_summary[kind] = {
            "functions": len(selected),
            "bytes": sum(row["size"] for row in selected),
            "rows": sum(next(item["rows"] for item in row["kinds"] if item["kind"] == kind) for row in selected),
        }

    source_pointing = [row for row in functions if row["group"] == "source-pointing"]
    return {
        "schema": "concord.residual.v1",
        "score_kind": "Concord instruction alignment",
        "summary": {
            "functions": len(functions),
            "unit_occurrences": len(occurrences),
            "bytes": sum(row["size"] for row in functions),
            "groups": group_summary,
            "kinds": kind_summary,
        },
        "functions": functions,
        "top_source_pointing": source_pointing[:20],
    }
