"""Translate Irrlicht 0.7 source into Harvest's daisy and ox vocabulary.

daisy is a fork of Irrlicht 0.7, and Harvest keeps Irrlicht's source in
third_party/irrlicht-0.7 as a reference. Many daisy functions without recovered
source are Irrlicht functions with the namespace, the integer typedefs and the
core and video types renamed: `irr::core::rect<s32>` is `ox::core::CRect<int>`,
`video::IImage` is `ox::video::IImage`. `translate` applies those renames, so an
Irrlicht function becomes a seed the searches can take from there.
"""

from __future__ import annotations

import re

# Irrlicht's integer and float typedefs (irrTypes.h).
TYPEDEFS = {
    "s8": "char", "u8": "unsigned char", "s16": "short", "u16": "unsigned short",
    "s32": "int", "u32": "unsigned int", "f32": "float", "f64": "double", "c8": "char",
}  # fmt: skip

# Irrlicht core and video types and their ox counterparts in Harvest's headers.
TYPES = {
    "core::rect": "ox::core::CRect",
    "core::array": "ox::TArray",
    "core::list": "ox::TList",
    "core::stringc": "ox::core::CString<char>",
    "core::stringw": "ox::core::CString<wchar_t>",
    "core::aabbox3df": "ox::core::CAabbox3d<float>",
    "core::line3df": "ox::core::CLine3d<float>",
    "core::triangle3df": "ox::core::CTriangle3d<float>",
    "core::vector2df": "ox::core::CVector2d<float>",
    "core::vector2d": "ox::core::CVector2d",
    "core::position2d": "ox::core::CPosition2d",
    "core::dimension2d": "ox::core::CDimension2d",
    "core::vector3d": "ox::core::CVector3d",
    "core::vector3df": "ox::core::CVector3d<float>",
    "core::matrix4": "ox::core::CMatrix4",
    "core::aabbox3d": "ox::core::CAabbox3d",
    "core::line3d": "ox::core::CLine3d",
    "core::triangle3d": "ox::core::CTriangle3d",
    "core::plane3df": "ox::core::CPlane3d<float>",
    "core::plane3d": "ox::core::CPlane3d",
    "core::quaternion": "ox::core::CQuaternion",
    "video::IImage": "ox::video::IImage",
    "video::SColor": "ox::video::SColor",
    "video::ITexture": "ox::video::ITexture",
    "video::getAlpha": "ox::video::getAlpha",
    "video::getRed": "ox::video::getRed",
    "video::getGreen": "ox::video::getGreen",
    "video::getBlue": "ox::video::getBlue",
    "video::RGB16": "ox::video::RGB16",
}

# Irrlicht headers and the Harvest header that declares the same thing.
HEADERS = {
    "rect.h": "ox/core/CRect.h",
    "vector2d.h": "ox/core/CVector2d.h",
    "position2d.h": "ox/core/CPosition2d.h",
    "dimension2d.h": "ox/core/CDimension2d.h",
    "vector3d.h": "ox/core/CVector3d.h",
    "SColor.h": "ox/video/SColor.h\"\n#include \"ox/video/ColorPacking.h",
    "IImage.h": "ox/video/IImage.h",
    "IUnknown.h": "ox/IUnknown.h",
    "array.h": "ox/TArray.h",
    "irrList.h": "ox/TList.h",
    "irrString.h": "ox/core/CString.h",
    "aabbox3d.h": "ox/core/CAabbox3d.h",
    "matrix4.h": "ox/core/CMatrix4.h",
    "line3d.h": "ox/core/CLine3d.h",
    "triangle3d.h": "ox/core/CTriangle3d.h",
    "plane3d.h": "ox/core/CPlane3d.h",
    "quaternion.h": "ox/core/CQuaternion.h",
    "irrMath.h": "ox/core/irrMath.h",
}


LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"')

# The file-local helpers the call-site expansions emit stand under `_concord_`
# markers until the unit's text is known: `qualify` gives each the plain name a
# person would write, numbered when the unit or a header already uses it.
HELPERS = {
    "_concord_interpolate": "interpolate",
    "_concord_isOnSameSide": "isOnSameSide",
    "_concord_getIntersectionWithLine": "getIntersectionWithLine",
}


def code_words(text: str) -> set[str]:
    """Identifiers in the code of `text`. Names in comments, string literals and
    after . or -> cannot collide with a file-local helper."""
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.DOTALL)
    code = LITERAL.sub("", code)
    code = re.sub(r"(?:\.|->)\s*\w+", "", code)
    return set(re.findall(r"\b\w+\b", code))


def helper_names(text: str, taken: set[str]) -> dict[str, str]:
    """The name each `_concord_` helper marker in the text takes: the plain name a
    person would write, numbered while the unit or a header already uses it."""
    own = code_words(text) - set(HELPERS)
    names = {}
    for marker in dict.fromkeys(re.findall(r"\b_concord_\w+\b", text)):
        plain = HELPERS.get(marker, marker.removeprefix("_concord_"))
        name, n = plain, 2
        while name in taken or name in own:
            name, n = f"{plain}{n}", n + 1
        names[marker] = name
        own.add(name)
    return names


def name_helpers(text: str, taken: set[str]) -> str:
    """The text with each `_concord_` helper marker renamed per `helper_names`."""
    for marker, name in helper_names(text, taken).items():
        text = re.sub(r"\b" + marker + r"\b", name, text)
    return text


def _protect_literals(source: str) -> tuple[str, list[str]]:
    """The source with each string literal outside preprocessor lines replaced by
    a placeholder, and the literals: renaming `s32` to `int` must not reach the
    text of a message the original binary still holds ("found s32 file")."""
    literals: list[str] = []

    def keep(match: re.Match) -> str:
        literals.append(match.group())
        return f"__concord_literal_{len(literals) - 1}__"

    lines = [line if line.lstrip().startswith("#") else LITERAL.sub(keep, line) for line in source.split("\n")]
    return "\n".join(lines), literals


def translate(source: str, namespace: str = "daisy") -> str:
    """The Irrlicht source with ox's types and headers, in daisy's namespace, or in
    ox's for one of Irrlicht's public types, which daisy kept there. String
    literals are left as they are."""
    source, literals = _protect_literals(source)
    return re.sub(r"__concord_literal_(\d+)__", lambda m: literals[int(m[1])], _translate(source, namespace))


def _translate(source: str, namespace: str) -> str:
    # GCC emits the destructor variants' exception tables in the target's
    # order when the mesh destructor precedes its constructor.
    destructor = re.search(r"(?://! destructor\n)?CMeshSceneNode::~CMeshSceneNode\(\)\s*\{.*?^\}", source, re.MULTILINE | re.DOTALL)
    constructor = re.search(r"(?://! constructor\n)?CMeshSceneNode::CMeshSceneNode\(", source)
    if destructor and constructor and destructor.start() > constructor.start():
        source = source[:constructor.start()] + destructor.group() + "\n\n" + source[constructor.start():destructor.start()] + source[destructor.end():]
    # The Maya camera writes the base position directly before updating its
    # absolute transform; an unqualified call dispatches virtually in daisy.
    source = re.sub(
        r"(void CCameraMayaSceneNode::OnPostRender\([^)]*\)\s*\{[^}]*?)(?<!::)\bsetPosition\(Pos\);",
        r"\1ISceneNode::setPosition(Pos);",
        source,
    )
    # daisy's Maya camera overrides setPosition: the Mac vtable gives slot 29 of
    # CCameraMayaSceneNode's table to CCameraMayaSceneNode::setPosition, where
    # Irrlicht 0.7's class inherits ISceneNode's. The node reads the position as
    # its distance from the target, so the override stores the length as the
    # current zoom.
    if re.search(r"class CCameraMayaSceneNode\s*:", source):
        source = re.sub(
            r"(\t\tvirtual void OnPostRender\(u32 timeMs\);\n)",
            r"\1\n\t\t//! sets the position of the node; the maya camera reads it as its zoom\n"
            r"\t\tvirtual void setPosition(const core::vector3df& newpos);\n",
            source,
        )
    if "void CCameraMayaSceneNode::OnPostRender(" in source:
        source = re.sub(
            r"(CCameraMayaSceneNode::~CCameraMayaSceneNode\(\)\n\{\n\}\n)",
            "\\1\n\n//! sets the position of the node\n"
            "void CCameraMayaSceneNode::setPosition(const core::vector3df& newpos)\n"
            "{\n\tcurrentZoom = newpos.getLength();\n}\n",
            source,
        )
    # Daisy's particle buffers reserve capacity without changing their size.
    # The target's reallocateBuffers calls vector::reserve and precedes the
    # simulation step; changing the shared array header affects other units.
    buffers = re.search(r"^void CParticleSystemSceneNode::reallocateBuffers\(\)\s*\{.*?^\}", source, re.MULTILINE | re.DOTALL)
    step = "void CParticleSystemSceneNode::doParticleSystem("
    if buffers and step in source:
        body = buffers.group().replace(".set_used(", ".reserve(")
        source = source[:buffers.start()] + source[buffers.end():]
        source = source.replace(step, body + "\n\n" + step, 1)
    source = re.sub(r"\bnamespace irr\b", f"namespace {namespace}", source)
    source = re.sub(r"\birr::(?=(?:core|video|scene|io|gui|ELL_\w+|" + "|".join(TYPEDEFS) + r")\b)", "", source)  # qualified again below
    source = re.sub(r"\birr::", "daisy::", source)
    source = re.sub(r"\bOnEvent\(\s*SEvent\b", "OnEvent(const SEvent&", source)  # Harvest takes SEvent by const reference
    for name, replacement in sorted(TYPES.items(), key=lambda kv: -len(kv[0])):
        source = re.sub(r"(?<![\w:])" + re.escape(name) + r"\b", replacement, source)
        if replacement.endswith(">"):  # GCC 4.4 reads the >> of nested template arguments as a shift
            source = source.replace(replacement + ">", replacement + " >")
    for name, replacement in TYPEDEFS.items():
        source = re.sub(r"\b" + name + r"\b", replacement, source)
    source = re.sub(r"\bEVDF_BILINEAR_FILER\b", "EVDF_BILINEAR_FILTER", source)  # Irrlicht's spelling; ox's is FILTER
    source = re.sub(r"\b(const\s+float\s+PI\s*=\s*)3\.14159f;", r"\g<1>3.14159274f;", source)  # Oxeye's full-precision float PI
    source = re.sub(
        r"(\w+)\.clipAgainst\(([^)]+)\);",
        r"if ((\2).LowerRightCorner.X < \1.LowerRightCorner.X) \1.LowerRightCorner.X = (\2).LowerRightCorner.X; "
        r"if ((\2).LowerRightCorner.Y < \1.LowerRightCorner.Y) \1.LowerRightCorner.Y = (\2).LowerRightCorner.Y; "
        r"if ((\2).UpperLeftCorner.X > \1.UpperLeftCorner.X) \1.UpperLeftCorner.X = (\2).UpperLeftCorner.X; "
        r"if ((\2).UpperLeftCorner.Y > \1.UpperLeftCorner.Y) \1.UpperLeftCorner.Y = (\2).UpperLeftCorner.Y;",
        source,
    )  # Irrlicht's CRect::clipAgainst, written out, so ox's CRect needs no member for it
    source = re.sub(r"((?:\w+(?:\[[^\]\n]*\])?(?:\.|->))*\w+(?:\([^)]*\))?)\.toA1R5G5B5\(\)", r"ox::video::toA1R5G5B5(\1.color)", source)  # a colour's packing is a free function in ox
    # SColor, SColorf, S3DVertex, CVector3d and CMatrix4 members written out where completing the shared ox header shifts DECL_UID modulo 31
    source = re.sub(r"\bColor\s*==\s*other\.Color\b", "Color.color == other.Color.color", source)
    source = re.sub(r"(\w+(?:\.\w+)*)\.set\(\s*([^,()]+),\s*([^,()]+),\s*([^,()]+),\s*([^,()]+)\)", r"\1 = ox::video::SColor(\2, \3, \4, \5)", source)
    source = re.sub(
        r"(\w+(?:\.\w+)+)\.toSColor\(\)",
        r"ox::video::SColor((int)(\1.a*255.0f), (int)(\1.r*255.0f), (int)(\1.g*255.0f), (int)(\1.b*255.0f))",
        source,
    )
    source = re.sub(
        r"(\w+)\.rotateXYBy\(([^,]+),\s*([^;\n]+)\);",
        r"{ double deg = (\2) * ox::core::GRAD_PI2; float cs = (float)cos(deg); float sn = (float)sin(deg); "
        r"\1.X -= (\3).X; \1.Y -= (\3).Y; \1.set(\1.X*cs - \1.Y*sn, \1.X*sn + \1.Y*cs, \1.Z); \1.X += (\3).X; \1.Y += (\3).Y; }",
        source,
    )
    source = re.sub(
        r"(\w+)\.rotateXZBy\(([^,]+),\s*([^;\n]+)\);",
        r"{ double deg = (\2) * ox::core::GRAD_PI2; float cs = (float)cos(deg); float sn = (float)sin(deg); "
        r"\1.X -= (\3).X; \1.Z -= (\3).Z; \1.set(\1.X*cs - \1.Z*sn, \1.Y, \1.X*sn + \1.Z*cs); \1.X += (\3).X; \1.Z += (\3).Z; }",
        source,
    )
    source = re.sub(
        r"(\w+)\.rotateYZBy\(([^,]+),\s*([^;\n]+)\);",
        r"{ double deg = (\2) * 0.017453290522098541; float cs = (float)cos(deg); float sn = (float)sin(deg); "
        r"\1.Z -= (\3).Z; \1.Y -= (\3).Y; \1.set(\1.X, \1.Y*cs - \1.Z*sn, \1.Y*sn + \1.Z*cs); \1.Z += (\3).Z; \1.Y += (\3).Y; }",
        source,
    )
    source = re.sub(r"(\w+)\.getExtent\(\)", r"((\1).MaxEdge - (\1).MinEdge)", source)
    source = re.sub(
        r"(\w+(?:\[\w+\]|\.\w+)+)\.getInterpolated\((?![^;\n]*?\b(?:Min|Max)StartColor\b)([^,()]+),\s*([^,()]+)\)",
        r"ox::core::CVector3d<float>((\2).X*(1.0f - (\3)) + \1.X*(\3), (\2).Y*(1.0f - (\3)) + \1.Y*(\3), (\2).Z*(1.0f - (\3)) + \1.Z*(\3))",
        source,
    )
    source = re.sub(r"\bos::Randomizer::rand\(\)", "ox::algo::CRand::rand()", source)
    source = re.sub(r"\bos::Randomizer::reset\(\)", "ox::algo::CRand::reset()", source)
    source = re.sub(
        r"(\bFont->draw\([^,]+,\s*[^,]+,\s*[^,]+),\s*true,\s*true\)",
        r"\1, ox::gui::EFHA_CENTER, ox::gui::EFVA_CENTER, 0)",
        source,
    )
    source = re.sub(
        r"(\w+(?:\.\w+)*)\s*(\*?=)\s*([^;\n]*?)\.interpolate\(\s*([^,]+?),\s*([^)]+)\);",
        r"\1 \2 _concord_interpolate(\3, \4, \5);",
        source,
        flags=re.DOTALL,
    )
    if "_concord_interpolate(" in source and "inline ox::core::CMatrix4 _concord_interpolate(" not in source:
        helper = (
            "inline ox::core::CMatrix4 _concord_interpolate(const ox::core::CMatrix4& self, const ox::core::CMatrix4& b, float time, int pad = 0)\n"
            "{\n\tfloat ntime = 1.0f - time;\n\tox::core::CMatrix4 mat;\n"
            "\tif (pad) { mat.M[0] = 0.0f; mat.M[1] = 0.0f; mat.M[2] = 0.0f; mat.M[3] = 0.0f; mat.M[4] = 0.0f; mat.M[5] = 0.0f; mat.M[6] = 0.0f; mat.M[7] = 0.0f; mat.M[8] = 0.0f; }\n"
            "\tfor (int i=0; i<16; ++i)\n"
            "\t\tmat.M[i] = self.M[i]*ntime + b.M[i]*time;\n\n\treturn mat;\n}\n\n"
        )
        source = re.sub(r"^(void\s+\w+::animateSkeleton\s*\()", helper + r"\1", source, count=1, flags=re.MULTILINE)
    if "Vertices.linear_reverse_search(" in source and "operator == (const S3DVertex&" not in source:
        vertex_eq = (
            "} }\nnamespace ox { namespace video {\n"
            "inline bool operator == (const S3DVertex& a, const S3DVertex& b)\n"
            "{\n\treturn (a.Pos == b.Pos && a.Normal == b.Normal &&\n"
            "\t\t\t\tb.Color.color == a.Color.color && a.TCoords == b.TCoords);\n"
            "}\n} }\nnamespace daisy { namespace scene {\n"
        )
        source = re.sub(r"^(void\s+\w+::addFacesToBuffer\s*\()", vertex_eq + r"\1", source, count=1, flags=re.MULTILINE)
    source = re.sub(
        r"(\w+(?:\[(?:[^\[\]\n]|\[[^\]\n]*\])*\]|\.\w+)*)\s*=\s*event\.KeyInput\.PressedDown;[ \t]*\n([ \t]*)return true;",
        r"if (event.KeyInput.Event == ox::event::EKIE_KEY_PRESSED_DOWN)\n\2{\n\2\t\1 = true;\n\2\treturn true;\n\2}\n\2else if (event.KeyInput.Event == ox::event::EKIE_KEY_LEFT_UP)\n\2{\n\2\t\1 = false;\n\2\treturn true;\n\2}\n\2return false;",
        source,
    )
    source = re.sub(
        r"(void\s+CCameraSceneNode::OnPreRender\s*\(\)\s*\{\s*(?:ox::)?video::IVideoDriver\*\s+driver\s*=\s*SceneManager->getVideoDriver\(\);\s*if\s*\(!driver\)\s*return;\n)"
        r"(\s*if\s*\(SceneManager->getActiveCamera\(\)\s*==\s*this\)\s*\{\s*screenDim\.Width\s*=[^\n]+\n\s*screenDim\.Height\s*=[^\n]+\n)"
        r"\s*driver->setTransform\(((?:ox::)?video::ETS_PROJECTION),\s*Projection\);\n"
        r"([\s\S]*?recalculateViewArea\(\);\n)",
        r"\1\n\tupdateAbsolutePosition();\n\2\4\t\tdriver->setTransform(\3, Projection);\n",
        source,
    )
    group_match = re.search(r"\n\n(void\s+C3DSMeshFileLoader::readMaterialGroup\s*\([^}]+\}\n)", source)
    if group_match and "bool C3DSMeshFileLoader::readMaterialChunk(" in source:
        source = source[: group_match.start()] + source[group_match.end() :]
        source = source.replace(
            "bool C3DSMeshFileLoader::readMaterialChunk(",
            group_match.group(1) + "\n\nbool C3DSMeshFileLoader::readMaterialChunk(",
            1,
        )
    source = re.sub(r"(?<![\w)])((?:\w+(?:\[[^\]\n]*\])?(?:\.|->))+)getColorFormat\(\)", r"(ECOLOR_FORMAT)\1getColorFormat()", source)  # ox's IImage returns int
    source = re.sub(r"(\bswitch\s*\()([^;\n]*?->getVertexType\(\)|vertexType)(\))", r"\1(int)\2\3", source)
    source = re.sub(r"\b(\w+)\(\1\s*&", r"\1(const \1&", source)  # copies of Harvest's containers take const references
    source = re.sub(r"\boperator\s*<\(\s*(\w+)\s*&", r"operator <(const \1&", source)
    source = re.sub(r"\bE_DRIVER_TYPE(\s+(?:\w+::)?getDriverType\s*\()", r"int\1", source)  # ox's drivers return int
    source = re.sub(r"\bECOLOR_FORMAT(\s+(?:\w+::)?getColorFormat\s*\()", r"int\1", source)
    source = re.sub(r"(?<!unsigned )\bint(\s+(?:\w+::)?get(?:Red|Green|Blue|Alpha)Mask\s*\()", r"unsigned int\1", source)
    # 32-bit Irrlicht did byte arithmetic on int-cast pointers; on x86-64 the int
    # truncates the pointer, so the cast moves to char* and the difference keeps
    # the computation's meaning (CQ3LevelMesh::Blend, createCurvedSurface).
    source = re.sub(
        r"\(int\)\s*(&[^;()\n,]+?)\s*-\s*\(int\)\s*([^;()\n,]+?)(?=\s*\))",
        r"(int)((char*)\1 - (char*)\2)",
        source,
    )
    source = re.sub(
        r"\(int\)\s*([^;()\n,]+?)\s*-\s*\(int\)\s*(&[^;()\n,]+?)(?=\s*\))",
        r"(int)((char*)\1 - (char*)\2)",
        source,
    )
    source = re.sub(
        r"\(\s*([\w:<>]+(?:\s*<[^>]*>)?)\s*\*\s*\)\s*\(\s*\(int\)\s*([^()\n]+?)\s*\+\s*([^()\n]+?)\s*\)",
        r"(\1*)((char*)\2 + \3)",
        source,
    )
    # The original 64-bit Q3 port reads the first float of each control vertex
    # for every blended field: all 36 nonzero mulss displacements are zero in
    # the target. Preserve that port's behavior, rather than repairing Irrlicht's
    # 32-bit field-offset arithmetic (Goal 3 permits target-proven behavior).
    def _q3_blend_body(match: re.Match[str]) -> str:
        body = re.sub(
            r"\(float\*\)\(\(char\*\)(v\[[^\]\n]+\])\s*\+\s*offset\)",
            r"(float*)\1",
            match[0],
        )
        comment = "\t// The original 1.18 body ignores offset; see docs/port/original-bugs.md.\n"
        if comment not in body:
            body = re.sub(
                r"^((?:inline\s+)?float\s+CQ3LevelMesh::Blend\([^;{]*\)\s*\{\n)",
                r"\1" + comment,
                body,
                count=1,
                flags=re.MULTILINE,
            )
        return body

    source = re.sub(
        r"^(?:inline\s+)?float\s+CQ3LevelMesh::Blend\([^;{]*\)\s*\{.*?^\}",
        _q3_blend_body,
        source, flags=re.MULTILINE | re.DOTALL,
    )
    # Daisy updates an animated mesh's absolute transform during OnPreRender.
    # Its target OnPostRender omits Irrlicht's extra update; camera overrides
    # retain their own update calls and already match with them present.
    source = re.sub(
        r"^void CAnimatedMeshSceneNode::OnPostRender\([^)]*\)\s*\{.*?^\}",
        lambda match: re.sub(r"^[ \t]*(?:// update absolute position\n[ \t]*)?updateAbsolutePosition\(\);\n", "", match[0], flags=re.MULTILINE),
        source, flags=re.MULTILINE | re.DOTALL,
    )
    # The port's scene cache uses its locale-aware helper, as the target's
    # tolower calls show; keep Irrlicht's ASCII helper in other classes.
    source = re.sub(
        r"^[\w:* ]+ CSceneManager::\w+\([^;{]*\)\s*\{.*?^\}",
        lambda match: re.sub(r"\b([\w.]+)\.make_lower\(\)",
                             r"ox::core::CStringFunctions::ansiMakeLower(\1)", match[0]),
        source, flags=re.MULTILINE | re.DOTALL,
    )
    # Render-list sorting retains the low int bits of a texture pointer. Cast
    # through the target's pointer-width long before narrowing on amd64.
    source = re.sub(r"\(int\)\(n->getMaterial\(0\)\.Texture1\)",
                    "(int)(long)(n->getMaterial(0).Texture1)", source)
    # Irrlicht's existFile looked inside open archives too; daisy's signature
    # takes that choice as an argument (CQ3LevelMesh::loadTextures).
    source = re.sub(r"(\.|->)existFile\(((?:[^,()]|\([^()]*\))+?)\)", r"\1existFile(\2, true)", source)
    # Q3's temporary texture arrays use vector::reserve in the target (the
    # two calls go to 0x574680); their explicit index writes initialize storage
    # without changing the vectors' sizes, as in the original 64-bit port.
    # Its archive-search argument is also false in the target (xor edx, edx).
    source = re.sub(
        r"^void CQ3LevelMesh::loadTextures\(\)\s*\{.*?^\}",
        lambda match: match[0].replace("tex.set_used(", "tex.reserve(").replace("lig.set_used(", "lig.reserve(")
        .replace("existFile(s.c_str(), true)", "existFile(s.c_str(), false)"),
        source, flags=re.MULTILINE | re.DOTALL,
    )
    # The reader narrows a wide attribute through the locale-aware helper: the
    # target calls wcstombs, which the templated CString<char> construction from
    # c_str() would not.
    source = re.sub(
        r"^float\s+CXMLReader::getAttributeValueAsFloat\([^)]*\)\s*\{.*?^\}",
        lambda match: match[0].replace(
            "= attr->Value.c_str();",
            "= ox::core::CStringFunctions::wideToAnsi(attr->Value.c_str());",
        ),
        source, flags=re.MULTILINE | re.DOTALL,
    )
    # The x-reader's material parser compares the token case-sensitively: the
    # target constructs a CString and runs its byte loop where the ancestor
    # calls equals_ignore_case (0x58e68f).
    source = re.sub(
        r"^bool CXFileReader::parseDataObjectMaterial\([^)]*\)\s*\{.*?^\}",
        lambda match: re.sub(
            r"objectName\.equals_ignore_case\((__concord_literal_\d+__)\)",
            r"objectName == \1", match[0],
        ),
        source, flags=re.MULTILINE | re.DOTALL,
    )
    # Daisy reads the TGA header field by field, the 16-bit fields through
    # CHelpIO::readShort, as CZipReader reads its packed headers; one read of
    # the packed struct would compile to a single call.
    source = re.sub(
        r"^IImage\*\s+CImageLoaderTGA::loadImage\([^)]*\)\s*\{.*?^\}",
        lambda match: match[0].replace(
            "file->read(&header, sizeof(STGAHeader));",
            "file->read(&header.IdLength, 1);\n"
            "\tfile->read(&header.ColorMapType, 1);\n"
            "\tfile->read(&header.ImageType, 1);\n"
            "\tfile->read(header.FirstEntryIndex, 2);\n"
            "\theader.ColorMapLength = ox::io::CHelpIO::readShort(file);\n"
            "\tfile->read(&header.ColorMapEntrySize, 1);\n"
            "\tfile->read(header.XOrigin, 2);\n"
            "\tfile->read(header.YOrigin, 2);\n"
            "\theader.ImageWidth = ox::io::CHelpIO::readShort(file);\n"
            "\theader.ImageHeight = ox::io::CHelpIO::readShort(file);\n"
            "\tfile->read(&header.PixelDepth, 1);\n"
            "\tfile->read(&header.ImageDescriptor, 1);",
        ),
        source, flags=re.MULTILINE | re.DOTALL,
    )
    # ox's S3DVertex declares no operator!=; compare the members as the
    # file-local vertex equality helper does (CBspTree::BspPoly::operator==).
    source = re.sub(
        r"vertices\[(\w+)\] != other\.vertices\[\1\]",
        r"!(vertices[\1].Pos == other.vertices[\1].Pos && vertices[\1].Normal == other.vertices[\1].Normal && "
        r"vertices[\1].Color.color == other.vertices[\1].Color.color && vertices[\1].TCoords == other.vertices[\1].TCoords)",
        source,
    )
    # daisy kept the signature where Irrlicht defaulted an argument:
    # addDummyTransformationSceneNode's id=-1 and addTexture's ECF_A1R5G5B5. The
    # two-argument addTexture is told from (name, image) by its size argument, a
    # declared dimension2d or a dimension2d literal.
    source = re.sub(
        r"(\.|->)addDummyTransformationSceneNode\(((?:[^,()]|\([^()]*\))*)\)",
        lambda m: m[1] + "addDummyTransformationSceneNode(" + (m[2].strip() + ", -1" if m[2].strip() else "0, -1") + ")",
        source,
    )
    sizes = set(
        re.findall(r"(?:ox::core::CDimension2d|core::dimension2d|CDimension2d|dimension2d)\s*<[^>]*>\s*(?:const\s*)?[&*]?\s*(\w+)", source)
    )
    source = re.sub(
        r"(\.|->)addTexture\(((?:ox::core::CDimension2d|core::dimension2d)<[^>]+>\s*\([^()]*\)|\w+)\s*,\s*((?:[^,()]|\([^()]*\))+?)\)",
        lambda m: m[1] + "addTexture(" + m[2] + ", " + m[3] + ", video::ECF_A1R5G5B5)"
        if "<" in m[2] or m[2] in sizes
        else m[0],
        source,
    )
    # Irrlicht's four-argument S3DVertex convenience constructor, written as the
    # nine-argument one S3DVertexInline.h defines (CBspTree::splitPoly).
    source = re.sub(
        r"\bS3DVertex(\s+\w+)?\s*\(" + LEAF_ARG + r",\s*" + LEAF_ARG + r",\s*" + LEAF_ARG + r",\s*" + LEAF_ARG + r"\)",
        lambda m: f"S3DVertex{m[1] or ''}({m[2]}.X, {m[2]}.Y, {m[2]}.Z, {m[3]}.X, {m[3]}.Y, {m[3]}.Z, {m[4]}, {m[5]}.X, {m[5]}.Y)",
        source,
    )
    # CPlane3d::getIntersectionWithLimitedLine calls CVector3d::isBetweenPoints,
    # which ox's CVector3d does not have; the plane-line half is expanded with the
    # between-points test Irrlicht's isBetweenPoints wrote (CBspTree::splitPoly).
    source = re.sub(
        OBJECT + r"\.getIntersectionWithLimitedLine\(" + LEAF_ARG + r",\s*" + LEAF_ARG + r",\s*" + LEAF_ARG + r"\)",
        r"(\1.getIntersectionWithLine(\2, (\3) - (\2), \4) && "
        r"(float)(\4).getDistanceFromSQ(\2) < (float)((\3) - (\2)).getLengthSQ() && "
        r"(float)(\4).getDistanceFromSQ(\3) < (float)((\3) - (\2)).getLengthSQ())",
        source,
    )
    # vector3d::setLength is normalize + scale (CSceneCollisionManager::collideWithWorld).
    source = re.sub(OBJECT + r"\.setLength\(([^;\n]+)\);", r"{ \1.normalize(); \1 *= (\2); }", source)
    # CTriangle3d::getIntersectionWithLine, expanded to a file-local helper that
    # writes Irrlicht's plane test and three isOnSameSide checks out, since adding
    # the member to the shared CTriangle3d.h would shift DECL_UID elsewhere.
    source = re.sub(
        r"(\w+(?:\[[^\]\n]*\])(?:\.(?:\w+|\w+\[[^\]\n]*\]))*)\.getIntersectionWithLine\(" + LEAF_ARG + r",\s*" + LEAF_ARG + r",\s*" + LEAF_ARG + r"\)",
        r"_concord_getIntersectionWithLine(\1, \2, \3, \4)",
        source,
    )
    if "_concord_getIntersectionWithLine(" in source and "inline bool _concord_getIntersectionWithLine(" not in source:
        helper = (
            "inline bool _concord_isOnSameSide(const ox::core::CVector3d<float>& p1, const ox::core::CVector3d<float>& p2,\n"
            "\t\tconst ox::core::CVector3d<float>& a, const ox::core::CVector3d<float>& b)\n"
            "{\n\tox::core::CVector3d<float> bminusa = b - a;\n"
            "\tox::core::CVector3d<float> cp1 = bminusa.crossProduct(p1 - a);\n"
            "\tox::core::CVector3d<float> cp2 = bminusa.crossProduct(p2 - a);\n"
            "\treturn (cp1.dotProduct(cp2) > 0.0f);\n}\n\n"
            "inline bool _concord_getIntersectionWithLine(const ox::core::CTriangle3d<float>& triangle,\n"
            "\t\tconst ox::core::CVector3d<float>& linePoint, const ox::core::CVector3d<float>& lineVect,\n"
            "\t\tox::core::CVector3d<float>& outIntersection)\n"
            "{\n\tox::core::CVector3d<float> normal = (triangle.pointB - triangle.pointA).crossProduct(triangle.pointC - triangle.pointA);\n"
            "\tfloat t2 = normal.dotProduct(lineVect);\n\tif (t2 == 0.0f)\n\t\treturn false;\n"
            "\tfloat d = triangle.pointA.dotProduct(normal);\n"
            "\tfloat t = -(normal.dotProduct(linePoint) - d) / t2;\n"
            "\toutIntersection = linePoint + (lineVect * t);\n"
            "\treturn (_concord_isOnSameSide(outIntersection, triangle.pointA, triangle.pointB, triangle.pointC) &&\n"
            "\t\t_concord_isOnSameSide(outIntersection, triangle.pointB, triangle.pointA, triangle.pointC) &&\n"
            "\t\t_concord_isOnSameSide(outIntersection, triangle.pointC, triangle.pointA, triangle.pointB));\n}\n\n"
        )
        at = re.search(r"^namespace ", source, re.MULTILINE)
        source = (source[: at.start()] + helper + source[at.start() :]) if at else helper + source
    for name, replacement in HEADERS.items():
        source = source.replace(f'#include "{name}"', f'#include "{replacement}"')
    source = re.sub(r"^(?!#include)(.*?)(?<![\w:/])IUnknown\b", r"\1ox::IUnknown", source, flags=re.MULTILINE)
    source = re.sub(r'^#include "irrTypes.h"\n', "", source, flags=re.MULTILINE)  # its typedefs are translated away

    source = re.sub(r"\s*#ifdef _DEBUG\s*\n\s*setDebugName\([^)]*\);\s*\n\s*#endif", "", source)
    # daisy's logger takes the level explicitly where Irrlicht's defaulted it
    source = re.sub(r"\bos::Printer::log\(([^,()]+)\);", r"os::Printer::log(\1, ELL_INFORMATION);", source)
    source = re.sub(r"\bos::Printer::log\(([^,;()]+,(?!\s*ELL_)[^,;]+)\);", r"os::Printer::log(\1, ELL_INFORMATION);", source, flags=re.DOTALL)  # a hint is not a level
    # daisy re-sorts a deduped index-size array after each push_back, the order
    # binarySearchPos assumes; the target's createIndexBuffers calls
    # ox::algo::sort once for widths and once for heights, each in the taken
    # branch. Authored: Irrlicht's core::array had no such call.
    source = re.sub(
        r"if \((widths|heights)\.binary_search\(((?:[^()]|\([^()]*\))+)\)==-1\)\s*\n(\s*)\1\.push_back\(\2\);",
        r"if (ox::algo::binarySearchPos(\1.begin(), \1.end(), \2)==-1)\n\3{\n\3\t\1.push_back(\2);\n\3\tox::algo::sort(\1.begin(), \1.end());\n\3}",
        source,
    )
    for pattern, replacement in CONTAINER_CALLS:
        source = re.sub(pattern, replacement, source)
    # ox's ISceneManager::registerNodeForRendering takes the render time Irrlicht defaulted
    source = re.sub(r"(\.|->)registerNodeForRendering\(([^,()]+)\)", r"\1registerNodeForRendering(\2, SNRT_DEFAULT)", source)
    # Irrlicht's const list::begin() yields Iterator; a const std::list's yields const_iterator
    for name in set(re.findall(r"\bconst\s+[^;=\n]*?\b(\w+)\s*=", source)):
        source = re.sub(
            r">::iterator(\s+\w+\s*=[^;\n]*?\b" + name + r"\s*\.\s*(?:begin|end|rbegin|rend)\s*\()",
            r">::const_iterator\1",
            source,
        )
    return "\n".join(line.rstrip() for line in _unqualify_in_class(source).split("\n"))  # no trailing spaces


# Irrlicht's core::array and core::list calls as calls on the std::vector and
# std::list that ox::TArray and ox::TList derive from. Searches become the
# free ox::algo helpers daisy calls on those containers' iterators.
OBJECT = r"((?:\(\*\w+\)|\w+)(?:\[[^\]\n]*\])?(?:(?:\.|->)\w+(?:\[[^\]\n]*\])?)*)"
ARG = r"((?:\([^()]*\)|[^()])*?)"  # one argument, allowing one level of parentheses
LEAF_ARG = r"((?:\([^()]*\)|[^(),])*?)"  # one argument of several: commas end it at the top level
CONTAINER_CALLS = [
    (OBJECT + r"\.const_pointer\(\)", r"&\1[0]"),
    (OBJECT + r"\.pointer\(\)\[", r"\1["),  # an element, not a pointer to the array's first element
    (OBJECT + r"\.pointer\(\)", r"&\1[0]"),
    (OBJECT + r"->const_pointer\(\)", r"&(*\1)[0]"),
    (OBJECT + r"->pointer\(\)\[", r"(*\1)["),
    (OBJECT + r"->pointer\(\)", r"&(*\1)[0]"),
    (OBJECT + r"\.sort\(\)", r"ox::algo::sort(\1.begin(), \1.end())"),
    (OBJECT + r"\.binary_search\(" + ARG + r"\)", r"ox::algo::binarySearchPos(\1.begin(), \1.end(), \2)"),
    (OBJECT + r"\.linear_(?:reverse_)?search\(" + ARG + r"\)", r"ox::algo::linearSearchPos(\1.begin(), \1.end(), \2)"),
    (OBJECT + r"\.erase\((?!a?it\))(\w+),\s*([^,()]+)\)", r"\1.erase(\1.begin() + (int)\2, \1.begin() + (int)(\2 + \3))"),
    (OBJECT + r"\.erase\((?!a?it\))(\w+)\)", r"\1.erase(\1.begin() + \2)"),  # Irrlicht erased by index; a vector erases by iterator
    (r"\s*[^\n;]+\.set_sorted\([^)]*\);\s*\n", "\n"),  # a sorted flag has no counterpart in a vector
    (r"(\.|->)set_used\(", r"\1resize("),
    (r"(\.|->)reallocate\(", r"\1reserve("),
    (r"(\.|->)getLast\(\)", r"\1back()"),
    (r"::ConstIterator\b", "::const_iterator"),
    (r"::Iterator\b", "::iterator"),
]


def _unqualify_in_class(source: str) -> str:
    """Drop the class's own name from a member declared inside it
    (`quaternion quaternion::operator*(...) const;`), which MSVC accepted and GCC
    does not."""
    classes = set(re.findall(r"^\s*(?:class|struct)\s+(\w+)\s*(?::[^;{]*)?$", source, re.MULTILINE))
    lines = []
    for line in source.split("\n"):
        if line.rstrip().endswith(";") and "=" not in line.replace("operator=", "") and line[:1] in " \t":
            for name in classes:
                line = re.sub(r"\b" + name + r"::(?=operator\b|~?\w+\s*\()", "", line)
        lines.append(line)
    return "\n".join(lines)


def bare(source: str, namespace: str = "") -> str:
    """Rename the core types a text names without qualification, as code written
    inside Irrlicht's core namespace does: vector3df, matrix4, plane3d<T>.
    `namespace` (ox::core::) is the destination's own and is left off its names."""
    lines = []
    for line in source.split("\n"):
        if not line.lstrip().startswith("#"):
            for name, replacement in sorted(TYPES.items(), key=lambda kv: -len(kv[0])):
                if name.startswith("core::"):
                    line = re.sub(r"(?<![\w:])" + re.escape(name.removeprefix("core::")) + r"\b", replacement, line)
                    if replacement.endswith(">"):
                        line = line.replace(replacement + ">", replacement + " >")
                    # the constructor and destructor of a renamed class: CQuaternion::quaternion(
                    own = replacement.split("::")[-1]
                    line = re.sub(r"\b" + own + r"::(~?)" + re.escape(name.removeprefix("core::")) + r"\b", own + r"::\1" + own, line)
            if namespace:
                line = line.replace(namespace, "")
        if re.fullmatch(r"\s*typedef (.+) \1;\s*", line):
            continue  # Irrlicht's alias for a type already written out: typedef plane3d<f32> plane3df
        lines.append(line)
    return "\n".join(lines)
