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
    "core::vector2d": "ox::core::CVector2d",
    "core::position2d": "ox::core::CPosition2d",
    "core::dimension2d": "ox::core::CDimension2d",
    "core::vector3d": "ox::core::CVector3d",
    "core::vector3df": "ox::core::CVector3d<float>",
    "core::matrix4": "ox::core::CMatrix4",
    "core::aabbox3d": "ox::core::CAabbox3d",
    "core::line3d": "ox::core::CLine3d",
    "core::triangle3d": "ox::core::CTriangle3d",
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
}


def translate(source: str) -> str:
    """The Irrlicht source with daisy's namespace and ox's types and headers."""
    source = re.sub(r"\bnamespace irr\b", "namespace daisy", source)
    source = re.sub(r"\birr::", "daisy::", source)
    for name, replacement in sorted(TYPES.items(), key=lambda kv: -len(kv[0])):
        source = re.sub(r"(?<![\w:])" + re.escape(name) + r"\b", replacement, source)
    for name, replacement in TYPEDEFS.items():
        source = re.sub(r"\b" + name + r"\b", replacement, source)
    for name, replacement in HEADERS.items():
        source = source.replace(f'#include "{name}"', f'#include "{replacement}"')
    source = re.sub(r"^(?!#include)(.*?)(?<![\w:/])IUnknown\b", r"\1ox::IUnknown", source, flags=re.MULTILINE)
    source = re.sub(r"\s*#ifdef _DEBUG\s*\n\s*setDebugName\([^)]*\);\s*\n\s*#endif", "", source)
    return source
