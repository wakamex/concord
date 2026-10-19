"""Translating Irrlicht 0.7 source into Harvest's vocabulary."""

import unittest

from concord.irrlicht import bare, translate


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

    def test_onevent_takes_its_event_by_const_reference(self):
        out = translate("namespace irr\n{\nclass C\n{\n    virtual bool OnEvent(SEvent event);\n};\n}\n", "daisy")
        self.assertIn("OnEvent(const SEvent& event)", out)

    def test_copies_and_comparisons_take_const_references(self):
        out = translate("struct S\n{\n    S(S& o);\n    bool operator <(S& other) const;\n};\n", "daisy")
        self.assertIn("S(const S& o);", out)
        self.assertIn("operator <(const S& other) const;", out)

    def test_driver_colour_and_mask_types_match_ox(self):
        out = translate("virtual E_DRIVER_TYPE getDriverType() const;\nvirtual ECOLOR_FORMAT getColorFormat() const;\nunsigned int getRedMask() const;\nint getAlphaMask() const;\n", "daisy")
        self.assertIn("virtual int getDriverType() const;", out)
        self.assertIn("virtual int getColorFormat() const;", out)
        self.assertIn("unsigned int getRedMask() const;", out)
        self.assertIn("unsigned int getAlphaMask() const;", out)

    def test_pointer_indexing_is_an_element_and_sort_is_ox_algo_sort(self):
        out = translate("int a = vec.pointer()[2];\nint b = vec.pointer()[0];\nvec.sort();\nvec.set_sorted(false);\n", "daisy")
        self.assertIn("int a = vec[2];", out)
        self.assertIn("int b = vec[0];", out)
        self.assertIn("ox::algo::sort(vec.begin(), vec.end());", out)
        self.assertNotIn("set_sorted", out)

    def test_a_two_argument_log_gets_a_level_unless_it_has_one(self):
        out = translate('os::Printer::log("msg", hint);\nos::Printer::log("msg", ELL_WARNING);\nos::Printer::log("msg", hint, ELL_ERROR);\n', "daisy")
        self.assertIn('os::Printer::log("msg", hint, ELL_INFORMATION);', out)
        self.assertIn('os::Printer::log("msg", ELL_WARNING);', out)
        self.assertIn('os::Printer::log("msg", hint, ELL_ERROR);', out)

    def test_trailing_spaces_and_tabs_are_removed_from_each_line(self):
        self.assertEqual(translate("class C  \n{\t\n    int x;\n};  \n", "daisy"), "class C\n{\n    int x;\n};\n")

    def test_nested_templates_are_spaced_and_shifts_are_not(self):
        out = translate("core::array<core::vector3df> a; value = (value >> 16);")
        self.assertIn("ox::TArray<ox::core::CVector3d<float> > a;", out)
        self.assertIn("(value >> 16)", out)

    def test_container_calls_become_vector_and_list_calls(self):
        out = translate("Vertices.set_used(4); return Indices.const_pointer(); b->Vertices.reallocate(n); core::list<T>::Iterator it;")
        self.assertIn("Vertices.resize(4);", out)
        self.assertIn("return &Indices[0];", out)
        self.assertIn("b->Vertices.reserve(n);", out)
        self.assertIn("ox::TList<T>::iterator it;", out)

    def test_a_member_qualified_inside_its_class_loses_the_qualifier(self):
        source = "class quaternion\n{\n\tquaternion quaternion::operator*(const quaternion& other) const;\n};\ninline quaternion quaternion::operator*(const quaternion& other) const\n{\n}\n"
        out = translate(source)
        self.assertIn("\tquaternion operator*(const quaternion& other) const;", out)
        self.assertIn("inline quaternion quaternion::operator*(", out)

    def test_bare_core_names_and_constructors_are_renamed_inside_the_namespace(self):
        source = "class quaternion\n{\n\tquaternion(const matrix4& mat);\n\tvector3df v;\n};\ninline quaternion::quaternion(const matrix4& mat)\n{\n}\ntypedef plane3d<f32> plane3df;\n"
        out = bare(translate(source), "ox::core::")
        self.assertIn("class CQuaternion", out)
        self.assertIn("\tCQuaternion(const CMatrix4& mat);", out)
        self.assertIn("\tCVector3d<float> v;", out)
        self.assertIn("inline CQuaternion::CQuaternion(const CMatrix4& mat)", out)
        self.assertNotIn("typedef", out)


if __name__ == "__main__":
    unittest.main()
