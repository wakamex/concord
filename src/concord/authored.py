"""Authored source the diff calls for, measured before it is written.

The entries in AUTHORED carry source no ancestor has, so they are authored
rather than translated; each is a text edit applied inside the named function's
body and accepted when the function is exact, or scores higher with the
oracle's agreement like `permute --apply-improved`, and the unit is no worse.

A recovered header can also have an incorrect enum value or function body. Its
registered correction is measured in every dependent unit and must improve the
named function without lowering another score; the whole-build gate follows.

Whole-unit source candidates may reorder complete function definitions or edit
file-scope wide-string pointer tables. A static-table candidate must improve
inferred object placement, preserve the unit's function scores, and pass the
oracle when the named function remains inexact.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Parser

from concord import oracle, unchanged_source
from concord.harvest import Evaluation, Harvest, function_key
from concord.search import net_gain, other_scores, worse_elsewhere
from concord.statics import data_layout_improves
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


@dataclass(frozen=True)
class Replacement:
    """A required number of replacements within a function body."""

    old: bytes
    new: bytes
    count: int


@dataclass(frozen=True)
class BodyEdit:
    """Related replacements that must apply together inside one body."""

    name: str
    replacements: tuple[Replacement, ...]


@dataclass(frozen=True)
class HeaderFunctionEdit:
    """An atomic correction to a function defined in a shared header.

    A complete declarator anchors an inline overload when symbol names a
    caller to score rather than the function being rewritten.
    """

    name: str
    header: str
    replacements: tuple[Replacement, ...]
    declarator: bytes | None = None


AUTHORED: dict[tuple[str, str], list[Edit | BodyEdit]] = {
    (
        "HarvestFull/harvest/entity/CDropshipEntity.cpp",
        "_ZN7harvest6entity15CDropshipEntity11updateLogicEf",
    ): [
        Edit(
            "approach-alignment-result",
            b'''        if ((distance > 424.413147f || direction.X * forward.X + direction.Y * forward.Y > 0) &&
            turnTowardsTarget(frameDelta))''',
            b'''        bool aligned = false;
        if (distance > 424.413147f || direction.X * forward.X + direction.Y * forward.Y > 0)
            aligned = turnTowardsTarget(frameDelta);
        if (aligned)''',
        ),
        BodyEdit(
            "gun-sine-before-start",
            (
                Replacement(
                    b"                    ox::core::CVector3d<float> start = Position",
                    b"                    double sn = sin((double)angle);\n                    ox::core::CVector3d<float> start = Position",
                    1,
                ),
                Replacement(b"sin((double)angle) * radius, 0, 0", b"sn * radius, 0, 0", 1),
            ),
        ),
        BodyEdit(
            "missile-y-before-x",
            (
                Replacement(
                    b"                    float x1 = missileX + sideX;\n                    float y1 = missileY + sideY;",
                    b"                    float y1 = missileY + sideY;\n                    float x1 = missileX + sideX;",
                    1,
                ),
                Replacement(
                    b"                    float x2 = missileX - sideX;\n                    float y2 = missileY - sideY;",
                    b"                    float y2 = missileY - sideY;\n                    float x2 = missileX - sideX;",
                    1,
                ),
            ),
        ),
        Edit(
            "landing-pitch-below-fifteen",
            b'''        if (Position.Z < 15.0f)
            SpriteIndex = 73;
        SoundPitch = ox::core::max_(SoundPitch + -.05f * frameDelta, .75f);''',
            b'''        if (Position.Z < 15.0f)
        {
            SpriteIndex = 73;
            SoundPitch = ox::core::max_(SoundPitch + -.05f * frameDelta, .75f);
        }''',
        ),
    ],
    # GCC treats a switch on the two-value enum as exhaustive. The target
    # checks both values and skips the vertex copy for other values.
    (
        "daisy/scene/CShadowVolumeSceneNode.cpp",
        "_ZN5daisy5scene22CShadowVolumeSceneNode19setMeshToRenderFromEPN2ox5scene5IMeshE",
    ): [
        Edit(
            "vertex-type-int-switch",
            b"switch(b->getVertexType())",
            b"switch((int)b->getVertexType())",
        ),
    ],
    # The target's A1R5G5B5 path branches on the signed 16-bit pixel: alpha is
    # either absent or 0xff, while the RGB fields use the same shifts in both
    # arms. This form comes from the target disassembly rather than Irrlicht's
    # branchless conversion helper.
    (
        "daisy/video/Null/CImage.cpp",
        "_ZN5daisy5video6CImage8getPixelEii",
    ): [
        Edit(
            "a1r5g5b5-alpha-branch",
            b"\t\treturn A1R5G5B5toA8R8G8B8(((short*)Data)[y*Size.Width + x]);",
            b"\t\t{\n"
            b"\t\t\tshort color = ((short*)Data)[y*Size.Width + x];\n"
            b"\t\t\tif (color < 0)\n"
            b"\t\t\t\treturn SColor(0xff000000 |\n"
            b"\t\t\t\t\t(color << 9 & 0xf80000) |\n"
            b"\t\t\t\t\t(color << 6 & 0xf800) |\n"
            b"\t\t\t\t\t(color << 3 & 0xff));\n"
            b"\t\t\treturn SColor(\n"
            b"\t\t\t\t(color << 9 & 0xf80000) |\n"
            b"\t\t\t\t(color << 6 & 0xf800) |\n"
            b"\t\t\t\t(color << 3 & 0xff));\n"
            b"\t\t}",
        ),
    ],
    # Initializing alpha before the flags changes the tower's PHI copies and
    # block layout without changing its calls or constants. This is authored
    # game code; no Irrlicht ancestor defines the tower.
    (
        "HarvestFull/harvest/entity/CDefenseTowerEntity.cpp",
        "_ZN7harvest6entity19CDefenseTowerEntity11updateLogicEf",
    ): [
        Edit(
            "alpha-before-flags",
            b"    bool showLaser = false;\n    bool noHitParticle = false;\n    int alpha = 162;",
            b"    int alpha = 162;\n    bool showLaser = false;\n    bool noHitParticle = false;",
        ),
        Edit(
            "clamp-alpha-before-color",
            b"        Beam.Color = ox::video::SColor(ox::core::min_(laserAlpha, 255), 255, 255, 255);",
            b"        if (laserAlpha >= 255)\n            laserAlpha = 255;\n        Beam.Color = ox::video::SColor(laserAlpha, 255, 255, 255);",
        ),
        Edit(
            "firing-sprites-first",
            b'''    if (!Shooting)
    {
        if (SpriteIndex >= 72)
            SpriteIndex -= 72;
    }
    else if (SpriteIndex < 72)
        SpriteIndex += 72;''',
            b'''    if (Shooting)
    {
        if (SpriteIndex < 72)
            SpriteIndex += 72;
    }
    else if (SpriteIndex >= 72)
        SpriteIndex -= 72;''',
        ),
    ],
    # drawIndexedTriangleList's vertex colour packs to A1R5G5B5 with a bool
    # alpha test shifted left 15, not the free function's `?: 0x8000` -- the
    # target shows `test 0xff000000; setne al; shl eax,0xf` where ours folds the
    # ternary into `cmp eax,1; sbb; not; and dx,0x8000`, and its blue-channel
    # `sar` marks the raw signed `color` beside the folded `shr` of the nested
    # `>>16`/`>>8` shifts (Irrlicht 0.7's RGB16 shape). Irrlicht's member drops
    # alpha; daisy kept it, and the member lives on ox::video::SColor, whose
    # declaration count the other units cannot afford, so the spelling goes in
    # the function bodies here.
    (
        "daisy/video/Software/CVideoSoftware.cpp",
        "_ZN5daisy5video14CVideoSoftware23drawIndexedTriangleListEPKN2ox5video9S3DVertexEiPKti",
    ): [
        Edit(
            "a1r5g5b5-alpha-uint",
            b"\t\ttp->Color = ox::video::toA1R5G5B5(currentVertex->Color.color);",
            b"\t\tunsigned int alpha = (currentVertex->Color.color & 0xff000000) != 0;\n"
            b"\t\ttp->Color = (alpha << 15)\n"
            b"\t\t\t| (((currentVertex->Color.color >> 19) & 0x1f) << 10)\n"
            b"\t\t\t| (((currentVertex->Color.color >> 11) & 0x1f) << 5)\n"
            b"\t\t\t| ((currentVertex->Color.color >> 3) & 0x1f);",
        ),
        Edit(
            "a1r5g5b5-alpha-unsigned",
            b"\t\ttp->Color = ox::video::toA1R5G5B5(currentVertex->Color.color);",
            b"\t\tunsigned int c = currentVertex->Color.color;\n"
            b"\t\tunsigned int alpha = (c & 0xff000000) != 0;\n"
            b"\t\ttp->Color = (alpha << 15) | ((c >> 9) & 0x7c00) | ((c >> 6) & 0x3e0) | (((int)c >> 3) & 0x1f);",
        ),
        Edit(
            "a1r5g5b5-alpha-uint-temps",
            b"\t\ttp->Color = ox::video::toA1R5G5B5(currentVertex->Color.color);",
            b"\t\tunsigned int alpha = (currentVertex->Color.color & 0xff000000) != 0;\n"
            b"\t\tint r = currentVertex->Color.color >> 16;\n"
            b"\t\tint g = currentVertex->Color.color >> 8;\n"
            b"\t\tint b = currentVertex->Color.color;\n"
            b"\t\ttp->Color = (alpha << 15)\n"
            b"\t\t\t| (((r >> 3) & 0x1f) << 10)\n"
            b"\t\t\t| (((g >> 3) & 0x1f) << 5)\n"
            b"\t\t\t| ((b >> 3) & 0x1f);",
        ),
    ],
    (
        "daisy/video/Software/CVideoSoftware.cpp",
        "_ZN5daisy5video14CVideoSoftware23drawIndexedTriangleListEPKN2ox5video17S3DVertex2TCoordsEiPKti",
    ): [
        Edit(
            "a1r5g5b5-alpha-uint",
            b"\t\ttp->Color = ox::video::toA1R5G5B5(currentVertex->Color.color);",
            b"\t\tunsigned int alpha = (currentVertex->Color.color & 0xff000000) != 0;\n"
            b"\t\ttp->Color = (alpha << 15)\n"
            b"\t\t\t| (((currentVertex->Color.color >> 19) & 0x1f) << 10)\n"
            b"\t\t\t| (((currentVertex->Color.color >> 11) & 0x1f) << 5)\n"
            b"\t\t\t| ((currentVertex->Color.color >> 3) & 0x1f);",
        ),
        Edit(
            "a1r5g5b5-alpha-unsigned",
            b"\t\ttp->Color = ox::video::toA1R5G5B5(currentVertex->Color.color);",
            b"\t\tunsigned int c = currentVertex->Color.color;\n"
            b"\t\tunsigned int alpha = (c & 0xff000000) != 0;\n"
            b"\t\ttp->Color = (alpha << 15) | ((c >> 9) & 0x7c00) | ((c >> 6) & 0x3e0) | (((int)c >> 3) & 0x1f);",
        ),
        Edit(
            "a1r5g5b5-alpha-uint-temps",
            b"\t\ttp->Color = ox::video::toA1R5G5B5(currentVertex->Color.color);",
            b"\t\tunsigned int alpha = (currentVertex->Color.color & 0xff000000) != 0;\n"
            b"\t\tint r = currentVertex->Color.color >> 16;\n"
            b"\t\tint g = currentVertex->Color.color >> 8;\n"
            b"\t\tint b = currentVertex->Color.color;\n"
            b"\t\ttp->Color = (alpha << 15)\n"
            b"\t\t\t| (((r >> 3) & 0x1f) << 10)\n"
            b"\t\t\t| (((g >> 3) & 0x1f) << 5)\n"
            b"\t\t\t| ((b >> 3) & 0x1f);",
        ),
    ],
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
    # The target passes L"" as the last argument of addStaticText at secondInit's
    # four status labels. That argument is the line prefix CGUIStaticText keeps
    # in a CString, which reads the NULL the source passed and L"" as the same
    # empty string, so only the passed pointer differs. The target stores L"" in
    # the call's eighth argument ([rsp+8]) where the source stored 0. The file's
    # other addStaticText calls already pass L"".
    (
        "HarvestFull/harvest/states/CPlayState.cpp",
        "_ZN7harvest6states10CPlayState10secondInitEv",
    ): [
        Edit(
            "info-text-style",
            b'InfoText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, 0);',
            b'InfoText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, L"");',
        ),
        Edit(
            "selected-name-style",
            b'SelectedNameText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, 0);',
            b'SelectedNameText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, L"");',
        ),
        Edit(
            "operator-text-style",
            b'OperatorText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, 0);',
            b'OperatorText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, L"");',
        ),
        Edit(
            "mini-stat-style",
            b'MiniStatText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, 0);',
            b'MiniStatText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, L"");',
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

# Daisy's quad batch is absent from Irrlicht 0.7. The target loads the batch
# index once and retains it across all four vertex assignments.
_QUAD_INDEX = BodyEdit("quad-vertex-index", (
    Replacement(b"Vertices2D[Current2DQuadCount * 4 +", b"Vertices2D[index +", 4),
    Replacement(b"    Vertices2D[index + 0]",
                b"    const int index = Current2DQuadCount * 4;\n    Vertices2D[index + 0]", 1),
))
for _draw_symbol in (
    "_ZN5daisy5video12CVideoOpenGL11draw2DImageEPN2ox5video8ITextureERKNS2_4core11CPosition2dIiEERKNS6_5CRectIiEEPSD_NS3_6SColorEb",
    "_ZN5daisy5video12CVideoOpenGL11draw2DImageEPN2ox5video8ITextureERKNS2_4core11CPosition2dIiEERKNS6_5CRectIiEEPSD_PNS3_6SColorEb",
    "_ZN5daisy5video12CVideoOpenGL11draw2DImageEPN2ox5video8ITextureERKNS2_4core11CPosition2dIiEESA_SA_SA_RKNS6_5CRectIiEEPNS3_6SColorEb",
    "_ZN5daisy5video12CVideoOpenGL11draw2DImageEPN2ox5video8ITextureERKNS2_4core11CPosition2dIfEESA_SA_SA_RKNS6_5CRectIiEEPKNS3_11SColorArrayEb",
):
    AUTHORED[("daisy/video/OpenGL/CVideoOpenGL.cpp", _draw_symbol)] = [_QUAD_INDEX]

# These daisy methods have no corresponding Irrlicht 0.7 bodies. The target
# reloads the viewport after external calls and subtracts '0' from the minor
# version before either version branch.
AUTHORED[("daisy/video/OpenGL/CVideoOpenGL.cpp", "_ZN5daisy5video12CVideoOpenGL18saveJpegScreenshotEPKcS3_")] = [
    BodyEdit("live-viewport-dimensions", (
        Replacement(b"const int width = viewport[2];", b"const int& width = viewport[2];", 1),
        Replacement(b"const int height = viewport[3];", b"const int& height = viewport[3];", 1),
        Replacement(b"    const int pitch = width * 3;\n", b"", 1),
        Replacement(b"new unsigned char[pitch * height]", b"new unsigned char[width * height * 3]", 1),
        Replacement(b"memcpy(flipped + (height - 1 - y) * pitch, pixels + y * pitch, pitch);",
                    b"memcpy(flipped + (height - 1 - y) * (width * 3), pixels + y * (width * 3), width * 3);", 1),
    )),
]
AUTHORED[("daisy/video/OpenGL/CVideoOpenGL.cpp", "_ZN5daisy5video12CVideoOpenGL14loadExtensionsEv")] = [
    Edit("eager-minor-version", b"    if (major > 1 || (parts[0][2] - '0' > 1 && major == 1))",
         b"    int minor = parts[0][2] - '0';\n    if (major > 1 || (minor > 1 && major == 1))"),
    Edit("glx-version-outputs", b"        int major = 0;\n        int minor = 0;",
         b"        int major;\n        int minor;"),
]
AUTHORED[("daisy/video/OpenGL/CVideoOpenGL.cpp", "_ZN5daisy5video12CVideoOpenGL17switch2dRenderingEPN2ox5video8ITextureEbbi")] = [
    Edit("quad-bound-shift", b"i < Current2DQuadCount * 4", b"i < (Current2DQuadCount << 2)"),
]
AUTHORED[("daisy/video/OpenGL/CVideoOpenGL.cpp", "_ZN5daisy5video12CVideoOpenGL17drawScaled2DImageEPN2ox5video8ITextureERKNS2_4core11CPosition2dIfEERKNS6_5CRectIiEEfNS3_6SColorEb")] = [
    BodyEdit("height-after-upper-corners", (
        Replacement(b"    float height = sourceRect.getHeight() * scale;\n", b"", 1),
        Replacement(b"    ox::core::CPosition2d<float> lowerLeft",
                    b"    float height = sourceRect.getHeight() * scale;\n    ox::core::CPosition2d<float> lowerLeft", 1),
    )),
]


HEADER_AUTHORED: dict[tuple[str, str], ConstantEdit] = {
    # NVIDIA's Cg header defines the program parameter namespace as 4109:
    # https://github.com/NVIDIAGameWorks/PhysX/blob/4.1/externals/cg-linux/include/Cg/cg.h
    (
        "daisy/video/Null/CCGMaterialRenderer.cpp",
        "_ZN5daisy5video19CCGMaterialRenderer14printVariablesEv",
    ): ConstantEdit("cg-program-namespace", "daisy/video/CgApi.h", "CG_PROGRAM", b"4125", b"4109"),
}


_RIVER_FALLBACK = HeaderFunctionEdit(
    "river-tab-fallback", "ox/gui/IGUILayoutInline.h", (
        Replacement(
            b'''        if (!flags && !sortHidden)
        {
            rows[row].push_back(*it);
            continue;
        }
        if (!flags)
            flags = "";''',
            b'''        if (!flags)
        {
            if (!sortHidden)
            {
                rows[row].push_back(*it);
                continue;
            }
            flags = "tab";
        }''', 1,
        ),
        Replacement(
            b'''                if (!flags && !sortHidden)
                    continue;
                if (!flags)
                    flags = "";''',
            b'''                if (!flags)
                {
                    if (!sortHidden)
                        continue;
                    flags = "tab";
                }''', 2,
        ),
    ),
)

_FLOW_CURSOR = HeaderFunctionEdit(
    "flow-cursor-and-extents", "ox/gui/IGUILayoutInline.h", (
        Replacement(b"int left = x - rect.getWidth();", b"x -= rect.getWidth();", 1),
        Replacement(b"if (spacingX > left && !first)", b"if (spacingX > x && !first)", 1),
        Replacement(b"left = width - rect.getWidth();", b"x = width - rect.getWidth();", 1),
        Replacement(b"placed(left, y, left + rect.getWidth(), rect.getHeight() + y)",
                    b"placed(x, y, x + rect.getWidth(), rect.getHeight() + y)", 1),
        Replacement(b"            (*it)->setRelativePosition(placed);\n            x = left - spacingX;",
                    b"            x -= spacingX;\n            (*it)->setRelativePosition(placed);", 1),
        Replacement(b"            if (width - x > maxWidth)\n                maxWidth = width - x;",
                    b"            maxWidth = width - x < maxWidth ? maxWidth : width - x;", 1),
        Replacement(b"y + rowHeight + spacingY - area.getHeight()",
                    b"y + rowHeight + (spacingY + area.UpperLeftCorner.Y - area.LowerRightCorner.Y)", 1),
        Replacement(b"maxWidth - area.getWidth()",
                    b"maxWidth + area.UpperLeftCorner.X - area.LowerRightCorner.X", 1),
    ),
)

# The target substitutes "tab" for absent layout flags at all three sites.
HEADER_FUNCTION_AUTHORED = {
    (f"daisy/gui/{name}.cpp", "_ZN2ox3gui10IGUILayout9sortRiverEbiib"): _RIVER_FALLBACK
    for name in ("CGUIClickArea", "CGUIEnvironment", "CGUIModalScreen", "CGUITabControl", "CGUIWindow")
}
HEADER_FUNCTION_AUTHORED.update({
    (f"daisy/gui/{name}.cpp", "_ZN2ox3gui10IGUILayout8sortFlowEiibb"): _FLOW_CURSOR
    for name in ("CGUIClickArea", "CGUIEnvironment", "CGUIModalScreen", "CGUITabControl", "CGUIWindow")
})

# Several spellings of one header operation can be screened with causal-trial.
# author requires an explicit name before measuring and writing a family member.
HEADER_FUNCTION_VARIANTS: dict[tuple[str, str], list[HeaderFunctionEdit]] = {}


def header_edits(unit: str, symbol: str, variant: str | None = None) -> list[HeaderFunctionEdit]:
    from concord.cstring_spellings import registered

    edits = HEADER_FUNCTION_VARIANTS.get((unit, symbol), registered().get((unit, symbol), []))
    if edit := HEADER_FUNCTION_AUTHORED.get((unit, symbol)):
        edits = [edit, *edits]
    return [edit for edit in edits if variant is None or edit.name == variant]


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


def header_function_candidate(source: bytes, symbol: str, edit: HeaderFunctionEdit) -> bytes | None:
    """Apply every replacement inside the named function or make no change."""
    if edit.declarator is None:
        scope = find_function(source, symbol)
    else:
        scopes = [node for node in _descendants(Parser(CPP).parse(source).root_node, "function_definition")
                  if node.child_by_field_name("declarator").text == edit.declarator]
        if len(scopes) != 1:
            return None
        scope = scopes[0]
    body = source[scope.start_byte:scope.end_byte]
    for replacement in edit.replacements:
        if body.count(replacement.old) != replacement.count:
            return None
        body = body.replace(replacement.old, replacement.new)
    return source[:scope.start_byte] + body + source[scope.end_byte:]


def _body_candidate(body: bytes, edit: Edit | BodyEdit) -> bytes | None:
    replacements = edit.replacements if isinstance(edit, BodyEdit) else (Replacement(edit.old, edit.new, 1),)
    for replacement in replacements:
        if body.count(replacement.old) != replacement.count:
            return None
        body = body.replace(replacement.old, replacement.new)
    return body


def candidates(source: bytes, symbol: str, edits: list[Edit | BodyEdit]) -> dict[str, bytes]:
    """One candidate source per edit whose old text appears exactly once inside
    the named function's body, plus one candidate applying every matching edit
    together when two or more do: a function can need several disjoint edits to
    reach exact, and single-edit candidates cannot combine."""
    scope = find_function(source, symbol)
    body = source[scope.start_byte : scope.end_byte]
    found = {}
    combined, names = body, []
    for edit in edits:
        candidate = _body_candidate(body, edit)
        if candidate is not None:
            found[edit.name] = source[: scope.start_byte] + candidate + source[scope.end_byte :]
        candidate = _body_candidate(combined, edit)
        if candidate is not None:
            combined = candidate
            names.append(edit.name)
    if len(names) > 1:
        found["+".join(names)] = source[: scope.start_byte] + combined + source[scope.end_byte :]
    return found


def _source_candidate(source: bytes, candidate: bytes) -> bool:
    """Allow definition reordering and edits to file-scope wide-string pointer tables."""
    def scope(node, text: bytes) -> tuple[bytes, ...] | None:
        parent = node.parent
        namespaces = []
        while parent is not None:
            if parent.type in {"class_specifier", "function_definition", "lambda_expression"}:
                return None
            if parent.type == "namespace_definition":
                name = parent.child_by_field_name("name")
                namespaces.append(text[name.start_byte:name.end_byte] if name else b"")
            parent = parent.parent
        return tuple(reversed(namespaces))

    def definitions(text: bytes):
        root = Parser(CPP).parse(text).root_node
        functions = []
        tables = {}
        for node in _descendants(root, "function_definition"):
            namespaces = scope(node, text)
            if namespaces is None:
                continue
            declarator = node.child_by_field_name("declarator")
            name = text[declarator.start_byte:declarator.end_byte].split(b"(", 1)[0] if declarator else b""
            functions.append((node.start_byte, node.end_byte, (*namespaces, name.rpartition(b"::")[0])))

        for node in _descendants(root, "declaration"):
            namespaces = scope(node, text)
            if namespaces is None:
                continue
            raw = text[node.start_byte:node.end_byte]
            if not re.match(rb"static\s+const\s+wchar_t\s*\*\s*const\s+", raw):
                continue
            type_node = node.child_by_field_name("type")
            if type_node is None or text[type_node.start_byte:type_node.end_byte] != b"wchar_t":
                continue
            declarators = [child for child in node.named_children if child.type == "init_declarator"]
            if len(declarators) != 1:
                continue
            declarator = declarators[0].child_by_field_name("declarator")
            initializer = next(
                (child for child in declarators[0].named_children if child.type == "initializer_list"), None
            )
            if declarator is None or initializer is None:
                continue
            name = re.search(rb"([A-Za-z_]\w*)\s*\[\s*\]\s*$", text[declarator.start_byte:declarator.end_byte])
            entries = initializer.named_children
            if (name is None or not entries
                    or any(entry.type != "string_literal" or not text[entry.start_byte:entry.end_byte].startswith(b"L\"")
                           for entry in entries)):
                continue
            key = (namespaces, name.group(1))
            if key in tables:
                return functions, None
            signature = (
                raw[:initializer.start_byte - node.start_byte]
                + raw[initializer.end_byte - node.start_byte:]
            )
            tables[key] = (node.start_byte, node.end_byte, signature)
        return functions, tables

    original_functions, original_tables = definitions(source)
    candidate_functions, candidate_tables = definitions(candidate)
    if (not original_functions and not original_tables) or original_tables is None or candidate_tables is None:
        return False
    if Counter((scope, source[start:end]) for start, end, scope in original_functions) != Counter(
        (scope, candidate[start:end]) for start, end, scope in candidate_functions
    ):
        return False
    if (original_tables.keys() != candidate_tables.keys()
            or any(original_tables[key][2] != candidate_tables[key][2] for key in original_tables)):
        return False

    def outside(text: bytes, ranges: list[tuple[int, int, tuple[bytes, ...]]]) -> bytes:
        previous, chunks = 0, []
        for start, end, _ in sorted(ranges, key=lambda item: item[0]):
            chunks.append(text[previous:start])
            previous = end
        chunks.append(text[previous:])
        return b"".join(chunks)

    original_ranges = original_functions + [
        (start, end, key[0]) for key, (start, end, _) in original_tables.items()
    ]
    candidate_ranges = candidate_functions + [
        (start, end, key[0]) for key, (start, end, _) in candidate_tables.items()
    ]
    return outside(source, original_ranges) == outside(candidate, candidate_ranges)


def keep(before: Evaluation, after: Evaluation, symbol: str) -> bool:
    """Whether the authored candidate may replace the unit's source: it compiles,
    the named function is exact or scores higher than before, every previously
    exact function stays exact, and no other function of the unit scores lower or
    the unit gains overall, by size-weighted score (Goal 2's net rule).
    An inexact candidate also needs the oracle's agreement; apply() runs it."""
    if after.error:
        return False
    verdict = after.verdict(symbol)
    if verdict is None or (not verdict.row["exact"] and (after.fuzzy.get(symbol) or 0.0) <= (before.fuzzy.get(symbol) or 0.0)):
        return False
    if not before.exact_functions() <= after.exact_functions():
        return False
    return not worse_elsewhere(other_scores(before, symbol), after, symbol) or net_gain(before, after) > 0


# Sections whose bytes and relocations are the compiled program; debug sections,
# which carry local names, are left out.
CODE_SECTIONS = (".text", ".rodata", ".data", ".gcc_except_table")


def _code(path: Path) -> dict[str, object]:
    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection

    with open(path, "rb") as stream:
        elf = ELFFile(stream)
        code: dict[str, object] = {}
        for section in elf.iter_sections():
            if isinstance(section, RelocationSection):
                if section.name.removeprefix(".rela").startswith(CODE_SECTIONS):
                    table = elf.get_section(section["sh_link"])
                    rows = []
                    for reloc in section.iter_relocations():
                        symbol = table.get_symbol(reloc["r_info_sym"])
                        name = symbol.name or elf.get_section(symbol["st_shndx"]).name
                        rows.append((reloc["r_offset"], reloc["r_info_type"], name, reloc["r_addend"]))
                    code[section.name] = tuple(rows)
            elif section.name.startswith(CODE_SECTIONS):
                code[section.name] = section.data()
        return code


def same_code(before: Evaluation, after: Evaluation) -> bool:
    """Whether two compiles of a unit emit identical code, data and relocations, as a
    change of local names or layout alone does: the same bytes, scores and behavior."""
    return bool(not after.error and before.object and after.object
                and _code(before.object) == _code(after.object))


def _preserves(before: Evaluation, after: Evaluation) -> bool:
    return (not after.error and before.exact_functions() <= after.exact_functions()
            and all(name in after.fuzzy and after.fuzzy[name] >= score for name, score in before.fuzzy.items()))


def _matched_data(evaluation: Evaluation) -> set[int]:
    return {
        address + offset
        for section in evaluation.sections
        for row in section.get("data_ranges", [])
        for address in [int(row["address"], 0) if isinstance(row["address"], str) else int(row["address"])]
        for offset in range(int(row["size"]))
    }



def _preserves_with_exact_copies(before: Evaluation, after: Evaluation, other_exact_addresses: set[str]) -> bool:
    """Allow a unit to drop an exact duplicate already emitted by another unchanged unit."""
    if after.error:
        return False
    after_exact = after.exact_functions()
    for key in before.exact_functions() - after_exact:
        duplicate = next((row for section in before.sections for row in section.get("functions", [])
                          if row["exact"] and function_key(row) == key), None)
        if duplicate is None or duplicate.get("address") not in other_exact_addresses:
            return False
    for name, score in before.fuzzy.items():
        if name in after.fuzzy and after.fuzzy[name] >= score:
            continue
        original = before.verdict(name)
        if original is None or original.row.get("address") not in other_exact_addresses:
            return False
    return True


def keep_improvement(before: Evaluation, after: Evaluation, symbol: str, *, known: bool = False,
                     other_exact_addresses: set[str] | None = None) -> bool:
    """Accept an improved function or a new exact known symbol without unit losses."""
    old, new = before.verdict(symbol), after.verdict(symbol)
    old_score, new_score = before.fuzzy.get(symbol), after.fuzzy.get(symbol)
    preserves = _preserves_with_exact_copies(before, after, other_exact_addresses or set())
    if old is None:
        return bool(known and new and new.row["exact"] and preserves)
    return bool(old and new and old_score is not None and new_score is not None
                and (new_score > old_score or (new.row["exact"] and not old.row["exact"]))
                and preserves)


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


def apply_header_function(harvest: Harvest, unit: str, symbol: str, edit: HeaderFunctionEdit, write: bool) -> dict:
    """Measure a header body in its dependents and check the selected copy."""
    path = harvest.root / "src" / edit.header
    source = path.read_bytes()
    candidate = header_function_candidate(source, symbol, edit)
    if candidate is None:
        raise ValueError(f"no authored function candidate applies to {symbol} in {edit.header}")
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
    evidence = None
    if accepted and not verdict.row["exact"]:
        if before.object is None or after.object is None:
            accepted = False
        else:
            accepted, evidence = oracle.gate_objects(harvest, unit, symbol, before.object, after.object)
            accepted = accepted and evidence is not None
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
                                   "before": before.fuzzy.get(symbol), "score": after.fuzzy.get(symbol),
                                   "kept": accepted, "oracle": evidence}},
        "chosen": edit.name if accepted else None,
        "applied": bool(accepted and write),
        "original": source,
        "source": candidate if accepted else None,
    }


def apply(harvest: Harvest, unit: str, symbol: str, write: bool, variant: str | None = None,
          candidate: bytes | None = None, source_candidate: bytes | None = None) -> dict:
    """Measure authored candidates and optionally write an accepted one. A
    candidate is kept when the named function is exact, or scores higher with
    the oracle's agreement, and no other function of the unit is worse."""
    unit = harvest.source(unit)
    if sum(value is not None for value in (candidate, source_candidate, variant)) > 1:
        raise ValueError('choose one candidate source or a registered variant')
    if candidate is None and source_candidate is None and (edits := header_edits(unit, symbol, variant)):
        if len(edits) != 1:
            raise ValueError("select one authored header spelling with --variant")
        return apply_header_function(harvest, unit, symbol, edits[0], write)
    if variant is not None and source_candidate is None:
        raise ValueError(f"no authored header variant {variant} for {unit} {symbol}")
    if candidate is None and source_candidate is None and (edit := HEADER_AUTHORED.get((unit, symbol))):
        return apply_header(harvest, unit, symbol, edit, write)
    path = harvest.root / "src" / unit
    source = path.read_bytes()
    if source_candidate is not None:
        if not _source_candidate(source, source_candidate):
            raise ValueError("a source candidate may only reorder complete function definitions or edit file-scope wide-string pointer tables")
        forms = {"causal-source-candidate": source_candidate} if source != source_candidate else {}
    elif candidate is None:
        forms = candidates(source, symbol, AUTHORED.get((unit, symbol), []))
    else:
        old_scope, new_scope = find_function(source, symbol), find_function(candidate, symbol)
        old_body, new_body = old_scope.child_by_field_name('body'), new_scope.child_by_field_name('body')
        if (source[:old_body.start_byte] != candidate[:new_body.start_byte]
                or source[old_body.end_byte:] != candidate[new_body.end_byte:]):
            raise ValueError('an authored candidate may change only the named function body')
        forms = {'causal-candidate': candidate} if source != candidate else {}
    if not forms:
        raise ValueError(f"no authored candidate applies to {symbol} in {unit}")
    evaluations = harvest.evaluate(unit, {"baseline": source, **forms})
    baseline = evaluations["baseline"]
    if baseline.error:
        raise RuntimeError(f"the unit's own source does not compile:\n{baseline.error}")
    verdicts = {}
    before_verdict = baseline.verdict(symbol)
    for name, row in evaluations.items():
        if name == "baseline":
            continue
        verdict = row.verdict(symbol)
        kept = keep(baseline, row, symbol)
        data_layout = None
        if source_candidate is not None and not kept and _preserves(baseline, row):
            data_layout = data_layout_improves(harvest, unit, baseline, row)
            kept = data_layout["improves"]
        evidence = None
        same = candidate is not None and not kept and same_code(baseline, row)
        if same:
            # A readability change: nothing compiled differs, so nothing needs the oracle.
            kept = True
        elif kept and not verdict.row["exact"]:
            kept, evidence = oracle.gate(harvest, unit, symbol, source, forms[name])
        verdicts[name] = {
            "error": row.error or None,
            "exact": bool(verdict and verdict.row["exact"]),
            "before": baseline.fuzzy.get(symbol),
            "score": row.fuzzy.get(symbol),
            "before_size": before_verdict.row.get("size") if before_verdict else None,
            "size": verdict.row.get("size") if verdict else None,
            "object": str(row.object) if row.object else None,
            "kept": kept,
            "data_layout": data_layout,
            "same_code": same,
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


def seed(harvest: Harvest, files: dict[str, bytes], units: dict[str, dict[str, int]],
         write: bool = False, claims: list[dict] | None = None) -> dict:
    """Measure an initial authored recovery of absent units and their headers.

    Like ancestor seeding, initial recovery has no previous source behavior to
    preserve. Every unit must add an exact function; existing dependents must
    retain all scores. Subsequent body changes use apply() and its oracle gate.
    Explicit names must refer to target function extents validated by the driver.
    The whole-build scores gate remains required after installation.
    """
    from concord.seed import _match, _register

    if not units or not units.keys() <= files.keys():
        raise ValueError("each new unit needs source")
    for name in files:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or path.suffix not in {".h", ".cpp"}:
            raise ValueError(f"invalid authored source path: {name}")
    for unit in units:
        if (harvest.root / "src" / unit).exists():
            raise ValueError(f"{unit} already exists; use a body candidate")
    originals = {name: (harvest.root / "src" / name).read_bytes()
                 for name in files if (harvest.root / "src" / name).exists()}
    dependents = sorted({unit for name in originals for unit in harvest.dependents("src/" + name)})
    overlay = {"src/" + name: body for name, body in files.items()}
    items = {f"new-{n}": (unit, overlay) for n, unit in enumerate(units)}
    for n, unit in enumerate(dependents):
        items[f"before-{n}"] = (unit, {})
        items[f"after-{n}"] = (unit, overlay)
    measured = harvest.evaluate_overlays(items, units=units, claims=claims)
    accepted = all(not row.error for row in measured.values())
    accepted = accepted and all(measured[f"new-{n}"].exact_functions() for n in range(len(units)))
    accepted = accepted and all(_preserves(measured[f"before-{n}"], measured[f"after-{n}"])
                                for n in range(len(dependents)))
    applied = False
    if accepted and write:
        for name, original in originals.items():
            if (harvest.root / "src" / name).read_bytes() != original:
                raise ValueError(f"{name} changed during measurement")
        for name, body in files.items():
            path = harvest.root / "src" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        for unit, placements in units.items():
            _register(harvest.root, unit, placements)
        if claims:
            import json
            import subprocess

            script = """import json,sys
from hv import symbols
rows=symbols.load(sys.argv[1])
known=symbols.by_name(rows)
for claim in json.load(sys.stdin):
    if claim['symbol'] not in known:
        rows.append(symbols.Symbol(claim['address'],claim['size'],claim['symbol'],claim['evidence']))
symbols.save(sys.argv[1],rows)
"""
            subprocess.run(["uv", "--no-config", "run", "--locked", "python", "-c", script, harvest.build],
                           cwd=harvest.root, input=json.dumps(claims), text=True, check=True, capture_output=True)
        for unit in units:
            _match(harvest.root, unit, learn=True)
        applied = True
    return {"accepted": bool(accepted), "applied": applied, "units": list(units), "dependents": dependents,
            "evaluations": {name: {"error": row.error, "exact": sorted(row.exact_functions()), "fuzzy": row.fuzzy,
                                   "object": str(row.object) if row.object else None}
                            for name, row in measured.items()}}


def overlay(harvest: Harvest, files: dict[str, bytes], write: bool = False) -> dict:
    """Measure authored source in every dependent unit under Goal 2.

    Each unit keeps its exact functions, except a copy another unchanged unit
    still emits exactly, and its matched data. A lower function score is allowed
    when that function is exact elsewhere or the unit's size-weighted score rises.
    A new function counts as a gain only when it is exact and named in symbols.tsv.
    Improved inexact functions require oracle evidence, or unchanged source for
    their own body and all transitive inlines in both compiles, before writing.
    """
    originals = {}
    units = set()
    for name in files:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or path.suffix not in {'.cpp', '.h'}:
            raise ValueError(f'invalid authored source path: {name}')
        originals[name] = (harvest.root / 'src' / name).read_bytes()
        units.update(harvest.dependents('src/' + name))
        if path.suffix == '.cpp':
            units.add(name)
    units = sorted(units)
    known_symbols = set()
    other_exact_addresses = set()
    if units:
        with (harvest.root / "config" / harvest.build / "symbols.tsv").open() as stream:
            known_symbols = {row["symbol"] for row in csv.DictReader(stream, delimiter="\t")}
        for report_path in harvest.reports.glob("*.json"):
            report = json.loads(report_path.read_text())
            if report.get("unit") in units:
                continue
            other_exact_addresses.update(
                row["address"]
                for section in report.get("sections", [])
                for row in section.get("functions", [])
                if row["exact"]
            )
    items = {}
    for n, unit in enumerate(units):
        items[f'before-{n}'] = (unit, {})
        items[f'after-{n}'] = (unit, {'src/' + name: body for name, body in files.items()})
    changed = unchanged_source.changed_functions(originals, files)
    measured = harvest.evaluate_overlays(items, inline_decisions=changed is not None)
    accepted = bool(units)
    gains = []
    for n, unit in enumerate(units):
        before, after = measured[f'before-{n}'], measured[f'after-{n}']
        if before.error or after.error:
            accepted = False
            continue
        if not _preserves_with_exact_copies(before, after, other_exact_addresses):
            lowered = [name for name, score in before.fuzzy.items()
                       if after.fuzzy.get(name, 0.0) < score
                       and (before.verdict(name) is None
                            or before.verdict(name).row.get("address") not in other_exact_addresses)]
            lost_exact = any(not (before.verdict(name) and before.verdict(name).row.get("address") in other_exact_addresses)
                             for name in {row["symbol"] for section in before.sections
                                          for row in section.get("functions", [])
                                          if row["exact"] and function_key(row) not in after.exact_functions()})
            if lost_exact or not lowered or net_gain(before, after) <= 0:
                accepted = False
                continue
        if not _matched_data(before) <= _matched_data(after):
            accepted = False
            continue
        for symbol in after.fuzzy:
            old_score = before.fuzzy.get(symbol)
            verdict = after.verdict(symbol)
            old_verdict = before.verdict(symbol)
            if verdict is None:
                continue
            if old_score is None:
                improved = bool(verdict.row['exact'] and symbol in known_symbols)
            else:
                improved = (after.fuzzy[symbol] > old_score
                            or bool(verdict.row['exact'] and old_verdict and not old_verdict.row['exact']))
            if not improved:
                continue
            evidence = None
            unchanged = None
            kept = True
            if not verdict.row['exact']:
                unchanged = unchanged_source.evidence(before.object, after.object, symbol, changed)
                if unchanged is None:
                    kept, evidence = oracle.gate_objects(harvest, unit, symbol, before.object, after.object)
                    kept = kept and evidence is not None
            accepted = accepted and kept
            if kept:
                gain = {'unit': unit, 'symbol': symbol, 'before': old_score,
                        'score': after.fuzzy[symbol], 'exact': verdict.row['exact']}
                gain.update({'unchanged_source': unchanged} if unchanged is not None else {'oracle': evidence})
                gains.append(gain)
    accepted = bool(accepted and gains)
    if accepted and write:
        for name, original in originals.items():
            if (harvest.root / 'src' / name).read_bytes() != original:
                raise ValueError(f'{name} changed during measurement')
        for name, body in files.items():
            (harvest.root / 'src' / name).write_bytes(body)
    return {'accepted': accepted, 'applied': bool(accepted and write), 'gains': gains, 'units': units,
            'errors': {name: row.error for name, row in measured.items() if row.error}}
