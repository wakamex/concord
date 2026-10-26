"""Authored source the diff calls for, kept only when it is proven exact.

Goal 5: a person, an agent or a model writes the source the diff calls for in
the project's idiom, and concord keeps it when the function is exact and no
other function of the unit scores lower. The entries in AUTHORED carry source
no ancestor has, so they are authored rather than translated; each is a text
edit applied inside the named function's body.
"""

from __future__ import annotations

from dataclasses import dataclass

from concord.harvest import Evaluation, Harvest
from concord.search import other_scores, worse_elsewhere
from concord.transforms import find_function


@dataclass(frozen=True)
class Edit:
    """One authored text edit inside a function's body."""

    name: str
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


def apply(harvest: Harvest, unit: str, symbol: str, write: bool) -> dict:
    """Compile the authored candidates of the function against the baseline and
    keep one that is exact with no other function of the unit worse."""
    unit = harvest.source(unit)
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
