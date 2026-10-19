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
}


def translate(source: str, namespace: str = "daisy") -> str:
    """The Irrlicht source with ox's types and headers, in daisy's namespace, or in
    ox's for one of Irrlicht's public types, which daisy kept there."""
    source = re.sub(r"\bnamespace irr\b", f"namespace {namespace}", source)
    source = re.sub(r"\birr::(?=(?:core|video|scene|io|gui)::)", "", source)  # qualified again below
    source = re.sub(r"\birr::", "daisy::", source)
    source = re.sub(r"\bOnEvent\(\s*SEvent\b", "OnEvent(const SEvent&", source)  # Harvest takes SEvent by const reference
    for name, replacement in sorted(TYPES.items(), key=lambda kv: -len(kv[0])):
        source = re.sub(r"(?<![\w:])" + re.escape(name) + r"\b", replacement, source)
        if replacement.endswith(">"):  # GCC 4.4 reads the >> of nested template arguments as a shift
            source = source.replace(replacement + ">", replacement + " >")
    for name, replacement in TYPEDEFS.items():
        source = re.sub(r"\b" + name + r"\b", replacement, source)
    source = re.sub(r"\b(\w+)\(\1\s*&", r"\1(const \1&", source)  # copies of Harvest's containers take const references
    source = re.sub(r"\boperator\s*<\(\s*(\w+)\s*&", r"operator <(const \1&", source)
    source = re.sub(r"\bE_DRIVER_TYPE(\s+(?:\w+::)?getDriverType\s*\()", r"int\1", source)  # ox's drivers return int
    source = re.sub(r"\bECOLOR_FORMAT(\s+(?:\w+::)?getColorFormat\s*\()", r"int\1", source)
    source = re.sub(r"(?<!unsigned )\bint(\s+(?:\w+::)?get(?:Red|Green|Blue|Alpha)Mask\s*\()", r"unsigned int\1", source)
    for name, replacement in HEADERS.items():
        source = source.replace(f'#include "{name}"', f'#include "{replacement}"')
    source = re.sub(r"^(?!#include)(.*?)(?<![\w:/])IUnknown\b", r"\1ox::IUnknown", source, flags=re.MULTILINE)
    source = re.sub(r'^#include "irrTypes.h"\n', "", source, flags=re.MULTILINE)  # its typedefs are translated away

    source = re.sub(r"\s*#ifdef _DEBUG\s*\n\s*setDebugName\([^)]*\);\s*\n\s*#endif", "", source)
    # daisy's logger takes the level explicitly where Irrlicht's defaulted it
    source = re.sub(r"\bos::Printer::log\(([^,()]+)\);", r"os::Printer::log(\1, ELL_INFORMATION);", source)
    for pattern, replacement in CONTAINER_CALLS:
        source = re.sub(pattern, replacement, source)
    return _unqualify_in_class(source)


# Irrlicht's core::array and core::list calls as calls on the std::vector and
# std::list that ox::TArray and ox::TList derive from.
OBJECT = r"((?:\w+(?:\[[^\]\n]*\])?(?:\.|->))*\w+(?:\[[^\]\n]*\])?)"
CONTAINER_CALLS = [
    (OBJECT + r"\.const_pointer\(\)", r"&\1[0]"),
    (OBJECT + r"\.pointer\(\)", r"&\1[0]"),
    (OBJECT + r"->const_pointer\(\)", r"&(*\1)[0]"),
    (OBJECT + r"->pointer\(\)", r"&(*\1)[0]"),
    (r"(\.|->)set_used\(", r"\1resize("),
    (r"(\.|->)reallocate\(", r"\1reserve("),
    (r"(\.|->)getLast\(\)", r"\1back()"),
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
