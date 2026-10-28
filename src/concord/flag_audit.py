"""Measure compiler options on unchanged units without changing the build flags."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from concord.harvest import Harvest


def executable_code(path: Path) -> tuple:
    """Executable section bytes and relocations, independent of debug sections."""
    with path.open("rb") as stream:
        sections = list(ELFFile(stream).iter_sections())
        code = {index for index, section in enumerate(sections) if section["sh_flags"] & 4}
        relocations = defaultdict(list)
        for section in sections:
            if not isinstance(section, RelocationSection) or section["sh_info"] not in code:
                continue
            symbols = sections[section["sh_link"]]
            for row in section.iter_relocations():
                symbol = symbols.get_symbol(row["r_info_sym"])
                index = symbol["st_shndx"]
                target_section = sections[index].name if isinstance(index, int) and index < len(sections) else str(index)
                relocations[section["sh_info"]].append(
                    (row["r_offset"], row["r_info_type"], symbol.name, target_section,
                     symbol["st_value"], row["r_addend"] if section.is_RELA() else 0)
                )
        return tuple(sorted((sections[index].name, sections[index].data(), tuple(sorted(relocations[index]))) for index in code))


def audit(harvest: Harvest, units: list[str], variants: dict[str, tuple[str, ...]]) -> dict:
    """Compile each unit with its baseline flags and each named extra option set."""
    if not units or len(units) != len(set(units)) or "baseline" in variants:
        raise ValueError("supply distinct units and reserve the variant name baseline")
    choices = {"baseline": (), **variants}
    items = {}
    names = {}
    for unit in units:
        for variant, flags in choices.items():
            name = f"v{len(items)}"
            items[name] = (unit, {}, flags)
            names[unit, variant] = name
    started = time.monotonic()
    evaluations = harvest.evaluate_overlays(items, baseline_target_fallback=True)
    elapsed = time.monotonic() - started
    result = []
    for unit in units:
        baseline = evaluations[names[unit, "baseline"]]
        if baseline.error:
            raise ValueError(f"baseline compile failed for {unit}: {baseline.error}")
        baseline_exact = baseline.exact_functions()
        baseline_code = executable_code(baseline.object) if baseline.object else None
        measured = []
        for variant, flags in choices.items():
            evaluation = evaluations[names[unit, variant]]
            row = {"name": variant, "flags": list(flags)}
            if evaluation.error:
                row["error"] = evaluation.error
            else:
                exact = evaluation.exact_functions()
                row.update(
                    exact=len(exact),
                    exact_functions=sorted(exact),
                    lost_exact=sorted(baseline_exact - exact),
                    gained_exact=sorted(exact - baseline_exact),
                    scores=evaluation.fuzzy,
                )
                if evaluation.object:
                    row["object_sha256"] = hashlib.sha256(Path(evaluation.object).read_bytes()).hexdigest()
                    if baseline_code is not None:
                        row["same_executable_as_baseline"] = executable_code(evaluation.object) == baseline_code
                if evaluation.score_basis:
                    row["score_basis"] = evaluation.score_basis
                if evaluation.score_error:
                    row["score_error"] = evaluation.score_error
            measured.append(row)
        result.append({"unit": unit, "variants": measured})
    return {"elapsed_seconds": elapsed, "units": result}
