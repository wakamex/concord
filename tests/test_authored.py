"""Authored source edits are measured before writing."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from concord import authored
from concord.authored import Edit, candidates, keep
from concord.harvest import Evaluation
from concord.transforms import demangle

SOURCE = b"""int Board::pick(int a)
{
    x.flagA = false;
    x.flagB = false;
    return a;
}
int Board::skip()
{
    x.flagA = false;
    x.flagB = false;
    return 0;
}
"""
SYMBOL = "_ZN5Board4pickEi"  # Board::pick(int)


def evaluation(exact: dict[str, bool], fuzzy: dict[str, float] | None = None, error: str = "") -> Evaluation:
    return Evaluation(
        "c",
        "u",
        error=error,
        sections=[{"functions": [{"symbol": s, "exact": e} for s, e in exact.items()]}],
        fuzzy=fuzzy or {},
    )


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class Candidates(unittest.TestCase):
    def test_opengl_glx_version_outputs_have_no_initial_stores(self):
        symbol = "_ZN5daisy5video12CVideoOpenGL14loadExtensionsEv"
        source = b"""void CVideoOpenGL::loadExtensions()
{
    if (MultiTextureExtension)
    {
        int major = 0;
        int minor = 0;
        glXQueryVersion(display, &major, &minor);
    }
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(("daisy/video/OpenGL/CVideoOpenGL.cpp", symbol), []))
        self.assertIn("glx-version-outputs", forms)
        candidate = forms["glx-version-outputs"]
        self.assertIn(b"int major;\n        int minor;\n        glXQueryVersion", candidate)
        self.assertNotIn(b"= 0", candidate)

    def test_opengl_scaled_height_is_computed_after_the_upper_corners(self):
        symbol = "_ZN5daisy5video12CVideoOpenGL17drawScaled2DImageEPN2ox5video8ITextureERKNS2_4core11CPosition2dIfEERKNS6_5CRectIiEEfNS3_6SColorEb"
        source = b"""void CVideoOpenGL::drawScaled2DImage(ITexture* texture, const CPosition2d<float>& pos, const CRect<int>& sourceRect, float scale, SColor color, bool alpha)
{
    float height = sourceRect.getHeight() * scale;
    ox::core::CPosition2d<float> upperRight(pos.X + width, pos.Y);
    ox::core::CPosition2d<float> lowerLeft(pos.X, pos.Y + height);
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(("daisy/video/OpenGL/CVideoOpenGL.cpp", symbol), []))
        self.assertIn("height-after-upper-corners", forms)
        candidate = forms["height-after-upper-corners"]
        self.assertLess(candidate.index(b"upperRight("), candidate.index(b"float height"))
        self.assertLess(candidate.index(b"float height"), candidate.index(b"lowerLeft("))

    def test_opengl_batch_bound_retains_the_signed_shift(self):
        symbol = "_ZN5daisy5video12CVideoOpenGL17switch2dRenderingEPN2ox5video8ITextureEbbi"
        source = b"""void CVideoOpenGL::switch2dRendering(ITexture* texture, bool alpha, bool filter, int quads)
{
    for (int i = 0; i < Current2DQuadCount * 4; i += 4)
        draw(i);
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(("daisy/video/OpenGL/CVideoOpenGL.cpp", symbol), []))
        self.assertIn("quad-bound-shift", forms)
        self.assertIn(b"i < (Current2DQuadCount << 2)", forms["quad-bound-shift"])

    def test_opengl_screenshot_keeps_viewport_dimensions_live_across_calls(self):
        symbol = "_ZN5daisy5video12CVideoOpenGL18saveJpegScreenshotEPKcS3_"
        source = b"""bool CVideoOpenGL::saveJpegScreenshot(const char* directory, const char* name)
{
    const int width = viewport[2];
    const int height = viewport[3];
    const int pitch = width * 3;
    unsigned char* flipped = new unsigned char[pitch * height];
        memcpy(flipped + (height - 1 - y) * pitch, pixels + y * pitch, pitch);
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(("daisy/video/OpenGL/CVideoOpenGL.cpp", symbol), []))
        self.assertIn("live-viewport-dimensions", forms)
        candidate = forms["live-viewport-dimensions"]
        self.assertIn(b"const int& width = viewport[2]", candidate)
        self.assertNotIn(b"pitch", candidate)
        self.assertIn(b"new unsigned char[width * height * 3]", candidate)
        self.assertIn(b"memcpy(flipped + (height - 1 - y) * (width * 3), pixels + y * (width * 3), width * 3)", candidate)

    def test_opengl_parses_the_minor_version_before_branching(self):
        symbol = "_ZN5daisy5video12CVideoOpenGL14loadExtensionsEv"
        source = b"""void CVideoOpenGL::loadExtensions()
{
    int major = parts[0][0] - '0';
    if (major > 1 || (parts[0][2] - '0' > 1 && major == 1))
        report();
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(("daisy/video/OpenGL/CVideoOpenGL.cpp", symbol), []))
        self.assertIn("eager-minor-version", forms)
        self.assertIn(b"int minor = parts[0][2] - '0';\n    if (major > 1 || (minor > 1 && major == 1))", forms["eager-minor-version"])

    def test_opengl_draw_edits_cache_one_index_for_all_four_stores(self):
        unit = "daisy/video/OpenGL/CVideoOpenGL.cpp"
        edits = [(symbol, forms) for (path, symbol), forms in authored.AUTHORED.items()
                 if path == unit and "11draw2DImage" in symbol]
        self.assertEqual(len(edits), 4)
        for symbol, registered in edits:
            source = ("void " + demangle(symbol) + "\n{\n" + "".join(
                f"    Vertices2D[Current2DQuadCount * 4 + {n}] = vertex;\n" for n in range(4)
            ) + "}\n").encode()
            forms = candidates(source, symbol, registered)
            self.assertIn("quad-vertex-index", forms)
            candidate = forms["quad-vertex-index"]
            self.assertEqual(candidate.count(b"Current2DQuadCount * 4"), 1)
            self.assertEqual(candidate.count(b"Vertices2D[index +"), 4)
            self.assertEqual(candidates(candidate, symbol, registered), {})

    def test_atomic_body_edit_requires_every_anchor_and_stays_in_scope(self):
        edit = authored.BodyEdit("both", (
            authored.Replacement(b"x.flagA = false;", b"x.flagA = true;", 1),
            authored.Replacement(b"x.flagB = false;", b"x.flagB = true;", 1),
        ))
        forms = candidates(SOURCE, SYMBOL, [edit])
        self.assertIn("both", forms)
        self.assertEqual(forms["both"].count(b"= true;"), 2)
        self.assertEqual(forms["both"].split(b"int Board::skip()")[1], SOURCE.split(b"int Board::skip()")[1])
        missing = SOURCE.replace(b"x.flagB = false;", b"x.flagC = false;", 1)
        self.assertEqual(candidates(missing, SYMBOL, [edit]), {})
        duplicate = SOURCE.replace(b"x.flagA = false;", b"x.flagA = false; x.flagA = false;", 1)
        self.assertEqual(candidates(duplicate, SYMBOL, [edit]), {})

    def test_tower_alpha_precedes_the_laser_flags(self):
        symbol = "_ZN7harvest6entity19CDefenseTowerEntity11updateLogicEf"
        key = ("HarvestFull/harvest/entity/CDefenseTowerEntity.cpp", symbol)
        source = b"""int harvest::entity::CDefenseTowerEntity::updateLogic(float frameDelta)
{
    updateTowerLinks();
    bool showLaser = false;
    bool noHitParticle = false;
    int alpha = 162;
    return 0;
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(key, []))
        self.assertIn("alpha-before-flags", forms)
        self.assertIn(b"int alpha = 162;\n    bool showLaser = false;\n    bool noHitParticle = false;", forms["alpha-before-flags"])
        self.assertEqual(forms["alpha-before-flags"].count(b"int alpha = 162;"), 1)

    def test_tower_clamps_alpha_before_packing_color(self):
        symbol = "_ZN7harvest6entity19CDefenseTowerEntity11updateLogicEf"
        key = ("HarvestFull/harvest/entity/CDefenseTowerEntity.cpp", symbol)
        source = b"""int harvest::entity::CDefenseTowerEntity::updateLogic(float frameDelta)
{
        Beam.Color = ox::video::SColor(ox::core::min_(laserAlpha, 255), 255, 255, 255);
    return 0;
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(key, []))
        self.assertIn("clamp-alpha-before-color", forms)
        self.assertIn(b"if (laserAlpha >= 255)\n            laserAlpha = 255;\n        Beam.Color = ox::video::SColor(laserAlpha, 255, 255, 255);", forms["clamp-alpha-before-color"])

    def test_tower_checks_the_firing_sprite_bank_first(self):
        symbol = "_ZN7harvest6entity19CDefenseTowerEntity11updateLogicEf"
        key = ("HarvestFull/harvest/entity/CDefenseTowerEntity.cpp", symbol)
        source = b"""int harvest::entity::CDefenseTowerEntity::updateLogic(float frameDelta)
{
    if (!Shooting)
    {
        if (SpriteIndex >= 72)
            SpriteIndex -= 72;
    }
    else if (SpriteIndex < 72)
        SpriteIndex += 72;
    return 0;
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(key, []))
        self.assertIn("firing-sprites-first", forms)
        self.assertIn(b"if (Shooting)\n    {\n        if (SpriteIndex < 72)\n            SpriteIndex += 72;", forms["firing-sprites-first"])
        self.assertIn(b"else if (SpriteIndex >= 72)\n        SpriteIndex -= 72;", forms["firing-sprites-first"])

    def test_the_edit_applies_inside_the_named_function_only(self):
        edits = [Edit("flag", b"x.flagA = false;\n    x.flagB = false;", b"x.flagA = false;\n    x.flagC = false;\n    x.flagB = false;")]
        found = candidates(SOURCE, SYMBOL, edits)
        self.assertEqual(len(found), 1)
        self.assertIn(b"x.flagC = false;\n    x.flagB = false;\n    return a;", found["flag"])
        self.assertNotIn(b"x.flagC", found["flag"].split(b"int Board::skip()")[1])

    def test_an_edit_needs_exactly_one_anchor_in_the_body(self):
        edits = [
            Edit("once", b"x.flagA = false;\n    x.flagB = false;", b"x.flagA = false;\n    x.flagC = false;\n    x.flagB = false;"),
            Edit("gone", b"return 9;", b"return 8;"),
            Edit("ambiguous", b"false;", b"true;"),
        ]
        self.assertEqual(list(candidates(SOURCE, SYMBOL, edits)), ["once"])

    def test_every_matching_edit_also_applies_together(self):
        edits = [
            Edit("one", b"x.flagA = false;", b"x.flagA = true;"),
            Edit("two", b"x.flagB = false;", b"x.flagB = true;"),
            Edit("gone", b"return 9;", b"return 8;"),
        ]
        found = candidates(SOURCE, SYMBOL, edits)
        self.assertIn("one+two", found)
        self.assertIn(b"x.flagA = true;\n    x.flagB = true;", found["one+two"])
        self.assertNotIn(b"x.flagA = true", found["two"])
        self.assertNotIn("one+two+gone", found)

    def test_software_vertex_colour_packs_alpha_as_bool(self):
        unit = "daisy/video/Software/CVideoSoftware.cpp"
        source = (
            b"void CVideoSoftware::drawIndexedTriangleList(const S3DVertex* vertices, int vertexCount, const unsigned short* indexList, int triangleCount)\n"
            b"{\n"
            b"\t\ttp->Color = ox::video::toA1R5G5B5(currentVertex->Color.color);\n"
            b"}\n"
            b"void CVideoSoftware::drawIndexedTriangleList(const S3DVertex2TCoords* vertices, int vertexCount, const unsigned short* indexList, int triangleCount)\n"
            b"{\n"
            b"\t\ttp->Color = ox::video::toA1R5G5B5(currentVertex->Color.color);\n"
            b"}\n"
        )
        for symbol, vertex in (
            ("_ZN5daisy5video14CVideoSoftware23drawIndexedTriangleListEPKN2ox5video9S3DVertexEiPKti", b"S3DVertex*"),
            ("_ZN5daisy5video14CVideoSoftware23drawIndexedTriangleListEPKN2ox5video17S3DVertex2TCoordsEiPKti", b"S3DVertex2TCoords*"),
        ):
            edits = authored.AUTHORED[(unit, symbol)]
            found = candidates(source, symbol, edits)
            self.assertEqual([edit.name for edit in edits], [
                "a1r5g5b5-alpha-uint", "a1r5g5b5-alpha-unsigned", "a1r5g5b5-alpha-uint-temps"])
            body = found["a1r5g5b5-alpha-uint"].split(vertex)[1]
            self.assertIn(b"unsigned int alpha = (currentVertex->Color.color & 0xff000000) != 0;\n", body)
            self.assertIn(b"(alpha << 15)", body)
            self.assertEqual(found["a1r5g5b5-alpha-uint"].count(b"ox::video::toA1R5G5B5"), 1)

    def test_cimage_a1r5g5b5_conversion_branches_on_the_alpha_bit(self):
        unit = "daisy/video/Null/CImage.cpp"
        symbol = "_ZN5daisy5video6CImage8getPixelEii"
        key = (unit, symbol)
        source = b"""SColor CImage::getPixel(int x, int y)
{
    switch(Format)
    {
    case ECF_A1R5G5B5:
\t\treturn A1R5G5B5toA8R8G8B8(((short*)Data)[y*Size.Width + x]);
    case ECF_R5G6B5:
        return R5G6B5toA8R8G8B8(((short*)Data)[y*Size.Width + x]);
    }
    return SColor(0);
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(key, []))
        self.assertIn("a1r5g5b5-alpha-branch", forms)
        body = forms["a1r5g5b5-alpha-branch"]
        self.assertIn(b"short color = ((short*)Data)[y*Size.Width + x];", body)
        self.assertIn(b"if (color < 0)", body)
        self.assertIn(b"SColor(0xff000000 |", body)
        self.assertIn(b"case ECF_R5G6B5:\n        return R5G6B5toA8R8G8B8", body)

    def test_status_texts_take_the_empty_wide_string_as_style(self):
        symbol = "_ZN7harvest6states10CPlayState10secondInitEv"
        key = ("HarvestFull/harvest/states/CPlayState.cpp", symbol)
        source = b"""int harvest::states::CPlayState::secondInit()
{
        InfoText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, 0);
        SelectedNameText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, 0);
        OperatorText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, 0);
        MiniStatText = GUIEnvironment->addStaticText(L"", ox::core::CRect<int>(0, 0, 90, 20), false, false, ActionPanel, -1, 0);
    return 0;
}
"""
        forms = candidates(source, symbol, authored.AUTHORED.get(key, []))
        combined = "info-text-style+selected-name-style+operator-text-style+mini-stat-style"
        self.assertIn(combined, forms)
        self.assertEqual(forms[combined].count(b'ActionPanel, -1, L"");'), 4)
        self.assertNotIn(b"ActionPanel, -1, 0);", forms[combined])
        self.assertEqual(forms["info-text-style"].count(b'ActionPanel, -1, L"");'), 1)


class Keep(unittest.TestCase):
    def test_keeps_exact_with_no_function_worse(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        after = evaluation({SYMBOL: True, "other": True}, {SYMBOL: 100.0, "other": 100.0})
        self.assertTrue(keep(before, after, SYMBOL))

    def test_keeps_an_improved_inexact_candidate(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        after = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 95.0, "other": 100.0})
        self.assertTrue(keep(before, after, SYMBOL))

    def test_rejects_errors_unimproved_lost_exact_and_worse_scores(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": True}, error="boom"), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 89.0, "other": 100.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": False}, {SYMBOL: 95.0, "other": 100.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 95.0, "other": 99.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": False}, {SYMBOL: 100.0, "other": 50.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({SYMBOL: True, "other": True}, {SYMBOL: 100.0, "other": 99.0}), SYMBOL))
        self.assertFalse(keep(before, evaluation({"other": True}, {"other": 100.0}), SYMBOL))


class HeaderConstant(unittest.TestCase):
    def test_replaces_only_the_named_enumerator_value(self):
        source = b'''// CG_PROGRAM = 4125
enum CGenum { CG_SOURCE = 4112, CG_PROGRAM = 4125, CG_OBJECT = 4113 };
const char* example = "CG_PROGRAM = 4125";
'''
        edit = authored.ConstantEdit("namespace", "daisy/video/CgApi.h", "CG_PROGRAM", b"4125", b"4109")
        result = authored.constant_candidate(source, edit)
        self.assertEqual(result, source.replace(b"CG_PROGRAM = 4125, CG_OBJECT", b"CG_PROGRAM = 4109, CG_OBJECT"))
        self.assertIsNone(authored.constant_candidate(source.replace(b"CG_PROGRAM = 4125,", b"CG_PROGRAM = 1,"), edit))
        self.assertIsNone(authored.constant_candidate(source + b"enum Other { CG_PROGRAM = 4125 };\n", edit))
        self.assertIsNone(authored.constant_candidate(b"const int CG_PROGRAM = 4125;\n", edit))

    def test_partial_gain_requires_no_lost_or_lower_function(self):
        before = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 90.0, "other": 100.0})
        improved = evaluation({SYMBOL: False, "other": True}, {SYMBOL: 91.0, "other": 100.0})
        self.assertTrue(authored.keep_improvement(before, improved, SYMBOL))
        self.assertTrue(authored.keep_improvement(before, evaluation({SYMBOL: True, "other": True}, {SYMBOL: 90.0, "other": 100.0}), SYMBOL))
        self.assertFalse(authored.keep_improvement(before, before, SYMBOL))
        self.assertFalse(authored.keep_improvement(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 91.0, "other": 99.0}), SYMBOL))
        self.assertFalse(authored.keep_improvement(before, evaluation({SYMBOL: False, "other": False}, {SYMBOL: 91.0, "other": 100.0}), SYMBOL))
        self.assertFalse(authored.keep_improvement(before, evaluation({SYMBOL: False}, {SYMBOL: 91.0}), SYMBOL))
        self.assertFalse(authored.keep_improvement(before, evaluation({SYMBOL: False, "other": True}, {SYMBOL: 91.0, "other": 100.0}, error="boom"), SYMBOL))

    def test_header_write_requires_all_dependent_units_to_preserve_scores(self):
        unit, dependent = "d/a.cpp", "d/b.cpp"
        edit = authored.ConstantEdit("namespace", "d/constants.h", "CG_PROGRAM", b"4125", b"4109")

        class Checkout:
            def __init__(self, root, regress):
                self.root = root
                self.regress = regress
                self.items = {}

            def dependents(self, path):
                self_path = "src/d/constants.h"
                if path != self_path:
                    raise AssertionError(path)
                return [unit, dependent]

            def evaluate_overlays(self, items):
                self.items = items
                result = {}
                for name, (source_unit, overlays) in items.items():
                    if source_unit == unit:
                        result[name] = evaluation({SYMBOL: False}, {SYMBOL: 91.0 if overlays else 90.0})
                    else:
                        score = 99.0 if overlays and self.regress else 100.0
                        result[name] = evaluation({"other": True}, {"other": score})
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "src/d/constants.h"
            path.parent.mkdir(parents=True)
            source = b"enum CGenum { CG_PROGRAM = 4125 };\n"
            path.write_bytes(source)
            checkout = Checkout(root, regress=True)
            report = authored.apply_header(checkout, unit, SYMBOL, edit, write=True)
            self.assertFalse(report["applied"])
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(len(checkout.items), 4)

            checkout.regress = False
            report = authored.apply_header(checkout, unit, SYMBOL, edit, write=False)
            self.assertEqual(report["chosen"], "namespace")
            self.assertEqual(path.read_bytes(), source)
            report = authored.apply_header(checkout, unit, SYMBOL, edit, write=True)
            self.assertTrue(report["applied"])
            self.assertEqual(path.read_bytes(), b"enum CGenum { CG_PROGRAM = 4109 };\n")


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class HeaderFunction(unittest.TestCase):
    def test_replacements_are_atomic_and_confined_to_the_named_function(self):
        source = b'''void Board::pick(int a)
{
    one = "";
    repeated = "";
    repeated = "";
}
void Board::skip()
{
    one = "";
}
'''
        edit = authored.HeaderFunctionEdit(
            "fallback", "board.h", (
                authored.Replacement(b'one = "";', b'one = "tab";', 1),
                authored.Replacement(b'repeated = "";', b'repeated = "tab";', 2),
            ),
        )
        result = authored.header_function_candidate(source, SYMBOL, edit)
        self.assertEqual(result.count(b'"tab"'), 3)
        self.assertIn(b'void Board::skip()\n{\n    one = "";', result)
        self.assertIsNone(authored.header_function_candidate(source.replace(b'    repeated = "";\n', b'', 1), SYMBOL, edit))

    def test_header_function_requires_dependent_scores_and_an_oracle_verdict(self):
        unit, dependent = "d/a.cpp", "d/b.cpp"
        edit = authored.HeaderFunctionEdit("fallback", "d/board.h", (authored.Replacement(b'x = 0;', b'x = 1;', 1),))

        class Checkout:
            def __init__(self, root):
                self.root = root
                self.regress = False

            def dependents(self, path):
                if path != "src/d/board.h":
                    raise AssertionError(path)
                return [unit, dependent]

            def evaluate_overlays(self, items):
                result = {}
                for name, (source_unit, overlays) in items.items():
                    if source_unit not in (unit, dependent) or set(overlays) not in (set(), {"src/d/board.h"}):
                        raise AssertionError((source_unit, overlays))
                    scores = {SYMBOL: 91.0 if overlays else 90.0, "other": 100.0}
                    if source_unit == dependent and overlays and self.regress:
                        scores["other"] = 99.0
                    item = evaluation({SYMBOL: False, "other": True}, scores)
                    item.object = self.root / f"{name}.o"
                    result[name] = item
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "src/d/board.h"
            path.parent.mkdir(parents=True)
            source = b"void Board::pick(int a) { x = 0; }\n"
            path.write_bytes(source)
            checkout = Checkout(root)
            with patch("concord.authored.oracle.gate_objects", return_value=(False, {"same": {"verdict": "differ"}})):
                report = authored.apply_header_function(checkout, unit, SYMBOL, edit, write=True)
            self.assertIsNone(report["chosen"])
            self.assertEqual(path.read_bytes(), source)
            with patch("concord.authored.oracle.gate_objects", return_value=(True, None)):
                report = authored.apply_header_function(checkout, unit, SYMBOL, edit, write=True)
            self.assertIsNone(report["chosen"])
            with patch("concord.authored.oracle.gate_objects", return_value=(True, {"original": {"before": 2, "after": 0}})) as gate:
                checkout.regress = True
                report = authored.apply_header_function(checkout, unit, SYMBOL, edit, write=True)
                self.assertIsNone(report["chosen"])
                gate.assert_not_called()
                checkout.regress = False
                report = authored.apply_header_function(checkout, unit, SYMBOL, edit, write=True)
                self.assertTrue(report["applied"])
                self.assertEqual(path.read_bytes(), b"void Board::pick(int a) { x = 1; }\n")
                self.assertEqual(gate.call_count, 1)


if __name__ == "__main__":
    unittest.main()
