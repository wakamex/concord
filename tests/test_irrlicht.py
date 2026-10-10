"""Translating Irrlicht 0.7 source into Harvest's vocabulary."""

import unittest

from concord.irrlicht import translate


class Translate(unittest.TestCase):
    def test_namespace_typedefs_types_and_headers(self):
        source = (
            '#include "IUnknown.h"\n#include "SColor.h"\nnamespace irr\n{\nnamespace video\n{\n'
            "class C : public IUnknown\n{\n    void f(core::rect<s32> r, const u16* i, video::IImage* s)\n"
            "    {\n        #ifdef _DEBUG\n        setDebugName(\"C\");\n        #endif\n"
            "        s32 red = video::getRed(color);\n    }\n};\n}\n}\n"
        )
        out = translate(source)
        self.assertIn('#include "ox/IUnknown.h"', out)
        self.assertIn('#include "ox/video/SColor.h"\n#include "ox/video/ColorPacking.h"', out)
        self.assertIn("namespace daisy", out)
        self.assertIn("class C : public ox::IUnknown", out)
        self.assertIn("void f(ox::core::CRect<int> r, const unsigned short* i, ox::video::IImage* s)", out)
        self.assertIn("int red = ox::video::getRed(color);", out)
        self.assertNotIn("setDebugName", out)


if __name__ == "__main__":
    unittest.main()
