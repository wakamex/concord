"""Translating Irrlicht 0.7 source into Harvest's vocabulary."""

import unittest

from concord.irrlicht import bare, translate


class Translate(unittest.TestCase):
    def test_maya_post_render_sets_the_base_position(self):
        source = "void CCameraMayaSceneNode::OnPostRender(u32 timeMs)\n{\n    animate();\n    setPosition(Pos);\n    updateAbsolutePosition();\n}\n"
        out = translate(source)
        self.assertIn("ISceneNode::setPosition(Pos);", out)
        self.assertIn("updateAbsolutePosition();", out)
        self.assertEqual(translate(out), out)
        self.assertEqual(translate("setPosition(Pos);"), "setPosition(Pos);")

    def test_mesh_destructor_precedes_constructor_with_its_comment(self):
        source = (
            "//! constructor\nCMeshSceneNode::CMeshSceneNode()\n{\n}\n\n"
            "//! destructor\nCMeshSceneNode::~CMeshSceneNode()\n{\n    Mesh->drop();\n}\n"
        )
        out = translate(source)
        self.assertTrue(out.startswith("//! destructor\nCMeshSceneNode::~CMeshSceneNode()"))
        self.assertIn("//! constructor\nCMeshSceneNode::CMeshSceneNode()", out)
        self.assertEqual(out.count("Mesh->drop();"), 1)
        self.assertEqual(translate(out), out)

    def test_particle_buffers_reserve_before_simulation(self):
        source = (
            "void CParticleSystemSceneNode::doParticleSystem(u32 time)\n{\n}\n\n"
            "void CParticleSystemSceneNode::reallocateBuffers()\n{\n"
            "    Vertices.set_used(Particles.size() * 4);\n"
            "    Indices.set_used(Particles.size() * 6);\n}\n"
        )
        out = translate(source)
        self.assertLess(out.index("::reallocateBuffers"), out.index("::doParticleSystem"))
        self.assertIn("Vertices.reserve(Particles.size() * 4);", out)
        self.assertIn("Indices.reserve(Particles.size() * 6);", out)
        self.assertEqual(out.count("::reallocateBuffers"), 1)
        self.assertIn("other.resize(3)", translate("other.set_used(3)"))

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

    def test_erase_by_index_becomes_erase_by_iterator_and_iterators_stay(self):
        out = translate("node->Triangles.erase(i);\nlist.erase(it);\nlist.erase(ait);\n", "daisy")
        self.assertIn("node->Triangles.erase(node->Triangles.begin() + i);", out)
        self.assertIn("list.erase(it);", out)
        self.assertIn("list.erase(ait);", out)

    def test_a_two_argument_log_gets_a_level_unless_it_has_one(self):
        out = translate('os::Printer::log("msg", hint);\nos::Printer::log("msg", ELL_WARNING);\nos::Printer::log("msg", hint, ELL_ERROR);\n', "daisy")
        self.assertIn('os::Printer::log("msg", hint, ELL_INFORMATION);', out)
        self.assertIn('os::Printer::log("msg", ELL_WARNING);', out)
        self.assertIn('os::Printer::log("msg", hint, ELL_ERROR);', out)

    def test_trailing_spaces_and_tabs_are_removed_from_each_line(self):
        self.assertEqual(translate("class C  \n{\t\n    int x;\n};  \n", "daisy"), "class C\n{\n    int x;\n};\n")

    def test_typedef_names_behind_irr_are_resolved_and_the_irrlicht_spellings_are_ox_names(self):
        out = translate("irr::u16 a = 0;\nint f = EVDF_BILINEAR_FILER;\n", "daisy")
        self.assertIn("unsigned short a = 0;", out)
        self.assertIn("EVDF_BILINEAR_FILTER;", out)

    def test_packing_a_colour_is_the_ox_helper_on_its_colour_member(self):
        out = translate("s16 a = color.toA1R5G5B5();\ns16 b = currentVertex->Color.toA1R5G5B5();\ns16 c = imageToCopy->getPixel(x,y).toA1R5G5B5();\n", "daisy")
        self.assertIn("ox::video::toA1R5G5B5(color.color)", out)
        self.assertIn("ox::video::toA1R5G5B5(currentVertex->Color.color)", out)
        self.assertIn("ox::video::toA1R5G5B5(imageToCopy->getPixel(x,y).color)", out)

    def test_a_colour_format_is_cast_from_the_int_ox_returns(self):
        out = translate("ECOLOR_FORMAT f = image->getColorFormat();\nswitch(Mesh->getMeshBuffer(b)->getVertexType())\nswitch(vertexType)\n", "daisy")
        self.assertIn("ECOLOR_FORMAT f = (ECOLOR_FORMAT)image->getColorFormat();", out)
        self.assertIn("switch((int)Mesh->getMeshBuffer(b)->getVertexType())", out)
        self.assertIn("switch((int)vertexType)", out)

    def test_clip_against_is_written_out_as_corner_comparisons(self):
        out = translate("void f(core::rect<s32>& r, core::rect<s32> c)\n{\n    r.clipAgainst(c);\n}\n", "daisy")
        self.assertIn("if ((c).LowerRightCorner.X < r.LowerRightCorner.X) r.LowerRightCorner.X = (c).LowerRightCorner.X;", out)
        self.assertIn("if ((c).UpperLeftCorner.Y > r.UpperLeftCorner.Y) r.UpperLeftCorner.Y = (c).UpperLeftCorner.Y;", out)
        self.assertNotIn("clipAgainst", out)

    def test_nested_templates_are_spaced_and_shifts_are_not(self):
        out = translate("core::array<core::vector3df> a; value = (value >> 16);")
        self.assertIn("ox::TArray<ox::core::CVector3d<float> > a;", out)
        self.assertIn("(value >> 16)", out)

    def test_container_calls_become_vector_and_list_calls(self):
        out = translate(
            "Vertices.set_used(4); return Indices.const_pointer(); b->Vertices.reallocate(n); core::list<T>::Iterator it;\n"
            "(*indices)[i].Indices.erase(t, 3);\n"
        )
        self.assertIn("Vertices.resize(4);", out)
        self.assertIn("return &Indices[0];", out)
        self.assertIn("b->Vertices.reserve(n);", out)
        self.assertIn("ox::TList<T>::iterator it;", out)
        self.assertIn(
            "(*indices)[i].Indices.erase((*indices)[i].Indices.begin() + (int)t, (*indices)[i].Indices.begin() + (int)(t + 3));",
            out,
        )

    def test_container_searches_become_the_ox_algo_helpers(self):
        out = translate(
            "s32 i = Meshes.binary_search(e);\n"
            "s32 w = mesh.SkinWeights[k].Weights.binary_search(CXFileReader::SXWeight(idx));\n"
            "s32 l = list.linear_search(x);\n"
            "s32 n = buf->Vertices.linear_reverse_search(v);\n",
            "daisy",
        )
        self.assertIn("int i = ox::algo::binarySearchPos(Meshes.begin(), Meshes.end(), e);", out)
        self.assertIn(
            "ox::algo::binarySearchPos(mesh.SkinWeights[k].Weights.begin(), mesh.SkinWeights[k].Weights.end(), CXFileReader::SXWeight(idx))",
            out,
        )
        self.assertIn("int l = ox::algo::linearSearchPos(list.begin(), list.end(), x);", out)
        self.assertIn("int n = ox::algo::linearSearchPos(buf->Vertices.begin(), buf->Vertices.end(), v);", out)

    def test_shared_header_helpers_expand_at_the_call_site(self):
        out = translate(
            "vtx.Color.set(255, 255, 255, 255);\n"
            "mat.DiffuseColor = xmat.FaceColor.toSColor();\n"
            "Pos.rotateXYBy(nRotY, Target);\n"
            "Pos.rotateXZBy(-nRotX, Target);\n"
            "p.vector.rotateYZBy((os::Randomizer::rand()%40)-20, core::vector3df(0,0,0));\n"
            "core::vector3df extend = Box.getExtent();\n"
            "particles[i].vector = particles[i].startVector.getInterpolated(Gravity, f);\n"
            "Font->draw(Text.c_str(), r, Color, true, true);\n"
            "joint.LocalAnimatedMatrix *= mat1.interpolate(mat2, factor);\n"
            'os::Printer::log("bad", file->getFileName(), irr::ELL_WARNING);\n',
            "daisy",
        )
        self.assertIn("vtx.Color = ox::video::SColor(255, 255, 255, 255);", out)
        self.assertIn("mat.DiffuseColor = ox::video::SColor((int)(xmat.FaceColor.a*255.0f),", out)
        self.assertIn("double deg = (nRotY) * ox::core::GRAD_PI2;", out)
        self.assertIn("double deg = (-nRotX) * ox::core::GRAD_PI2;", out)
        self.assertIn("double deg = ((ox::algo::CRand::rand()%40)-20) * 0.017453290522098541;", out)
        self.assertIn("ox::core::CVector3d<float> extend = ((Box).MaxEdge - (Box).MinEdge);", out)
        self.assertIn("ox::core::CVector3d<float>((Gravity).X*(1.0f - (f)) + particles[i].startVector.X*(f),", out)
        self.assertIn("Font->draw(Text.c_str(), r, Color, ox::gui::EFHA_CENTER, ox::gui::EFVA_CENTER, 0);", out)
        self.assertIn("joint.LocalAnimatedMatrix *= _concord_interpolate(mat1, mat2, factor);", out)
        self.assertIn("ELL_WARNING);", out)
        self.assertNotIn("daisy::ELL_WARNING", out)

    def test_key_input_pressed_down_assignment_expands_to_event_branches(self):
        out = translate(
            "bool CCameraFPSSceneNode::OnEvent(SEvent event)\n"
            "{\n"
            "\tif (KeyMap[i].keycode == event.KeyInput.Key)\n"
            "\t{\n"
            "\t\tCursorKeys[KeyMap[i].action] = event.KeyInput.PressedDown;\n"
            "\t\treturn true;\n"
            "\t}\n"
            "\treturn false;\n"
            "}\n\n"
            "void CCameraFPSSceneNode::setTarget(const core::vector3df& wtgt)\n"
            "{\n"
            "}\n",
            "daisy",
        )
        self.assertIn("if (event.KeyInput.Event == ox::event::EKIE_KEY_PRESSED_DOWN)", out)
        self.assertIn("else if (event.KeyInput.Event == ox::event::EKIE_KEY_LEFT_UP)", out)

    def test_a_container_iterated_while_const_gets_a_const_iterator(self):
        out = translate(
            "const core::list<ISceneNode*>& list = start->getChildren();\n"
            "core::list<ISceneNode*>::Iterator it = list.begin();\n"
            "core::list<ISceneNode*>::ConstIterator end = list.end();\n"
            "core::list<ISceneNode*>::Iterator rest = other.begin();\n"
            "for (; it != list.end(); ++it)\n    f(*it);\n",
            "daisy",
        )
        self.assertIn("ox::TList<ISceneNode*>::const_iterator it = list.begin();", out)
        self.assertIn("ox::TList<ISceneNode*>::const_iterator end = list.end();", out)
        self.assertIn("ox::TList<ISceneNode*>::iterator rest = other.begin();", out)
        self.assertIn("it != list.end();", out)

    def test_registering_for_rendering_names_the_time_irrlicht_defaulted(self):
        out = translate(
            "if (IsVisible)\n    SceneManager->registerNodeForRendering(this);\n"
            "SceneManager->registerNodeForRendering(this, SNRT_LIGHT_AND_CAMERA);\n",
            "daisy",
        )
        self.assertIn("SceneManager->registerNodeForRendering(this, SNRT_DEFAULT);", out)
        self.assertIn("SceneManager->registerNodeForRendering(this, SNRT_LIGHT_AND_CAMERA);", out)

    def test_irrlicht_s_math_constants_header_is_ox_s(self):
        out = translate('#include "irrMath.h"\n#include "irrTypes.h"\nconst f32 PI = 3.14159f;\n', "daisy")
        self.assertIn('#include "ox/core/irrMath.h"', out)
        self.assertIn("const float PI = 3.14159274f;", out)

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


    def test_camera_on_pre_render_updates_position_and_sets_projection_after_view_area(self):
        source = (
            "void CCameraSceneNode::OnPreRender()\n{\n"
            "\tvideo::IVideoDriver* driver = SceneManager->getVideoDriver();\n"
            "\tif (!driver)\n\t\treturn;\n\n"
            "\tif (SceneManager->getActiveCamera() == this)\n\t{\n"
            "\t\tscreenDim.Width = (f32)driver->getScreenSize().Width;\n"
            "\t\tscreenDim.Height = (f32)driver->getScreenSize().Height;\n\n"
            "\t\tdriver->setTransform(video::ETS_PROJECTION, Projection);\n\n"
            "\t\tView.buildCameraLookAtMatrixLH(pos, Target, up);\n"
            "\t\trecalculateViewArea();\n\n"
            "\t\tSceneManager->registerNodeForRendering(this, SNRT_LIGHT_AND_CAMERA);\n\t}\n}\n"
        )
        out = translate(source, "daisy")
        self.assertIn("if (!driver)\n\t\treturn;\n\n\tupdateAbsolutePosition();\n\n\tif (SceneManager->getActiveCamera() == this)", out)
        self.assertIn("recalculateViewArea();\n\t\tdriver->setTransform(video::ETS_PROJECTION, Projection);\n\n\t\tSceneManager->registerNodeForRendering", out)


if __name__ == "__main__":
    unittest.main()
