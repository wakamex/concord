"""Refresh references and place called template sections without re-seeding source."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from tree_sitter import Node

from concord.harvest import BUILD, Harvest
from concord.irrlicht import translate
from concord.search import other_scores, worse_elsewhere
from concord.seed import IRRLICHT, _match, _place_bss, _place_called_sections, _register
from concord.transforms import find_function


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
