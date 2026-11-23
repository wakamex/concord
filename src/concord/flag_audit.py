"""Measure compiler options on unchanged units without changing the build flags.

`audit` compiles Harvest units; `gruntz_audit` compiles gruntz-decomp units through the checkout's driver, with each
variant's flags, and compares every function with the unit's configured flags."""

from __future__ import annotations

import hashlib
import re
import shlex
import time
from collections import defaultdict
from dataclasses import dataclass
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


BASELINE = "baseline"
GRUNTZ_JOBS = 4  # the most gruntz-decomp compiles at once
NAME = re.compile(r"[A-Za-z0-9_-]+")  # a variant's name is part of a candidate's file name


@dataclass(frozen=True)
class Variant:
    """A named change to a gruntz-decomp unit's compile. `remove` drops options from the unit's configured flags, `add`
    appends options, and `codegen` replaces c2.exe options (as (old, new) pairs) in the unit's captured intermediate
    language. A variant has cl options or c2 options, never both."""

    name: str
    remove: tuple[str, ...] = ()
    add: tuple[str, ...] = ()
    codegen: tuple[tuple[str, str], ...] = ()


def parse_variant(spec: str) -> Variant:
    """A variant from NAME=FLAGS. A word of FLAGS adds an option, `!OPTION` removes a configured one, and
    `c2:OLD=NEW` replaces a c2.exe option, as in `-G5` for `-G6`."""
    name, separator, text = spec.partition("=")
    if not separator or not NAME.fullmatch(name) or name == BASELINE:
        raise ValueError(f"{spec!r} is not NAME=FLAGS, with a name of letters, digits, '-' or '_' other than {BASELINE}")
    remove, add, codegen = [], [], []
    for word in shlex.split(text):
        if word.startswith("!"):
            if len(word) == 1:
                raise ValueError(f"variant {name} has a bare '!' with no option to remove")
            remove.append(word[1:])
        elif word.startswith("c2:"):
            old, equals, new = word[3:].partition("=")
            if not equals or not old or not new:
                raise ValueError(f"{word!r} is not c2:OLD=NEW")
            codegen.append((old, new))
        else:
            add.append(word)
    if codegen and (remove or add):
        raise ValueError(f"variant {name} takes c2 options or cl options, not both")
    if not (remove or add or codegen):
        raise ValueError(f"variant {name} changes no flag")
    return Variant(name, tuple(remove), tuple(add), tuple(codegen))


def effective_flags(configured: list[str], variant: Variant) -> list[str]:
    """The unit's flags with the variant applied. A removed option must be one of the configured flags."""
    missing = [option for option in variant.remove if option not in configured]
    if missing:
        raise ValueError(f"{' '.join(missing)} is not among the unit's flags: {' '.join(configured)}")
    return [flag for flag in configured if flag not in variant.remove] + list(variant.add)


def default_variants(configured: list[str]) -> list[Variant]:
    """The variants every gruntz-decomp unit is measured with: the optimization levels and options the audit asks for,
    with /O2 without /Og in place of /Og-, the /G options, c2.exe's -G6 in place of -G5, and the unit's /GX and /GR
    toggled. A unit without /GX or /GR gets that switch added instead."""
    variants = [
        Variant("ox", remove=("/O2",), add=("/Ox",)),
        Variant("o1", remove=("/O2",), add=("/O1",)),
        Variant("ob0", add=("/Ob0",)),
        Variant("ob2", add=("/Ob2",)),
        Variant("oy-", add=("/Oy-",)),
        Variant("op", add=("/Op",)),
        Variant("o2-no-g", remove=("/O2",), add=("/Oi", "/Ot", "/Oy", "/Ob2")),
        Variant("gy", add=("/Gy",)),
        Variant("gf", add=("/Gf",)),
        Variant("g4", add=("/G4",)),
        Variant("g6", add=("/G6",)),
        Variant("gb", add=("/GB",)),
        Variant("c2-g6", codegen=(("-G5", "-G6"),)),
    ]
    for switch in ("/GX", "/GR"):
        if switch in configured:
            variants.append(Variant(f"no-{switch[1:].lower()}", remove=(switch,)))
        else:
            variants.append(Variant(switch[1:].lower(), add=(switch,)))
    return variants


def _rows(evaluation) -> dict[str, dict]:
    return {row["symbol"]: row for section in evaluation.sections for row in section.get("functions", [])}


def _compare(exact: set[str], scores: dict[str, float], base_exact: set[str], base_scores: dict[str, float]) -> dict:
    """What a compile gains and loses against the configured flags' compile: exact functions gained and lost, and
    each function that was open there (not exact) with its score change. A score that rises is a gain."""
    gains = {s: scores[s] - base_scores[s] for s in base_scores if s not in base_exact and s in scores}
    ranked = sorted((s for s in gains if gains[s] > 0), key=lambda s: -gains[s])
    return {
        "exact": len(exact),
        "gained_exact": sorted(exact - base_exact),
        "lost_exact": sorted(base_exact - exact),
        "raised_by_1": sum(1 for gain in gains.values() if gain >= 1),
        "largest_gains": [{"symbol": s, "from": base_scores[s], "to": scores[s]} for s in ranked[:5]],
        "scores": scores,
    }


def _measured(name: str, flags: list[str], evaluation, base_exact: set[str], base_scores: dict[str, float]) -> dict:
    row = {"name": name, "flags": flags}
    if evaluation.error:
        return {**row, "error": evaluation.error[-2000:]}
    rows = _rows(evaluation)
    exact = {symbol for symbol, found in rows.items() if found["exact"]}
    return {**row, **_compare(exact, {symbol: found["fuzzy"] for symbol, found in rows.items()}, base_exact, base_scores)}


def _work_dir(evaluation) -> Path:
    """The directory a Gruntz evaluation's files were written to; its retail object is there."""
    return Path(evaluation.sections[0]["retail"]).parent


def gruntz_unit(target, unit: str, variants: list[Variant], jobs: int) -> dict:
    """Compile a gruntz-decomp unit with its configured flags and with each variant's, and compare each variant with the
    configured compile. A c2 variant replays the unit's captured intermediate language with its options replaced, and an
    unedited replay checks that the replay reproduces the configured compile. A variant that fails to compile is recorded
    with its error and not retried. `target` is a Gruntz checkout."""
    configured = target.configured_flags(unit)
    source = target.source_path(unit).read_bytes()
    flags = {BASELINE: configured}
    problems = {}
    for variant in variants:
        if variant.codegen:
            continue
        try:
            flags[variant.name] = effective_flags(configured, variant)
        except ValueError as error:
            problems[variant.name] = str(error)
    evaluations = target.evaluate(unit, {name: source for name in flags}, flags=flags, jobs=jobs)
    entry = {"unit": unit, "configured_flags": configured, "variants": []}
    try:
        baseline = evaluations[BASELINE]
        if baseline.error:
            return {**entry, "error": baseline.error[-2000:]}
        base_rows = _rows(baseline)
        base_exact = {symbol for symbol, found in base_rows.items() if found["exact"]}
        base_scores = {symbol: found["fuzzy"] for symbol, found in base_rows.items()}
        report = target.report_exact(unit)
        entry["baseline"] = {"exact": len(base_exact), "functions": len(base_rows), "scores": base_scores,
                             "matches_report": base_exact == report, "report_only": sorted(report - base_exact),
                             "compile_only": sorted(base_exact - report)}  # fmt: skip
        results = {}
        for variant in variants:
            if variant.codegen:
                continue
            if variant.name in problems:
                results[variant.name] = {"name": variant.name, "flags": None, "error": problems[variant.name]}
            else:
                results[variant.name] = _measured(variant.name, flags[variant.name], evaluations[variant.name], base_exact, base_scores)
        results.update(_replays(target, unit, source, configured, [v for v in variants if v.codegen], jobs, base_exact, base_scores, entry))
        entry["variants"] = [results[variant.name] for variant in variants]
        return entry
    finally:
        for evaluation in evaluations.values():
            target.discard(_work_dir(evaluation))


def _replays(target, unit: str, source: bytes, configured: list[str], variants: list[Variant], jobs: int,
             base_exact: set[str], base_scores: dict[str, float], entry: dict) -> dict[str, dict]:
    """Replay the captured intermediate language of each c2 variant, and an unedited control replay, which must give the
    configured compile's functions for the replays to mean anything. Records the control in `entry`."""
    results = {}
    if not variants:
        return results
    il, capture = target.capture_il(unit, source)
    copies = {}
    try:
        if il is None:
            for variant in variants:
                results[variant.name] = {"name": variant.name, "flags": configured, "codegen": [list(p) for p in variant.codegen],
                                         "error": f"the capture wrote no intermediate language: {capture.error[-1500:]}"}
            return results
        copies["control"] = target.edited_il(unit, il, [])
        for variant in variants:
            try:
                copies[variant.name] = target.edited_il(unit, il, list(variant.codegen))
            except ValueError as error:
                results[variant.name] = {"name": variant.name, "flags": configured, "codegen": [list(p) for p in variant.codegen],
                                         "error": str(error)}  # fmt: skip
        replays = target.replay_many(unit, copies, jobs)
        control = replays.pop("control")
        entry["replay_control"] = {"matches_configured": (not control.error) and _same(control, base_exact, base_scores)}
        for variant in variants:
            if variant.name in copies and variant.name not in results:
                row = _measured(variant.name, configured, replays[variant.name], base_exact, base_scores)
                results[variant.name] = {**row, "codegen": [list(p) for p in variant.codegen]}
        return results
    finally:
        if il is not None:
            target.discard(il.parent)
        for copy in copies.values():
            target.discard(copy.parent)


def _same(evaluation, base_exact: set[str], base_scores: dict[str, float]) -> bool:
    """Whether an evaluation has the same exact functions and the same score for every function as the baseline."""
    rows = _rows(evaluation)
    return {s for s, found in rows.items() if found["exact"]} == base_exact and {s: f["fuzzy"] for s, f in rows.items()} == base_scores


def gruntz_audit(target, units: list[str], variants_for, *, jobs: int, budget: float | None = None,
                 finished: dict[str, dict] | None = None, record=None) -> dict:
    """gruntz_unit for each unit in order. A unit in `finished` (an earlier run's entry) is kept when it has the same
    variants. No unit starts once `budget` seconds have passed, and the units not started are listed as remaining. Each
    new entry goes to `record`, so a caller that saves progress loses at most the unit in progress."""
    started = time.monotonic()
    finished = finished or {}
    done, remaining = {}, []
    for unit in units:
        variants = variants_for(target.configured_flags(unit))
        cached = finished.get(unit)
        if cached and [row["name"] for row in cached["variants"]] == [v.name for v in variants]:
            done[unit] = cached
            continue
        if budget is not None and time.monotonic() - started >= budget:
            remaining.append(unit)
            continue
        done[unit] = gruntz_unit(target, unit, variants, jobs)
        if record:
            record(done[unit])
    return {"units": [done[unit] for unit in units if unit in done], "remaining": remaining,
            "elapsed_seconds": round(time.monotonic() - started, 1)}


def summary(entry: dict) -> str:
    """One line for a unit: its exact count at the configured flags, then the variants that gain or lose exact functions,
    the number of open functions each raises by a point or more, and the variants that failed to compile."""
    if "error" in entry:
        return f"{entry['unit']}: the configured flags failed to compile: {entry['error'].strip().splitlines()[-1][:200]}"
    baseline = entry["baseline"]
    rows = [row for row in entry["variants"] if "error" not in row]
    gains = [f"{row['name']} +{len(row['gained_exact'])}" for row in rows if row["gained_exact"]]
    losses = [f"{row['name']} -{len(row['lost_exact'])}" for row in rows if row["lost_exact"]]
    raised = [f"{row['name']} ({row['raised_by_1']})" for row in rows if row["raised_by_1"]]
    failed = [row["name"] for row in entry["variants"] if "error" in row]
    control = "" if entry.get("replay_control", {}).get("matches_configured", True) else "; replay control differs"
    return (f"{entry['unit']}: {baseline['exact']}/{baseline['functions']} exact at the configured flags; gains {', '.join(gains) or 'none'}; "
            f"losses {', '.join(losses) or 'none'}; raised {', '.join(raised) or 'none'}; failed {', '.join(failed) or 'none'}{control}")
