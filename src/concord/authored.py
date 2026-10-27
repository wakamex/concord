"""Authored source the diff calls for, measured before it is written.

The entries in AUTHORED carry source no ancestor has, so they are authored
rather than translated; each is a text edit applied inside the named function's
body and accepted only when the function is exact and the unit is no worse.

A recovered header can also have an incorrect named enum value. Its registered
correction is measured in every dependent unit and must improve the named
function without lowering another score; the whole-build gate follows.
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Parser

from concord.harvest import Evaluation, Harvest
from concord.search import other_scores, worse_elsewhere
from concord.transforms import CPP, _descendants, find_function


@dataclass(frozen=True)
class Edit:
    """One authored text edit inside a function's body."""

    name: str
    old: bytes
    new: bytes


@dataclass(frozen=True)
class ConstantEdit:
    """One authored correction to a named enumerator in a recovered header."""

    name: str
    header: str  # path under src/
    constant: str
    old: bytes
    new: bytes


AUTHORED: dict[tuple[str, str], list[Edit]] = {
    # runPPShader's post-processing path is daisy's own: Irrlicht 0.7's
    # CVideoNull has no post-processing services. The target clears the pass
    # material's bilinear flag with the other explicit overrides: its SMaterial
    # flag stores put a 0 at struct offset 0x2f between the ZWriteEnable and
    # TrilinearFilter stores, where ours keeps the constructor's true.
    (
        "daisy/video/Null/CVideoNull.cpp",
        "_ZN5daisy5video10CVideoNull11runPPShaderEiRN2ox4core5CRectIiEEPS5_S7_j",
    ): [
        Edit(
            "bilinear-filter",
            b"    material.ZWriteEnable = false;\n    material.TrilinearFilter = false;",
            b"    material.ZWriteEnable = false;\n    material.BilinearFilter = false;\n    material.TrilinearFilter = false;",
        ),
    ],
}

HEADER_AUTHORED: dict[tuple[str, str], ConstantEdit] = {
    # NVIDIA's Cg header defines the program parameter namespace as 4109:
    # https://github.com/NVIDIAGameWorks/PhysX/blob/4.1/externals/cg-linux/include/Cg/cg.h
    (
        "daisy/video/Null/CCGMaterialRenderer.cpp",
        "_ZN5daisy5video19CCGMaterialRenderer14printVariablesEv",
    ): ConstantEdit("cg-program-namespace", "daisy/video/CgApi.h", "CG_PROGRAM", b"4125", b"4109"),
}


def constant_candidate(source: bytes, edit: ConstantEdit) -> bytes | None:
    """Replace exactly one matching enumerator's literal, leaving all other text."""
    root = Parser(CPP).parse(source).root_node
    matches = [node for node in _descendants(root, "enumerator")
               if (name := node.child_by_field_name("name")) and name.text == edit.constant.encode()]
    if len(matches) != 1:
        return None
    value = matches[0].child_by_field_name("value")
    if value is None or value.type != "number_literal" or value.text != edit.old:
        return None
    return source[:value.start_byte] + edit.new + source[value.end_byte:]


def candidates(source: bytes, symbol: str, edits: list[Edit]) -> dict[str, bytes]:
    """One candidate source per edit whose old text appears exactly once inside
    the named function's body."""
    scope = find_function(source, symbol)
    body = source[scope.start_byte : scope.end_byte]
    found = {}
    for edit in edits:
        if body.count(edit.old) == 1:
            found[edit.name] = source[: scope.start_byte] + body.replace(edit.old, edit.new, 1) + source[scope.end_byte :]
    return found


def keep(before: Evaluation, after: Evaluation, symbol: str) -> bool:
    """Whether the authored candidate may replace the unit's source: it compiles,
    the named function is exact, every previously exact function stays exact,
    and no other function of the unit scores lower."""
    if after.error:
        return False
    verdict = after.verdict(symbol)
    if verdict is None or not verdict.row["exact"]:
        return False
    return before.exact_functions() <= after.exact_functions() and not worse_elsewhere(other_scores(before, symbol), after, symbol)


def _preserves(before: Evaluation, after: Evaluation) -> bool:
    return (not after.error and before.exact_functions() <= after.exact_functions()
            and all(name in after.fuzzy and after.fuzzy[name] >= score for name, score in before.fuzzy.items()))


def keep_improvement(before: Evaluation, after: Evaluation, symbol: str) -> bool:
    """Accept a measured gain when no function in the unit becomes worse."""
    old, new = before.verdict(symbol), after.verdict(symbol)
    old_score, new_score = before.fuzzy.get(symbol), after.fuzzy.get(symbol)
    return bool(old and new and old_score is not None and new_score is not None
                and (new_score > old_score or (new.row["exact"] and not old.row["exact"]))
                and _preserves(before, after))


def apply_header(harvest: Harvest, unit: str, symbol: str, edit: ConstantEdit, write: bool) -> dict:
    """Measure a registered header correction in every dependent unit."""
    path = harvest.root / "src" / edit.header
    source = path.read_bytes()
    candidate = constant_candidate(source, edit)
    if candidate is None:
        raise ValueError(f"no authored constant candidate applies to {edit.constant} in {edit.header}")
    units = sorted(set(harvest.dependents(f"src/{edit.header}")) | {unit})
    items = {}
    for n, dependent in enumerate(units):
        items[f"before-{n}"] = (dependent, {})
        items[f"after-{n}"] = (dependent, {f"src/{edit.header}": candidate})
    evaluations = harvest.evaluate_overlays(items)
    pairs = {dependent: (evaluations[f"before-{n}"], evaluations[f"after-{n}"])
             for n, dependent in enumerate(units)}
    for dependent, (before, _) in pairs.items():
        if before.error:
            raise RuntimeError(f"the unit {dependent} does not compile:\n{before.error}")
    before, after = pairs[unit]
    verdict = after.verdict(symbol)
    accepted = keep_improvement(before, after, symbol) and all(_preserves(*pair) for pair in pairs.values())
    if accepted and write:
        if path.read_bytes() != source:
            raise ValueError(f"{edit.header} changed while candidates compiled")
        path.write_bytes(candidate)
    return {
        "unit": unit,
        "path": edit.header,
        "symbol": symbol,
        "dependents": units,
        "candidates": {edit.name: {"error": after.error or None, "exact": bool(verdict and verdict.row["exact"]),
                                   "before": before.fuzzy.get(symbol), "score": after.fuzzy.get(symbol), "kept": accepted}},
        "chosen": edit.name if accepted else None,
        "applied": bool(accepted and write),
        "original": source,
        "source": candidate if accepted else None,
    }


def apply(harvest: Harvest, unit: str, symbol: str, write: bool) -> dict:
    """Measure registered authored candidates and optionally write an accepted one."""
    unit = harvest.source(unit)
    if edit := HEADER_AUTHORED.get((unit, symbol)):
        return apply_header(harvest, unit, symbol, edit, write)
    path = harvest.root / "src" / unit
    source = path.read_bytes()
    forms = candidates(source, symbol, AUTHORED.get((unit, symbol), []))
    if not forms:
        raise ValueError(f"no authored candidate applies to {symbol} in {unit}")
    evaluations = harvest.evaluate(unit, {"baseline": source, **forms})
    baseline = evaluations["baseline"]
    if baseline.error:
        raise RuntimeError(f"the unit's own source does not compile:\n{baseline.error}")
    verdicts = {}
    for name, row in evaluations.items():
        if name == "baseline":
            continue
        verdict = row.verdict(symbol)
        verdicts[name] = {
            "error": row.error or None,
            "exact": bool(verdict and verdict.row["exact"]),
            "score": row.fuzzy.get(symbol),
            "kept": keep(baseline, row, symbol),
        }
    chosen = next((name for name, row in verdicts.items() if row["kept"]), None)
    if chosen and write:
        if path.read_bytes() != source:
            raise ValueError(f"{unit} changed while candidates compiled")
        path.write_bytes(forms[chosen])
    return {
        "unit": unit,
        "symbol": symbol,
        "candidates": verdicts,
        "chosen": chosen,
        "applied": bool(chosen and write),
        "original": source,
        "source": forms.get(chosen),
    }
