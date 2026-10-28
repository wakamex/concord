"""Infer static object placement from aligned references and target data."""

from __future__ import annotations

import csv
import difflib
import json
import struct
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from concord.calls_constants import (
    _pooled_string,
    _string_section_encoding,
    read_refs,
    read_string_sections,
)
from concord.diff import disassemble, read_function, shape
from concord.harvest import Evaluation, Harvest

DATA_SECTIONS = {".bss", ".data", ".rodata"}


def _static_objects(path: Path) -> list[dict]:
    objects = []
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        symtab = elf.get_section_by_name(".symtab")
        for symbol in symtab.iter_symbols():
            if symbol["st_info"]["type"] != "STT_OBJECT" or not symbol.name or not symbol["st_size"]:
                continue
            index = symbol["st_shndx"]
            if not isinstance(index, int):
                continue
            section = elf.get_section(index).name
            if section in DATA_SECTIONS:
                objects.append({
                    "symbol": symbol.name,
                    "section": section,
                    "offset": symbol["st_value"],
                    "size": symbol["st_size"],
                })
    return objects


def _relocations(path: Path) -> dict[str, dict[int, tuple[str, int]]]:
    """Relocation target identities by data section and offset."""
    found: dict[str, dict[int, tuple[str, int]]] = defaultdict(dict)
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        sections = list(elf.iter_sections())
        for relocs in sections:
            if not isinstance(relocs, RelocationSection):
                continue
            target = sections[relocs["sh_info"]]
            if target.name not in DATA_SECTIONS:
                continue
            symbols = sections[relocs["sh_link"]]
            for relocation in relocs.iter_relocations():
                symbol = symbols.get_symbol(relocation["r_info_sym"])
                index = symbol["st_shndx"]
                base = sections[index].name if isinstance(index, int) else symbol.name
                dest = symbol["st_value"] + relocation["r_addend"]
                found[target.name][relocation["r_offset"]] = (base, dest)
    return found


def _candidate_pointer_table(
    relocations: dict[str, dict[int, tuple[str, int]]],
    strings: dict[str, bytes],
    section: str,
    start: int,
    size: int,
) -> list[tuple[str, str]] | None:
    """String identities in a candidate array of relocated string pointers."""
    entries = relocations.get(section, {})
    if size % 8:
        return None
    values = []
    for offset in range(start, start + size, 8):
        item = entries.get(offset)
        if item is None:
            return None
        base, dest = item
        encoding = _string_section_encoding(base)
        data = strings.get(base) if encoding else None
        value = _pooled_string(data, dest, encoding) if data is not None and encoding else None
        if value is None:
            return None
        values.append(("string", value.hex()))
    return values


def _target_pointer_table(
    target_data: bytes,
    target_relocations: dict[int, tuple[str, int]],
    target_strings: dict[str, bytes],
    original_sections: list[tuple[int, int, str, bytes]],
    start: int,
    encoding: str,
) -> list[tuple[str, str]]:
    """String pointer entries in target bytes, including pointers left unrelocated."""
    values = []
    for offset in range(start, min(len(target_data) - 7, start + 4096), 8):
        relocation = target_relocations.get(offset)
        if relocation is not None:
            base, dest = relocation
            target_encoding = _string_section_encoding(base)
            data = target_strings.get(base) if target_encoding else None
            value = _pooled_string(data, dest, target_encoding) if data is not None and target_encoding else None
        else:
            address = struct.unpack_from("<Q", target_data, offset)[0]
            if not address:
                break
            value = None
            for section_start, section_end, _, data in original_sections:
                if section_start <= address < section_end:
                    value = _pooled_string(data, address - section_start, encoding)
                    break
        if value is None:
            break
        values.append(("string", value.hex()))
    return values


def _original_sections(path: Path) -> list[tuple[int, int, str, bytes]]:
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        return [
            (section["sh_addr"], section["sh_addr"] + section["sh_size"], section.name, section.data())
            for section in elf.iter_sections()
            if section["sh_type"] == "SHT_PROGBITS" and section["sh_flags"] & 2
        ]


def _section_data(path: Path) -> dict[str, bytes]:
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        return {
            section.name: section.data()
            for section in elf.iter_sections()
            if section.name in DATA_SECTIONS and section["sh_type"] != "SHT_NOBITS"
        }


def _aligned_data_refs(
    ours_path: Path,
    target_path: Path,
    exact: dict[str, bool],
) -> list[dict]:
    ours_refs, target_refs = read_refs(ours_path), read_refs(target_path)
    findings = []
    for symbol in ours_refs.keys() & target_refs.keys():
        ours = ours_refs[symbol]
        target = target_refs[symbol]
        ours_code = read_function(ours_path, symbol)
        target_code = read_function(target_path, symbol)
        ours_ins = [i for i in disassemble(ours_code) if i.mnemonic != "nop"]
        target_ins = [i for i in disassemble(target_code) if i.mnemonic != "nop"]
        matcher = difflib.SequenceMatcher(
            None, [shape(i) for i in ours_ins], [shape(i) for i in target_ins], autojunk=False
        )
        ours_at: dict[int, list] = defaultdict(list)
        target_at: dict[int, list] = defaultdict(list)
        for ref in ours.datarefs:
            ours_at[ref.offset].append(ref)
        for ref in target.datarefs:
            target_at[ref.offset].append(ref)
        for tag, oi, oj, ti, tj in matcher.get_opcodes():
            if tag != "equal":
                continue
            for ours_inst, target_inst in zip(ours_ins[oi:oj], target_ins[ti:tj]):
                left, right = ours_at[ours_inst.offset], target_at[target_inst.offset]
                if len(left) != len(right):
                    continue
                for source, dest in zip(left, right):
                    if source.base not in DATA_SECTIONS or dest.base not in DATA_SECTIONS:
                        continue
                    findings.append({
                        "function": symbol,
                        "exact": exact.get(symbol, False),
                        "ours": {"section": source.base, "offset": source.dest},
                        "target": {"section": dest.base, "offset": dest.dest},
                        "instruction": source.text,
                    })
    return findings


def _owner(objects: list[dict], section: str, offset: int) -> dict | None:
    matches = [
        item for item in objects
        if item["section"] == section and item["offset"] <= offset < item["offset"] + item["size"]
    ]
    return matches[0] if len(matches) == 1 else None


def _symbol_sizes(root: Path, build: str) -> dict[str, int]:
    path = root / "config" / build / "symbols.tsv"
    if not path.exists():
        return {}
    with path.open(newline="") as stream:
        return {
            row["symbol"]: int(row["size"])
            for row in csv.DictReader(stream, delimiter="\t")
            if int(row["size"])
        }


def analyze_unit(
    harvest: Harvest,
    unit: str,
    *,
    object_path: Path | None = None,
    function_sections: list[dict] | None = None,
) -> dict:
    slug = harvest.slug(unit)
    report_path = harvest.reports / f"{slug}.json"
    if not report_path.exists():
        raise FileNotFoundError(f"no match report for {unit}: {report_path}")
    report = json.loads(report_path.read_text())
    sections = function_sections if function_sections is not None else report["sections"]
    rows = {row["symbol"]: row for section in sections for row in section.get("functions", [])}
    ours_path = object_path or harvest.reports / f"{slug}.o"
    target_path = harvest.objects / "target" / f"{slug}.o"
    if not ours_path.exists() or not target_path.exists():
        raise FileNotFoundError(f"missing compiled object for {unit}")

    objects = _static_objects(ours_path)
    exact = {name: bool(row.get("exact")) for name, row in rows.items()}
    references = _aligned_data_refs(ours_path, target_path, exact)
    votes: dict[str, Counter] = defaultdict(Counter)
    evidence: dict[str, list[dict]] = defaultdict(list)
    for reference in references:
        source, target = reference["ours"], reference["target"]
        item = _owner(objects, source["section"], source["offset"])
        if item is None:
            continue
        relative = source["offset"] - item["offset"]
        start = target["offset"] - relative
        if start < 0:
            continue
        location = (target["section"], start)
        votes[item["symbol"]][location] += 2 if reference["exact"] else 1
        evidence[item["symbol"]].append({
            "function": reference["function"],
            "exact_function": reference["exact"],
            "ours_ref": f"{source['section']}+{source['offset']:#x}",
            "target_ref": f"{target['section']}+{target['offset']:#x}",
            "relative_offset": relative,
            "inferred_start": f"{target['section']}+{start:#x}",
        })

    ours_relocs = _relocations(ours_path)
    target_relocs = _relocations(target_path)
    ours_strings, target_strings = read_string_sections(ours_path), read_string_sections(target_path)
    target_data = _section_data(target_path)
    original_sections = _original_sections(harvest.root / "orig" / harvest.build / "Harvest")
    learned_sizes = _symbol_sizes(harvest.root, harvest.build)
    for item in objects:
        choices = votes[item["symbol"]]
        location = choices.most_common(1)[0][0] if choices else None
        ambiguous = len(choices) > 1 and choices.most_common()[0][1] == choices.most_common()[1][1]
        if ambiguous:
            location = None
        target_section, target_offset = location if location else (None, None)
        target_size, size_basis = None, "unverified"
        content_match = None
        if target_section:
            ours_content = _candidate_pointer_table(
                ours_relocs, ours_strings, item["section"], item["offset"], item["size"]
            )
            first = ours_relocs.get(item["section"], {}).get(item["offset"])
            encoding = _string_section_encoding(first[0]) if first else None
            target_content = (
                _target_pointer_table(
                    target_data.get(target_section, b""),
                    target_relocs.get(target_section, {}),
                    target_strings,
                    original_sections,
                    target_offset,
                    encoding,
                )
                if ours_content is not None and encoding
                else []
            )
            if ours_content is not None:
                content_match = target_content[:len(ours_content)] == ours_content
            if content_match:
                target_size = item["size"]
                size_basis = (
                    "target-content-match" if len(target_content) == len(ours_content)
                    else "target-content-prefix-match"
                )
            elif target_content:
                target_size, size_basis = len(target_content) * 8, "target-pointer-run"
            elif item["symbol"] in learned_sizes:
                target_size, size_basis = learned_sizes[item["symbol"]], "learned-symbol-size"
            else:
                target_size, size_basis = item["size"], "candidate-size"

        outliers = [
            row for row in evidence[item["symbol"]]
            if location is None or row["inferred_start"] != f"{target_section}+{target_offset:#x}"
        ]
        same = location == (item["section"], item["offset"])
        if not choices:
            status = "unmapped"
        elif location is None:
            status = "ambiguous"
        elif target_section != item["section"] or not same:
            status = "layout-differs"
        elif target_size != item["size"]:
            status = "size-differs"
        else:
            status = "same-layout"
        item["target"] = {
            "section": target_section,
            "offset": f"{target_offset:#x}" if target_offset is not None else None,
            "size": target_size,
            "size_basis": size_basis,
            "relocation_content_matches": content_match,
        }
        item["status"] = status
        item["references"] = len(evidence[item["symbol"]])
        item["outliers"] = outliers
        item["evidence"] = evidence[item["symbol"]]
        item["votes"] = [
            {"section": section, "offset": f"{offset:#x}", "weight": weight}
            for (section, offset), weight in choices.most_common()
        ]

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in objects:
        target_section = item["target"]["section"]
        if target_section:
            grouped[target_section].append(item)
    sections = {}
    for section, items in grouped.items():
        source_order = [x["symbol"] for x in sorted(items, key=lambda x: x["offset"])]
        target_order = [x["symbol"] for x in sorted(items, key=lambda x: int(x["target"]["offset"], 16))]
        sections[section] = {
            "ours_order": source_order,
            "target_order": target_order,
            "order_differs": source_order != target_order,
            "proposed_definition_order": target_order,
            "proposed_sizes": [
                {
                    "symbol": item["symbol"],
                    "size": item["target"]["size"],
                    "basis": item["target"]["size_basis"],
                }
                for item in sorted(items, key=lambda x: int(x["target"]["offset"], 16))
            ],
        }
    return {
        "schema": "concord.statics.v1",
        "unit": report["unit"],
        "objects": objects,
        "sections": sections,
        "aligned_data_references": len(references),
    }


def data_layout_improves(harvest: Harvest, unit: str, before: Evaluation, after: Evaluation) -> dict:
    """Whether a source candidate improves static placement without losing a match."""
    if before.error or after.error or before.object is None or after.object is None:
        return {"improves": False, "reason": "candidate object is unavailable"}
    old = analyze_unit(harvest, unit, object_path=before.object, function_sections=before.sections)
    new = analyze_unit(harvest, unit, object_path=after.object, function_sections=after.sections)
    old_objects = {row["symbol"]: row for row in old["objects"]}
    new_objects = {row["symbol"]: row for row in new["objects"]}
    improvements = []
    for symbol in old_objects.keys() | new_objects.keys():
        previous, candidate = old_objects.get(symbol), new_objects.get(symbol)
        if previous is None or candidate is None:
            return {"improves": False, "reason": f"static object set changed: {symbol}"}
        previous_target, candidate_target = previous["target"], candidate["target"]
        previous_same = previous["status"] == "same-layout" and previous_target["relocation_content_matches"] is not False
        candidate_same = candidate["status"] == "same-layout" and candidate_target["relocation_content_matches"] is not False
        if previous_same and not candidate_same:
            return {"improves": False, "reason": f"static object regressed: {symbol}"}
        if previous_target["section"] and not candidate_target["section"]:
            return {"improves": False, "reason": f"static object placement became ambiguous: {symbol}"}
        if not previous_same and candidate_same:
            improvements.append(symbol)
    return {
        "improves": bool(improvements),
        "objects": improvements,
        "reason": "aligned references now place these objects at their target locations" if improvements else "no static object placement improved",
    }


def gather(harvest: Harvest, units: Iterable[str]) -> dict:
    results = [analyze_unit(harvest, unit) for unit in units]
    return {"schema": "concord.statics-report.v1", "units": results}


def show(report: dict) -> list[str]:
    lines = []
    for unit in report["units"]:
        lines.append(unit["unit"])
        for section, proposal in sorted(unit["sections"].items()):
            if proposal["order_differs"] or any(
                row["basis"] == "target-pointer-run" for row in proposal["proposed_sizes"]
            ):
                lines.append(
                    f"  {section}: order differs={proposal['order_differs']}; "
                    f"definition order: {' -> '.join(proposal['proposed_definition_order'])}"
                )
                for row in proposal["proposed_sizes"]:
                    lines.append(f"    {row['symbol']}: size {row['size']} ({row['basis']})")
        for item in unit["objects"]:
            if item["status"] in {"layout-differs", "size-differs", "ambiguous"}:
                target = item["target"]
                lines.append(
                    f"  {item['symbol']}: {item['section']}+{item['offset']:#x}/"
                    f"{item['size']} -> {target['section']}+{target['offset']} / {target['size']} "
                    f"({item['status']}, {item['references']} references, {len(item['outliers'])} outliers)"
                )
    return lines
