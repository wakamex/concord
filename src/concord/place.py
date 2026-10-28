"""Refresh references and place called template sections without re-seeding source."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from tree_sitter import Node

from concord.harvest import BUILD, Harvest
from concord.irrlicht import translate
from concord.search import other_scores, worse_elsewhere
from concord.seed import IRRLICHT, _match, _place_bss, _place_called_sections, _register
from concord.transforms import find_function


@dataclass(frozen=True)
class ExtentMatch:
    address: int
    size: int
    fixed_bytes: int
    relocation_bytes: int


def extent_match(obj: Path, target: Path, symbol: str, address: int) -> ExtentMatch | None:
    """Prove that one candidate object function matches a target FDE extent.

    Relocation fields in the relocatable object are masked because the target
    contains their linked values. Every other function byte must agree.
    """
    widths = {0: 0, 1: 8, 2: 4, 4: 4, 9: 4, 10: 4, 11: 4, 41: 4, 42: 4}
    with obj.open("rb") as stream:
        candidate = ELFFile(stream)
        if candidate["e_type"] != "ET_REL" or candidate["e_machine"] != "EM_X86_64":
            raise ValueError(f"{obj}: expected a relocatable x86-64 object")
        found = {}
        for table in candidate.iter_sections():
            if table["sh_type"] not in {"SHT_SYMTAB", "SHT_DYNSYM"}:
                continue
            for entry in table.iter_symbols():
                section_index = entry["st_shndx"]
                if (entry.name == symbol and entry["st_info"]["type"] == "STT_FUNC"
                        and isinstance(section_index, int) and entry["st_size"] > 0):
                    key = (section_index, entry["st_value"], entry["st_size"])
                    found[key] = (section_index, entry["st_value"], entry["st_size"])
        if len(found) != 1:
            return None
        section_index, start, size = next(iter(found.values()))
        section = candidate.get_section(section_index)
        code = section.data()
        end = start + size
        if not section["sh_flags"] & 4 or start < 0 or end > len(code):
            return None
        masked = set()
        for relocation_section in candidate.iter_sections():
            if not isinstance(relocation_section, RelocationSection) or relocation_section["sh_info"] != section_index:
                continue
            for relocation in relocation_section.iter_relocations():
                relocation_start = int(relocation["r_offset"])
                relocation_type = int(relocation["r_info_type"])
                width = widths.get(relocation_type)
                if width is None:
                    if start <= relocation_start < end:
                        raise ValueError(f"{obj}: unsupported x86-64 relocation type {relocation_type} in {symbol}")
                    continue
                relocation_end = relocation_start + width
                if relocation_start < end and relocation_end > start:
                    if relocation_start < start or relocation_end > end:
                        return None
                    masked.update(range(relocation_start - start, relocation_end - start))
        candidate_code = code[start:end]

    with target.open("rb") as stream:
        executable = ELFFile(stream)
        if executable["e_machine"] != "EM_X86_64":
            raise ValueError(f"{target}: expected an x86-64 executable")
        extents = [entry for entry in executable.get_dwarf_info().EH_CFI_entries()
                   if hasattr(entry, "lsda_pointer") and entry["initial_location"] == address]
        if len(extents) != 1 or extents[0]["address_range"] != size:
            return None
        size = int(extents[0]["address_range"])
        sections = [section for section in executable.iter_sections()
                    if section["sh_flags"] & 4 and section["sh_addr"] <= address
                    and address + size <= section["sh_addr"] + section["sh_size"]]
        if len(sections) != 1:
            return None
        section = sections[0]
        offset = address - section["sh_addr"]
        target_code = section.data()[offset:offset + size]
    if len(target_code) != size or len(masked) == size:
        return None
    if any(candidate_code[index] != target_code[index] for index in range(size) if index not in masked):
        return None
    return ExtentMatch(address, size, size - len(masked), len(masked))


def _strings(node: Node) -> list[Node]:
    found, stack = [], [node]
    while stack:
        item = stack.pop()
        if item.type == "string_literal":
            found.append(item)
        else:
            stack.extend(item.named_children)
    return sorted(found, key=lambda n: n.start_byte)


def literal_candidate(current: bytes, ancestor: bytes, symbol: str) -> bytes | None:
    """Restore corresponding ancestor literals in one function. The caller must
    prove the whole repaired function exact before retaining this source."""
    try:
        ours, theirs = find_function(current, symbol), find_function(ancestor, symbol)
    except KeyError:
        return None
    old, new = _strings(ours), _strings(theirs)
    if len(old) != len(new):
        return None
    edits = [(a.start_byte, a.end_byte, b.text) for a, b in zip(old, new, strict=True) if a.text != b.text]
    if not edits:
        return None
    source = current
    for start, end, text in reversed(edits):
        source = source[:start] + text + source[end:]
    return source


def restore_literals(harvest: Harvest, unit: str) -> list[str]:
    """Keep an ancestor-literal repair only when it makes the target function
    exact and does not lower any other compiled function of the unit."""
    ancestor = harvest.root / IRRLICHT / "source" / "Irrlicht" / Path(unit).name
    if not ancestor.exists():
        return []
    path = harvest.root / "src" / unit
    source = path.read_bytes()
    original = translate(ancestor.read_text(encoding="latin-1")).encode()
    candidates = {}
    for verdict in harvest.verdicts(unit):
        if not verdict.row["exact"] and (candidate := literal_candidate(source, original, verdict.symbol)) is not None:
            candidates[verdict.symbol] = candidate
    if not candidates:
        return []
    baseline = harvest.evaluate(unit, {"baseline": source})["baseline"]
    if baseline.error:
        raise RuntimeError(baseline.error)
    # One repair per pass keeps acceptance tied to the exact source compiled.
    for symbol, candidate in candidates.items():
        evaluation = harvest.evaluate(unit, {"literal": candidate})["literal"]
        verdict = evaluation.verdict(symbol)
        others = other_scores(baseline, symbol)
        if evaluation.error or verdict is None or not verdict.row["exact"]:
            continue
        if not baseline.exact_functions() <= evaluation.exact_functions() or worse_elsewhere(others, evaluation, symbol):
            continue
        if path.read_bytes() != source:
            raise RuntimeError(f"{unit}: source changed during literal verification")
        path.write_bytes(candidate)
        _match(harvest.root, unit, learn=True)
        return [symbol]
    return []


def readonly_candidates(obj: Path, target: Path, names: set[str]) -> dict[str, list[int]]:
    """Find unique full-section constant patterns for target-matcher validation.

    Mask supported relocation fields, requiring at least 32 consecutive fixed
    bytes. The caller must verify every relocation with the target matcher.
    """
    with target.open("rb") as stream:
        elf = ELFFile(stream)
        regions = [(s["sh_addr"], s.data()) for s in elf.iter_sections()
                   if s["sh_type"] == "SHT_PROGBITS" and s["sh_flags"] & 2 and not s["sh_flags"] & 5]
    found = {}
    with obj.open("rb") as stream:
        elf = ELFFile(stream)
        relocations = {}
        for section in elf.iter_sections():
            if isinstance(section, RelocationSection):
                relocations.setdefault(section["sh_info"], []).extend(section.iter_relocations())
        for index, section in enumerate(elf.iter_sections()):
            if section.name not in names or not section.name.startswith(".rodata"):
                continue
            if section["sh_type"] != "SHT_PROGBITS" or not section["sh_flags"] & 2 or section["sh_flags"] & 5:
                continue
            data, masked = section.data(), set()
            supported = True
            for relocation in relocations.get(index, []):
                width = {1: 8, 2: 4, 10: 4, 11: 4}.get(relocation["r_info_type"])
                start = relocation["r_offset"]
                if width is None or start < 0 or start + width > len(data):
                    supported = False
                    break
                masked.update(range(start, start + width))
            if not supported:
                continue
            runs, start = [], 0
            for offset in sorted(masked) + [len(data)]:
                if offset > start:
                    runs.append((start, offset))
                start = offset + 1
            anchor_start, anchor_end = max(runs, key=lambda r: r[1] - r[0], default=(0, 0))
            if anchor_end - anchor_start < 32:
                continue
            matches = []
            for base, region in regions:
                offset = region.find(data[anchor_start:anchor_end])
                while offset >= 0:
                    location = offset - anchor_start
                    if 0 <= location <= len(region) - len(data) and all(
                        i in masked or value == region[location + i] for i, value in enumerate(data)
                    ):
                        matches.append(base + location)
                    offset = region.find(data[anchor_start:anchor_end], offset + 1)
            aligned = [address for address in matches if address % max(1, section["sh_addralign"]) == 0]
            if aligned:
                found[section.name] = aligned
    return found


def readonly_sections(obj: Path, target: Path, names: set[str]) -> dict[str, int]:
    """Return patterns with a single candidate; relocations still need proof."""
    return {name: addresses[0] for name, addresses in readonly_candidates(obj, target, names).items() if len(addresses) == 1}


def place_readonly(root: Path, unit: str, placements: dict[str, int]) -> dict[str, int]:
    """Keep a constant pattern only at one fully matching target address."""
    report = root / "build" / "match" / BUILD / (unit.replace("/", "__").removesuffix(".cpp") + ".json")
    names = set(json.loads(report.read_text()).get("unplaced_sections", []))
    candidates = readonly_candidates(report.with_suffix(".o"), root / "orig" / BUILD / "Harvest", names)
    found = {}
    if not candidates:
        return found
    try:
        for name, addresses in candidates.items():
            exact = []
            for address in addresses:
                _register(root, unit, {**placements, name: address})
                _match(root, unit, learn=False)
                sections = json.loads(report.read_text()).get("sections", [])
                if any(s["name"] == name and s.get("exact") for s in sections):
                    exact.append(address)
            if len(exact) == 1:
                found[name] = exact[0]
    finally:
        _register(root, unit, placements)
        _match(root, unit, learn=False)
    return found


def place(harvest: Harvest, unit: str, literals: bool = False) -> dict:
    """Learn existing exact references, then verify temporary section placements.
    Preserve all existing placements. Whole-build scoring is a separate gate."""
    root = harvest.root
    config = root / "config" / BUILD / "units.toml"
    placements = dict(tomllib.loads(config.read_text()).get(unit, {}))
    report = harvest.reports / (harvest.slug(unit) + ".json")
    output = _match(root, unit, learn=True)
    sections = {}
    for _ in range(2):
        called = _place_called_sections(root, unit)
        called.update(place_readonly(root, unit, placements))
        if not called:
            break
        _register(root, unit, {**placements, **called})
        output = _match(root, unit, learn=True)
        verified = {s["name"] for s in json.loads(report.read_text())["sections"] if s.get("exact")}
        sections.update({name: address for name, address in called.items() if name in verified})
        # Learning can name the section without an explicit placement. Retain an
        # override only if the canonical comparison still leaves it unplaced.
        _register(root, unit, placements)
        output = _match(root, unit, learn=True)
        unplaced = set(json.loads(report.read_text()).get("unplaced_sections", []))
        placements.update({name: address for name, address in sections.items() if name in unplaced})
        _register(root, unit, placements)
    repaired = restore_literals(harvest, unit) if literals else []
    output = _match(root, unit, learn=True)
    return {"unit": unit, "sections": sections, "literals": repaired, "output": output}


def place_bss(harvest: Harvest) -> list[dict]:
    """Place every unplaced unit .bss whose static initializer proves its target address."""
    root = harvest.root
    config = tomllib.loads((root / "config" / BUILD / "units.toml").read_text())
    proposals = []
    for report_path in sorted(harvest.reports.glob("*.json")):
        report = json.loads(report_path.read_text())
        unit = report["unit"]
        if ".bss" not in report.get("unplaced_sections", []) or ".bss" in config.get(unit, {}):
            continue
        text = next((section for section in report["sections"] if section["name"] == ".text"), None)
        placed_by = text.get("placement", "") if text else ""
        if not text or not text.get("address") or not placed_by.startswith("symbol: "):
            continue
        anchor = placed_by.removeprefix("symbol: ")
        if not any(function.get("symbol") == anchor and function.get("address") == text["address"]
                   and function.get("exact") for function in text.get("functions", [])):
            continue
        placement = _place_bss(root, unit).get(".bss")
        if placement is not None:
            proposals.append({"unit": unit, "address": placement})
    for row in proposals:
        placements = {**config.get(row["unit"], {}), ".bss": row["address"]}
        _register(root, row["unit"], placements)
    return proposals


def candidate_claims(before: set[str], rows: list[dict], digest: str) -> dict[str, str]:
    """Evidence for new claims only; existing claims keep their provenance."""
    return {row["symbol"]: f"candidate-object:{digest}:{row['evidence']}" for row in rows if row["symbol"] not in before}


def learn_object(harvest: Harvest, unit: str, obj: Path) -> dict:
    """Let Harvest learn addresses from a compiled candidate, then rebuild the
    unchanged current source. This separates naming from a body restoration;
    the named addresses still require target byte or aligned-reference proof.
    """
    root = harvest.root
    path = root / "src" / unit
    source = path.read_bytes()
    table = root / "config" / BUILD / "symbols.tsv"
    with table.open() as stream:
        before = {row["symbol"] for row in csv.DictReader(stream, delimiter="\t")}
    obj = obj.resolve()
    digest = hashlib.sha256(obj.read_bytes()).hexdigest()
    done = subprocess.run(
        ["uv", "--no-config", "run", "--locked", "hv", "match", unit, "--object", str(obj), "--learn"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if done.returncode not in (0, 1):
        raise RuntimeError(done.stdout + done.stderr)
    # Internal functions may already resolve through inferred section positions,
    # so hv's unknown-reference learner cannot name them. A fully matching
    # caller still proves the call destination even when the callee is inexact.
    from concord import calls

    calls.learn_chain(root, unit, obj)
    with table.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    evidence = candidate_claims(before, rows, digest)
    if evidence:
        # Use the owning project's symbol writer; do not rewrite TSV text.
        script = """import json,sys
from hv import symbols
build=sys.argv[1]
evidence=json.load(sys.stdin)
rows=symbols.load(build)
symbols.save(build,[symbols.Symbol(s.address,s.size,s.name,evidence.get(s.name,s.evidence)) for s in rows])
"""
        subprocess.run(["uv", "--no-config", "run", "--locked", "python", "-c", script, BUILD],
                       cwd=root, input=json.dumps(evidence), text=True, check=True, capture_output=True)
    if path.read_bytes() != source:
        raise RuntimeError(f"{unit}: source changed during object learning")
    output = _match(root, unit, learn=True)
    if path.read_bytes() != source:
        raise RuntimeError(f"{unit}: canonical matching changed source")
    return {"unit": unit, "object_sha256": digest, "source_sha256": hashlib.sha256(source).hexdigest(),
            "learned": {row["symbol"]: {**row, "evidence": evidence[row["symbol"]]} for row in rows if row["symbol"] in evidence},
            "output": output}


def place_extent(harvest: Harvest, unit: str, symbol: str, address: int) -> dict:
    """Name an unnamed target extent only when the unit's compiled body matches it."""
    root = harvest.root
    source = root / "src" / unit
    obj = harvest.reports / f"{harvest.slug(unit)}.o"
    target = root / "orig" / BUILD / "Harvest"
    table = root / "config" / BUILD / "symbols.tsv"
    if not source.is_file():
        raise FileNotFoundError(f"{unit}: no source file in this Harvest checkout")
    match = extent_match(obj, target, symbol, address)
    if match is None:
        raise ValueError(f"{unit}: {symbol} does not exactly match target extent {address:#x}")
    with table.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    for row in rows:
        row_address = int(row["address"], 16)
        if row["symbol"] == symbol:
            if row_address == address and int(row["size"]) == match.size:
                return {"unit": unit, "symbol": symbol, "address": address, "size": match.size,
                        "fixed_bytes": match.fixed_bytes, "relocation_bytes": match.relocation_bytes,
                        "object_sha256": hashlib.sha256(obj.read_bytes()).hexdigest(), "already_named": True}
            raise ValueError(f"{symbol}: already assigned to {row_address:#x} ({row['size']} bytes)")
        if row_address == address:
            raise ValueError(f"{address:#x}: already assigned to {row['symbol']}")
    digest = hashlib.sha256(obj.read_bytes()).hexdigest()
    evidence = f"candidate-extent:{digest}:fixed={match.fixed_bytes}:relocations={match.relocation_bytes}"
    claim = {"address": address, "size": match.size, "symbol": symbol, "evidence": evidence}
    script = """import json,sys
from hv import symbols
build=sys.argv[1]
claim=json.load(sys.stdin)
rows=symbols.load(build)
if any(row.name == claim['symbol'] or row.address == claim['address'] for row in rows):
    raise SystemExit('symbol or target address became assigned during placement')
rows.append(symbols.Symbol(claim['address'],claim['size'],claim['symbol'],claim['evidence']))
symbols.save(build,rows)
"""
    subprocess.run(["uv", "--no-config", "run", "--locked", "python", "-c", script, BUILD],
                   cwd=root, input=json.dumps(claim), text=True, check=True, capture_output=True)
    return {"unit": unit, "symbol": symbol, "address": address, "size": match.size,
            "fixed_bytes": match.fixed_bytes, "relocation_bytes": match.relocation_bytes,
            "object_sha256": digest, "already_named": False}
