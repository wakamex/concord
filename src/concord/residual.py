"""Classify the remaining differences across an inexact Harvest build."""

from __future__ import annotations

import json
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
# A size difference alone is not one: register choice and instruction selection change
# a function's size too, so it leaves a function unclassified unless a row or signal
# points at the source.
SOURCE_KINDS = {"data", "calls", "enum-check"}


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


# Callees that only exception cleanup and destruction reach. A difference made up of
# them alone is where GCC inlined a destructor, as in the GUI element constructors,
# whose target cleanup pads repeat IEventReceiver's destructor, delete and delete[].
CLEANUP_CALLEES = {"_Unwind_Resume", "_ZdlPv", "_ZdaPv"}


def _cleanup(callee: dict) -> bool:
    symbol = callee.get("symbol") or ""
    return symbol in CLEANUP_CALLEES or symbol.endswith(("D0Ev", "D1Ev", "D2Ev"))


def _cleanup_only(difference: dict) -> bool:
    callees = []
    for side in ("ours", "target"):
        value = difference.get(side)
        callees.extend(value if isinstance(value, list) else [value] if value else [])
    return bool(callees) and all(_cleanup(callee) for callee in callees)


def _pattern_counts(differences: list[dict]) -> tuple[Counter, bool]:
    counts: Counter = Counter()
    source_pointing = False
    for difference in differences:
        kind = difference["kind"]
        if kind == "call" and _cleanup_only(difference):
            counts["inline-distribution"] += 1
        elif kind == "call":
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


PROGRESS_CAUSES = {
    "not-compiled": {
        "why": "The target symbol is absent from every compiled base object.",
        "recommendation": "Restore or register the source definition first, then inspect its compiler context and callers.",
    },
    "compiled-but-unplaced": {
        "why": "A base object defines the target symbol in a section that the match report could not place.",
        "recommendation": "Place the owning section from its real target symbol or explicit unit mapping, then compare its body.",
    },
    "stubbed": {
        "why": "The base object emits the target symbol as a trap-only stub.",
        "recommendation": "Recover the function body before tuning callers or shared compiler state.",
    },
    "placed-in-unit-residual-skips": {
        "why": "The base object emits the target symbol in a placed section, but no residual function row represents its target address.",
        "recommendation": "Repair the unit's function extent or symbol mapping so residual can compare the emitted body.",
    },
    "matched-under-another-name": {
        "why": "A placed candidate function reaches the target address under a different symbol.",
        "recommendation": "Correct the symbol or alias mapping before changing source bodies.",
    },
    "unmapped-target-extent": {
        "why": "The target inventory provides only an address-derived name, and no placed candidate function reaches that address.",
        "recommendation": "Identify the owning source unit from neighboring symbols and extents, then compile and map it before body-level work.",
    },
    "other": {
        "why": "The available match and object evidence does not identify a more specific cause.",
        "recommendation": "Resolve the missing source or placement evidence before making a body change.",
    },
}


def _address(value) -> int | None:
    if value is None:
        return None
    return int(value, 0) if isinstance(value, str) else int(value)


def _classify_missing(
    function: dict,
    candidate_functions: dict[str, list[dict]],
    placed_by_address: dict[int, list[dict]],
) -> dict:
    symbol = function["symbol"]
    address = _address(function.get("address"))
    placed = placed_by_address.get(address, []) if address is not None else []
    other_names = [row for row in placed if row["symbol"] != symbol]
    if other_names:
        return {
            "cause": "matched-under-another-name",
            "evidence": [f"{row['symbol']} in {row['unit']}" for row in other_names],
            "candidate_units": sorted({row["unit"] for row in other_names}),
        }

    candidates = candidate_functions.get(symbol, [])
    candidate_units = sorted({row["unit"] for row in candidates})
    if any(row.get("stubbed") for row in candidates):
        cause = "stubbed"
    elif any(row.get("unplaced") for row in candidates):
        cause = "compiled-but-unplaced"
    elif candidates:
        cause = "placed-in-unit-residual-skips"
    elif symbol.startswith("sub_"):
        cause = "unmapped-target-extent"
    else:
        cause = "not-compiled"

    evidence = [f"{row['unit']}:{row['section']}" for row in candidates]
    if not evidence:
        if cause == "unmapped-target-extent":
            evidence = ["no candidate function is placed at the address-derived target extent"]
        elif cause == "not-compiled":
            evidence = ["the symbol is absent from every compiled base object"]
    return {"cause": cause, "evidence": evidence, "candidate_units": candidate_units}


def _candidate_function_index(harvest: Harvest, wanted: set[str]) -> dict[str, list[dict]]:
    """Find target symbols in compiled objects, including functions in unplaced sections."""
    if not wanted:
        return {}
    from elftools.elf.elffile import ELFFile

    found: dict[str, list[dict]] = defaultdict(list)
    base_objects = harvest.objects / "base"
    for path in sorted(base_objects.glob("*.o")) if base_objects.is_dir() else []:
        report_path = harvest.reports / f"{path.stem}.json"
        try:
            match_report = json.loads(report_path.read_text())
        except FileNotFoundError:
            match_report = {}
        unplaced = set(match_report.get("unplaced_sections", []))
        unit = match_report.get("unit", path.stem)
        with path.open("rb") as stream:
            elf = ELFFile(stream)
            table = elf.get_section_by_name(".symtab")
            if table is None:
                continue
            sections = list(elf.iter_sections())
            section_data = {}
            for symbol in table.iter_symbols():
                if symbol.name not in wanted or symbol["st_info"]["type"] != "STT_FUNC":
                    continue
                section_index = symbol["st_shndx"]
                if not isinstance(section_index, int) or section_index >= len(sections):
                    continue
                section = sections[section_index]
                if section_index not in section_data:
                    section_data[section_index] = section.data()
                start, size = symbol["st_value"], symbol["st_size"]
                code = section_data[section_index][start : start + size]
                trap_only = code in (b"\x0f\x0b", b"\xcc") or (
                    code.startswith(b"\x0f\x0b") and all(byte in (0x90, 0xCC) for byte in code[2:])
                )
                found[symbol.name].append(
                    {
                        "unit": unit,
                        "section": section.name,
                        "size": size,
                        "unplaced": section.name in unplaced,
                        "stubbed": trap_only,
                    }
                )
    return found


def _progress_inventory(progress_report: dict) -> tuple[dict[int, dict], dict[int, dict], list[dict], int, int]:
    if progress_report.get("version") != 2:
        raise ValueError("unsupported hv.progress report version")
    inexact = {}
    exact = {}
    unclaimed = []
    function_bytes = matched_function_bytes = 0
    for unit in progress_report["units"]:
        metadata = unit.get("metadata", {})
        measures = unit["measures"]
        categories = set(metadata.get("progress_categories", []))
        if "functions" in categories:
            matched = measures["matched_functions"] == 1
            for function in unit.get("functions", []):
                address = _address(function["metadata"]["virtual_address"])
                size = int(function["size"])
                if address is None or address in inexact or address in exact:
                    raise ValueError("duplicate or missing target function address in hv.progress report")
                function_bytes += size
                row = {
                    "address": hex(address),
                    "symbol": function["name"],
                    "size": size,
                    "fuzzy_match_percent": function.get("fuzzy_match_percent"),
                    "source_path": metadata.get("source_path"),
                }
                if matched:
                    exact[address] = row
                    matched_function_bytes += size
                else:
                    inexact[address] = row
        if "unclaimed" in categories:
            size = int(measures["total_code"])
            if size:
                unclaimed.append({"unit": unit["name"], "bytes": size})

    measures = progress_report["measures"]
    total_code = int(measures["total_code"])
    matched_code = int(measures["matched_code"])
    unclaimed_bytes = sum(row["bytes"] for row in unclaimed)
    if (
        len(inexact) + len(exact) != measures["total_functions"]
        or len(exact) != measures["matched_functions"]
        or matched_function_bytes != matched_code
        or function_bytes + unclaimed_bytes != total_code
    ):
        raise ValueError("hv.progress function and code totals disagree")
    return inexact, exact, unclaimed, total_code, matched_code


def _reconcile_progress(
    progress_report: dict,
    residual_functions: list[dict],
    candidate_functions: dict[str, list[dict]] | None = None,
    placed_by_address: dict[int, list[dict]] | None = None,
) -> dict:
    inexact, exact, unclaimed, total_code, matched_code = _progress_inventory(progress_report)
    candidate_functions = candidate_functions or {}
    placed_by_address = placed_by_address or {}

    residual_by_address = {}
    unaddressed_residual = []
    for function in residual_functions:
        address = _address(function.get("address"))
        if address is None:
            unaddressed_residual.append(function)
        else:
            residual_by_address[address] = function

    classified = {address: residual_by_address[address] for address in inexact.keys() & residual_by_address.keys()}
    exact_overlaps = {address: residual_by_address[address] for address in exact.keys() & residual_by_address.keys()}
    missing_rows = []
    for address in sorted(inexact.keys() - residual_by_address.keys()):
        row = dict(inexact[address])
        classification = _classify_missing(row, candidate_functions, placed_by_address)
        row.update(classification)
        missing_rows.append(row)

    groups = {
        cause: {**details, "functions": [], "bytes": 0}
        for cause, details in PROGRESS_CAUSES.items()
    }
    for row in missing_rows:
        group = groups[row["cause"]]
        group["functions"].append(row)
        group["bytes"] += row["size"]
    groups["unclaimed-code"] = {
        "why": "Executable bytes remain outside the captured FDE and thunk function extents, including linkage stubs and init/fini sections.",
        "recommendation": "Inspect each section as padding, linkage stubs, init/fini code, or missing function extents before attributing bytes to source functions.",
        "functions": [],
        "bytes": sum(row["bytes"] for row in unclaimed),
        "sections": unclaimed,
    }

    residual_bytes = sum(row["size"] for row in residual_functions)
    classified_bytes = sum(inexact[address]["size"] for address in classified)
    exact_overlap_bytes = sum(exact[address]["size"] for address in exact_overlaps)
    missing_bytes = sum(row["size"] for row in missing_rows)
    untracked = [
        row
        for address, row in residual_by_address.items()
        if address not in inexact and address not in exact
    ] + unaddressed_residual
    untracked_bytes = sum(row["size"] for row in untracked)
    inexact_function_bytes = sum(row["size"] for row in inexact.values())
    unclaimed_bytes = sum(row["bytes"] for row in unclaimed)
    inexact_code_bytes = total_code - matched_code
    reconciled_code_bytes = classified_bytes + missing_bytes + unclaimed_bytes
    summary = {
        "progress_functions": len(inexact) + len(exact),
        "progress_matched_functions": len(exact),
        "progress_inexact_functions": len(inexact),
        "progress_inexact_function_bytes": inexact_function_bytes,
        "residual_raw_functions": len(residual_functions),
        "residual_raw_bytes": residual_bytes,
        "residual_raw_unit_occurrences": sum(len(row.get("unit_occurrences", [])) for row in residual_functions),
        "residual_progress_inexact_functions": len(classified),
        "residual_progress_inexact_bytes": classified_bytes,
        "residual_progress_inexact_unit_occurrences": sum(
            len(residual_by_address[address].get("unit_occurrences", [])) for address in classified
        ),
        "residual_progress_exact_functions": len(exact_overlaps),
        "residual_progress_exact_bytes": exact_overlap_bytes,
        "missing_inexact_functions": len(missing_rows),
        "missing_inexact_function_bytes": missing_bytes,
        "unclaimed_code_bytes": unclaimed_bytes,
        "inexact_code_bytes": inexact_code_bytes,
        "reconciled_code_bytes": reconciled_code_bytes,
        "untracked_residual_functions": len(untracked),
        "untracked_residual_bytes": untracked_bytes,
    }
    if reconciled_code_bytes != inexact_code_bytes:
        raise ValueError("residual and hv.progress inexact code totals do not reconcile")
    return {
        "summary": summary,
        "groups": groups,
        "missing_functions": missing_rows,
        "exact_overlaps": [
            {
                "address": hex(address),
                "symbol": exact[address]["symbol"],
                "size": exact[address]["size"],
                "progress_source_path": exact[address]["source_path"],
                "residual_units": residual_by_address[address].get("units", []),
                "residual_kinds": residual_by_address[address].get("kinds", []),
            }
            for address in sorted(exact_overlaps)
        ],
        "unclaimed_code": unclaimed,
        "untracked_residual": untracked,
    }


def _placed_function_index(verdicts: list[FunctionVerdict], harvest: Harvest) -> dict[int, list[dict]]:
    placed: dict[int, list[dict]] = defaultdict(list)
    for verdict in verdicts:
        address = _address(verdict.row.get("address"))
        if address is not None:
            placed[address].append(
                {"unit": harvest.source(verdict.unit), "symbol": verdict.symbol, "exact": verdict.row["exact"]}
            )
    return placed


def _progress_reconciliation(harvest: Harvest, verdicts: list[FunctionVerdict], functions: list[dict]) -> dict | None:
    path = harvest.root / "build" / "progress" / "report.json"
    if not path.is_file():
        return None
    progress_report = json.loads(path.read_text())
    inexact, _, _, _, _ = _progress_inventory(progress_report)
    candidate_functions = _candidate_function_index(harvest, {row["symbol"] for row in inexact.values()})
    return _reconcile_progress(
        progress_report,
        functions,
        candidate_functions,
        _placed_function_index(verdicts, harvest),
    )


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
    all_verdicts = list(harvest.verdicts())
    verdicts = [verdict for verdict in all_verdicts if not verdict.row["exact"]]
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

    progress_reconciliation = _progress_reconciliation(harvest, all_verdicts, functions)
    if progress_reconciliation is not None:
        exact_addresses = {row["address"] for row in progress_reconciliation["exact_overlaps"]}
        functions = [row for row in functions if row["address"] not in exact_addresses]
        for row in progress_reconciliation["missing_functions"]:
            functions.append(
                {
                    "address": row["address"],
                    "symbol": row["symbol"],
                    "size": row["size"],
                    "score": None,
                    "candidate_sizes": [],
                    "unit_occurrences": [],
                    "kinds": [],
                    "source_signals": [],
                    "units": [],
                    "unit_count": 0,
                    "group": "progress-missing",
                    "residual_cause": row["cause"],
                    "residual_evidence": row["evidence"],
                    "recommendation": PROGRESS_CAUSES[row["cause"]]["recommendation"],
                    "progress_fuzzy_match_percent": row["fuzzy_match_percent"],
                    "source_path": row["source_path"],
                }
            )
        functions.sort(key=lambda row: (-row["size"], row["address"] or "", row["symbol"]))

    group_summary = {}
    for name in ("no-source-change", "source-pointing", "other"):
        selected = [row for row in functions if row["group"] == name]
        group_summary[name] = {"functions": len(selected), "bytes": sum(row["size"] for row in selected)}
    if progress_reconciliation is not None:
        selected = [row for row in functions if row["group"] == "progress-missing"]
        group_summary["progress-missing"] = {
            "functions": len(selected),
            "bytes": sum(row["size"] for row in selected),
        }

    kind_summary = {}
    for kind in sorted({item["kind"] for row in functions for item in row["kinds"]}):
        selected = [row for row in functions if kind in {item["kind"] for item in row["kinds"]}]
        kind_summary[kind] = {
            "functions": len(selected),
            "bytes": sum(row["size"] for row in selected),
            "rows": sum(next(item["rows"] for item in row["kinds"] if item["kind"] == kind) for row in selected),
        }

    source_pointing = [row for row in functions if row["group"] == "source-pointing"]
    summary = {
        "functions": len(functions),
        "unit_occurrences": sum(len(row["unit_occurrences"]) for row in functions),
        "bytes": sum(row["size"] for row in functions),
        "groups": group_summary,
        "kinds": kind_summary,
    }
    if progress_reconciliation is not None:
        progress = progress_reconciliation["summary"]
        summary.update(
            {
                "inexact_code_bytes": progress["inexact_code_bytes"],
                "unclaimed_code_bytes": progress["unclaimed_code_bytes"],
                "residual_analyzed_functions": progress["residual_progress_inexact_functions"],
                "residual_analyzed_bytes": progress["residual_progress_inexact_bytes"],
                "residual_raw_functions": progress["residual_raw_functions"],
                "residual_raw_bytes": progress["residual_raw_bytes"],
            }
        )
    return {
        "schema": "concord.residual.v2",
        "score_kind": "Concord instruction alignment",
        "summary": summary,
        "functions": functions,
        "top_source_pointing": source_pointing[:20],
        "progress_reconciliation": progress_reconciliation,
    }
