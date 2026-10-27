"""Authored source the diff calls for, measured before it is written.

The entries in AUTHORED carry source no ancestor has, so they are authored
rather than translated; each is a text edit applied inside the named function's
body and accepted when the function is exact, or scores higher with the
oracle's agreement like `permute --apply-improved`, and the unit is no worse.

A recovered header can also have an incorrect named enum value. Its registered
correction is measured in every dependent unit and must improve the named
function without lowering another score; the whole-build gate follows.
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Parser

from concord import oracle
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
    # updateState's game-over exit: the target stores NextState = 3 on each arm
    # of the campaign check -- a `mov [this+0x50],3` behind `je` to a lone
    # store block, and another ahead of the highscore evaluation -- where ours
    # keeps one hoisted store before `if (GameMode != EGM_CAMPAIGN)`. CPlayState
    # is Harvest's own, so the two-arm form is authored.
    (
        "HarvestFull/harvest/states/CPlayState.cpp",
        "_ZN7harvest6states10CPlayState11updateStateEf",
    ): [
        Edit(
            "campaign-nextstate",
            b"            NextState = 3;\n            if (GameMode != game::EGM_CAMPAIGN)\n            {\n                bool highscore;",
            b"            if (GameMode != game::EGM_CAMPAIGN)\n            {\n                NextState = 3;\n                bool highscore;",
        ),
        Edit(
            "campaign-nextstate-else",
            b"                else\n                    game::CHighscoreInfo::setNewHighscoreInfo(0);\n            }\n        }",
            b"                else\n                    game::CHighscoreInfo::setNewHighscoreInfo(0);\n            }\n            else\n                NextState = 3;\n        }",
        ),
        Edit(
            "highscore-direct",
            b"                bool highscore;\n                if (GameWon)\n                    highscore = GameMode != game::EGM_CREATIVE;\n                else\n                    highscore = GameMode == game::EGM_NORMAL || GameMode == game::EGM_INSANE;\n                if (highscore)\n                    game::CHighscoreInfo::setNewHighscoreInfo(new game::CHighscoreInfo(PlayerName.c_str(),\n                        PlayerGroup.c_str(), RandomValue, StartTime, GameMode, game::gp_world->getPlanet(),\n                        game::gp_statistics->getGameStatValue(1), ThreatLevel->getThreatLevel(), GameTime));\n                else\n                    game::CHighscoreInfo::setNewHighscoreInfo(0);",
            b"                if (GameWon)\n                {\n                    if (GameMode != game::EGM_CREATIVE)\n                        game::CHighscoreInfo::setNewHighscoreInfo(new game::CHighscoreInfo(PlayerName.c_str(),\n                            PlayerGroup.c_str(), RandomValue, StartTime, GameMode, game::gp_world->getPlanet(),\n                            game::gp_statistics->getGameStatValue(1), ThreatLevel->getThreatLevel(), GameTime));\n                    else\n                        game::CHighscoreInfo::setNewHighscoreInfo(0);\n                }\n                else\n                {\n                    if (GameMode == game::EGM_NORMAL || GameMode == game::EGM_INSANE)\n                        game::CHighscoreInfo::setNewHighscoreInfo(new game::CHighscoreInfo(PlayerName.c_str(),\n                            PlayerGroup.c_str(), RandomValue, StartTime, GameMode, game::gp_world->getPlanet(),\n                            game::gp_statistics->getGameStatValue(1), ThreatLevel->getThreatLevel(), GameTime));\n                    else\n                        game::CHighscoreInfo::setNewHighscoreInfo(0);\n                }",
        ),
    ],
    # renderMinimap: the target draws the clipped marker square's four edges in
    # both it->Time branches (eight MINIMAP_MARKER_COLOR reads, one shared clip
    # block in ours), and passes a literal SColor(0x64fe8280) to the progress bar
    # where ours loads THREAT_TEXT_COLOR from .bss.
    (
        "HarvestFull/harvest/states/CPlayState.cpp",
        "_ZN7harvest6states10CPlayState13renderMinimapEv",
    ): [
        Edit(
            "progress-bar-color",
            b"            GuiSprites[GS_PROGRESS_BAR]->draw(position, &clip, THREAT_TEXT_COLOR);",
            b"            GuiSprites[GS_PROGRESS_BAR]->draw(position, &clip, ox::video::SColor(0x64fe8280));",
        ),
        Edit(
            "marker-box-per-branch",
            b"        ox::core::CRect<int> box;\n        if (it->Time <= 4.0f)\n            box = ox::core::CRect<int>(position.X - 3, position.Y - 3, position.X + 4, position.Y + 4);\n        else\n        {\n            int size = (int)((it->Time - 4.0f) * 100.0f) + 6;\n            box.UpperLeftCorner = position - ox::core::CPosition2d<int>(size / 2, size / 2);\n            box.LowerRightCorner = box.UpperLeftCorner + ox::core::CPosition2d<int>(size, size);\n        }\n\n        // Clip the square to the minimap and skip the edges that were cut.\n        bool left = true;\n        if (box.UpperLeftCorner.X < area.UpperLeftCorner.X)\n        {\n            box.UpperLeftCorner.X = area.UpperLeftCorner.X;\n            left = false;\n        }\n        bool top = true;\n        if (box.UpperLeftCorner.Y < area.UpperLeftCorner.Y)\n        {\n            box.UpperLeftCorner.Y = area.UpperLeftCorner.Y;\n            top = false;\n        }\n        bool right = true;\n        if (box.LowerRightCorner.X >= area.LowerRightCorner.X)\n        {\n            box.LowerRightCorner.X = area.LowerRightCorner.X - 1;\n            right = false;\n        }\n        bool bottom = true;\n        if (box.LowerRightCorner.Y >= area.LowerRightCorner.Y)\n        {\n            box.LowerRightCorner.Y = area.LowerRightCorner.Y - 1;\n            bottom = false;\n        }\n        if (top)\n            Driver->draw2DLine(box.UpperLeftCorner,\n                ox::core::CPosition2d<int>(box.LowerRightCorner.X, box.UpperLeftCorner.Y), MINIMAP_MARKER_COLOR);\n        if (left)\n            Driver->draw2DLine(box.UpperLeftCorner,\n                ox::core::CPosition2d<int>(box.UpperLeftCorner.X, box.LowerRightCorner.Y), MINIMAP_MARKER_COLOR);\n        if (right)\n            Driver->draw2DLine(box.LowerRightCorner,\n                ox::core::CPosition2d<int>(box.LowerRightCorner.X, box.UpperLeftCorner.Y), MINIMAP_MARKER_COLOR);\n        if (bottom)\n            Driver->draw2DLine(box.LowerRightCorner,\n                ox::core::CPosition2d<int>(box.UpperLeftCorner.X, box.LowerRightCorner.Y), MINIMAP_MARKER_COLOR);",
            b"        if (it->Time <= 4.0f)\n        {\n            ox::core::CRect<int> box(position.X - 3, position.Y - 3, position.X + 4, position.Y + 4);\n            // Clip the square to the minimap and skip the edges that were cut.\n            bool left = true;\n            if (box.UpperLeftCorner.X < area.UpperLeftCorner.X)\n            {\n                box.UpperLeftCorner.X = area.UpperLeftCorner.X;\n                left = false;\n            }\n            bool top = true;\n            if (box.UpperLeftCorner.Y < area.UpperLeftCorner.Y)\n            {\n                box.UpperLeftCorner.Y = area.UpperLeftCorner.Y;\n                top = false;\n            }\n            bool right = true;\n            if (box.LowerRightCorner.X >= area.LowerRightCorner.X)\n            {\n                box.LowerRightCorner.X = area.LowerRightCorner.X - 1;\n                right = false;\n            }\n            bool bottom = true;\n            if (box.LowerRightCorner.Y >= area.LowerRightCorner.Y)\n            {\n                box.LowerRightCorner.Y = area.LowerRightCorner.Y - 1;\n                bottom = false;\n            }\n            if (top)\n                Driver->draw2DLine(box.UpperLeftCorner,\n                    ox::core::CPosition2d<int>(box.LowerRightCorner.X, box.UpperLeftCorner.Y), MINIMAP_MARKER_COLOR);\n            if (left)\n                Driver->draw2DLine(box.UpperLeftCorner,\n                    ox::core::CPosition2d<int>(box.UpperLeftCorner.X, box.LowerRightCorner.Y), MINIMAP_MARKER_COLOR);\n            if (right)\n                Driver->draw2DLine(box.LowerRightCorner,\n                    ox::core::CPosition2d<int>(box.LowerRightCorner.X, box.UpperLeftCorner.Y), MINIMAP_MARKER_COLOR);\n            if (bottom)\n                Driver->draw2DLine(box.LowerRightCorner,\n                    ox::core::CPosition2d<int>(box.UpperLeftCorner.X, box.LowerRightCorner.Y), MINIMAP_MARKER_COLOR);\n        }\n        else\n        {\n            int size = (int)((it->Time - 4.0f) * 100.0f) + 6;\n            ox::core::CRect<int> box;\n            box.UpperLeftCorner = position - ox::core::CPosition2d<int>(size / 2, size / 2);\n            box.LowerRightCorner = box.UpperLeftCorner + ox::core::CPosition2d<int>(size, size);\n            // Clip the square to the minimap and skip the edges that were cut.\n            bool left = true;\n            if (box.UpperLeftCorner.X < area.UpperLeftCorner.X)\n            {\n                box.UpperLeftCorner.X = area.UpperLeftCorner.X;\n                left = false;\n            }\n            bool top = true;\n            if (box.UpperLeftCorner.Y < area.UpperLeftCorner.Y)\n            {\n                box.UpperLeftCorner.Y = area.UpperLeftCorner.Y;\n                top = false;\n            }\n            bool right = true;\n            if (box.LowerRightCorner.X >= area.LowerRightCorner.X)\n            {\n                box.LowerRightCorner.X = area.LowerRightCorner.X - 1;\n                right = false;\n            }\n            bool bottom = true;\n            if (box.LowerRightCorner.Y >= area.LowerRightCorner.Y)\n            {\n                box.LowerRightCorner.Y = area.LowerRightCorner.Y - 1;\n                bottom = false;\n            }\n            if (top)\n                Driver->draw2DLine(box.UpperLeftCorner,\n                    ox::core::CPosition2d<int>(box.LowerRightCorner.X, box.UpperLeftCorner.Y), MINIMAP_MARKER_COLOR);\n            if (left)\n                Driver->draw2DLine(box.UpperLeftCorner,\n                    ox::core::CPosition2d<int>(box.UpperLeftCorner.X, box.LowerRightCorner.Y), MINIMAP_MARKER_COLOR);\n            if (right)\n                Driver->draw2DLine(box.LowerRightCorner,\n                    ox::core::CPosition2d<int>(box.LowerRightCorner.X, box.UpperLeftCorner.Y), MINIMAP_MARKER_COLOR);\n            if (bottom)\n                Driver->draw2DLine(box.LowerRightCorner,\n                    ox::core::CPosition2d<int>(box.UpperLeftCorner.X, box.LowerRightCorner.Y), MINIMAP_MARKER_COLOR);\n        }",
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
    the named function's body, plus one candidate applying every matching edit
    together when two or more do: a function can need several disjoint edits to
    reach exact, and single-edit candidates cannot combine."""
    scope = find_function(source, symbol)
    body = source[scope.start_byte : scope.end_byte]
    found = {}
    combined, names = body, []
    for edit in edits:
        if body.count(edit.old) == 1:
            found[edit.name] = source[: scope.start_byte] + body.replace(edit.old, edit.new, 1) + source[scope.end_byte :]
        if combined.count(edit.old) == 1:
            combined = combined.replace(edit.old, edit.new, 1)
            names.append(edit.name)
    if len(names) > 1:
        found["+".join(names)] = source[: scope.start_byte] + combined + source[scope.end_byte :]
    return found


def keep(before: Evaluation, after: Evaluation, symbol: str) -> bool:
    """Whether the authored candidate may replace the unit's source: it compiles,
    the named function is exact or scores higher than before, every previously
    exact function stays exact, and no other function of the unit scores lower.
    An inexact candidate also needs the oracle's agreement; apply() runs it."""
    if after.error:
        return False
    verdict = after.verdict(symbol)
    if verdict is None or (not verdict.row["exact"] and (after.fuzzy.get(symbol) or 0.0) <= (before.fuzzy.get(symbol) or 0.0)):
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
    """Measure registered authored candidates and optionally write an accepted
    one. A function-body candidate is kept when the function is exact, or
    scores higher with the oracle's agreement, and no other function of the
    unit is worse; an exact candidate always wins, then the one applying more
    of the function's diagnosed edits, then the higher score."""
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
        kept = keep(baseline, row, symbol)
        evidence = None
        if kept and not verdict.row["exact"]:
            kept, evidence = oracle.gate(harvest, unit, symbol, source, forms[name])
        verdicts[name] = {
            "error": row.error or None,
            "exact": bool(verdict and verdict.row["exact"]),
            "score": row.fuzzy.get(symbol),
            "kept": kept,
            "oracle": evidence,
        }
    chosen = max(
        (name for name, row in verdicts.items() if row["kept"]),
        key=lambda name: (verdicts[name]["exact"], name.count("+"), verdicts[name]["score"] or 0.0),
        default=None,
    )
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
