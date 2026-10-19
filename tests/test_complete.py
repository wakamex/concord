"""Completing Harvest's ox core types with Irrlicht's members."""

import tempfile
import unittest
from pathlib import Path

from concord.complete import (
    _indent,
    _members,
    _rename,
    absent,
    complete,
    complete_enum,
    missing_members,
    settle,
    shared_enums,
    undeclared,
    worse,
)

AABBOX = """namespace irr { namespace core {
template <class T> class aabbox3d
{
public:
	aabbox3d() {}
	void reset(T x, T y, T z)
	{
		MaxEdge.set(x,y,z);
		MinEdge = MaxEdge;
	}
	void reset(const vector3d<T>& initValue)
	{
		MaxEdge = initValue;
	}
	vector3d<T> MinEdge;
	vector3d<T> MaxEdge;
};
} }
"""


class Complete(unittest.TestCase):
    def test_compiler_reports_name_the_type_and_member(self):
        output = (
            "x.cpp:3: error: 'struct ox::core::CAabbox3d<float>' has no member named 'reset'\n"
            "x.cpp:4: error: 'class ox::TArray<int>' has no member named 'set_used'\n"
            "x.cpp:5: error: 'const class ox::core::CVector3d<float>' has no member named 'getInterpolated'\n"
        )
        self.assertEqual(missing_members(output), {("CAabbox3d", "reset"), ("CVector3d", "getInterpolated")})

    def test_an_unmatched_constructor_call_names_the_irrlicht_class(self):
        output = "x.h:161: error: no matching function for call to 'ox::core::CAabbox3d<float>::CAabbox3d(ox::core::CVector3d<float>&)'\n"
        self.assertEqual(missing_members(output), {("CAabbox3d", "aabbox3d")})

    def test_every_overload_is_taken_and_translated(self):
        members = [_indent(_rename(m, "ox::core::")) for m in _members(AABBOX, "aabbox3d", "reset")]
        self.assertEqual(len(members), 2)
        self.assertEqual(members[0], "    void reset(T x, T y, T z)\n    {\n        MaxEdge.set(x,y,z);\n        MinEdge = MaxEdge;\n    }")
        self.assertIn("void reset(const CVector3d<T>& initValue)", members[1])


MATRIX = """namespace irr { namespace core {
	class matrix4
	{
		public:
			matrix4();
			void makeIdentity();
			vector3df matrix4::getTranslation() const;
			void transformVect( vector3df& vect) const;
			f32 M[16];
	};

	inline matrix4::matrix4() { makeIdentity(); }

	inline vector3df matrix4::getTranslation() const
	{
		return vector3df(M[12], M[13], M[14]);
	}

	inline void matrix4::transformVect( vector3df& vect) const
	{
		vect.X = M[0];
	}
} }
"""

OX_MATRIX = """#ifndef OX_CORE_CMATRIX4_H
#define OX_CORE_CMATRIX4_H
namespace ox {
namespace core {

class CMatrix4
{
public:
    CMatrix4();
    void makeIdentity();
    float M[16];
};

inline CMatrix4::CMatrix4() { makeIdentity(); }

} // end namespace core
} // end namespace ox

#endif
"""


class DefinedAfterTheClass(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "src/ox/core").mkdir(parents=True)
        (self.root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
        self.header = self.root / "src/ox/core/CMatrix4.h"
        self.header.write_text(OX_MATRIX)
        (self.root / "third_party/irrlicht-0.7/include/matrix4.h").write_text(MATRIX)

    def test_absent_members_include_declared_ones(self):
        self.assertEqual(absent(self.root, "CMatrix4"), ["getTranslation", "transformVect"])

    def test_the_declaration_goes_in_the_class_and_the_definition_after_it(self):
        self.assertEqual(complete(self.root, {("CMatrix4", "getTranslation")}), ["ox/core/CMatrix4.h"])
        text = self.header.read_text()
        self.assertIn("public:\n    CVector3d<float> getTranslation() const;\n", text)
        body = "inline CVector3d<float> CMatrix4::getTranslation() const\n{\n    return CVector3d<float>(M[12], M[13], M[14]);\n}\n"
        self.assertIn(body + "\n} // end namespace core", text)
        self.assertEqual(absent(self.root, "CMatrix4"), ["transformVect"])


OX_AABBOX = """namespace ox { namespace core {
template <class T>
struct CAabbox3d
{
    CAabbox3d() {}
    bool operator == (const CAabbox3d<T>& other) const { return true; }
    CVector3d<T> MinEdge;
};
} }
"""


IRR_MESH = """namespace irr
{
namespace scene
{
\tenum E_ANIMATED_MESH_TYPE
\t{
\t\t//! Unknown animated mesh type.
\t\tEAMT_UNKNOWN = 0,
\t\tEAMT_MD2,
\t};

\tclass IAnimatedMesh : public IUnknown
\t{
\t};
}
}
"""

OX_MESH = """// Adapted from Irrlicht 0.7 include/IAnimatedMesh.h (license: third_party/irrlicht-0.7/include/irrlicht.h).

namespace ox {
namespace scene {

class IMesh;

class IAnimatedMesh : public IUnknown
{
};

} // end namespace scene
} // end namespace ox
"""


class Enums(unittest.TestCase):
    def test_an_undeclared_enum_or_enumerator_comes_from_the_header_s_irrlicht_original(self):
        self.assertEqual(
            undeclared("a.h:9: error: 'E_ANIMATED_MESH_TYPE' does not name a type\na.h:3: error: 'EAMT_MD2' was not declared in this scope\n"),
            {"E_ANIMATED_MESH_TYPE", "EAMT_MD2"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/scene").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include/IAnimatedMesh.h").write_text(IRR_MESH)
            header = root / "src/ox/scene/IAnimatedMesh.h"
            header.write_text(OX_MESH)
            self.assertEqual(complete_enum(root, "EAMT_MD2"), "ox/scene/IAnimatedMesh.h")
            text = header.read_text()
            self.assertIn("class IMesh;\n\nenum E_ANIMATED_MESH_TYPE\n{\n    //! Unknown animated mesh type.\n    EAMT_UNKNOWN = 0,\n    EAMT_MD2,\n};\n\nclass IAnimatedMesh", text)
            self.assertIsNone(complete_enum(root, "E_ANIMATED_MESH_TYPE"))  # already there
            self.assertIsNone(complete_enum(root, "ELL_ERROR"))
            header.write_text(OX_MESH)
            (root / "src/ox/scene/Elsewhere.h").write_text("enum E_ANIMATED_MESH_TYPE { EAMT_UNKNOWN };\n")
            self.assertIsNone(complete_enum(root, "EAMT_MD2"))  # defined in another ox header


class EnumsByFileName(unittest.TestCase):
    IRR_XML = """namespace irr {
namespace io {
class IXMLReader
{
public:
    enum EXML_NODE
    {
        EXN_NONE,
        EXN_ELEMENT
    };
    virtual bool read() = 0;
};
}
}
"""

    def test_a_header_without_an_origin_comment_takes_its_enum_from_the_irrlicht_header_of_the_same_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/io").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include/IXMLReader.h").write_text(self.IRR_XML)
            header = root / "src/ox/io/IXMLReader.h"
            header.write_text("namespace ox {\nnamespace io {\nclass IXMLReader : public IUnknown\n{\n};\n}\n}\n")
            self.assertEqual(complete_enum(root, "EXML_NODE"), "ox/io/IXMLReader.h")
            self.assertIn("enum EXML_NODE", header.read_text())


class FakeHarvest:
    """Two units include the header. `sensitive` is exact only when the header's
    member count is a multiple of 3; `steady` always is."""

    def __init__(self, root):
        self.root = root

    def dependents(self, path):
        return ["sensitive.cpp", "steady.cpp"]

    def count(self, overlays):
        text = overlays.get("src/ox/core/CAabbox3d.h", (self.root / "src/ox/core/CAabbox3d.h").read_bytes()).decode()
        return text.count("    void ") + text.count("    bool ")


class Settle(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "src/ox/core").mkdir(parents=True)
        (self.root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
        self.header = self.root / "src/ox/core/CAabbox3d.h"
        self.header.write_text(OX_AABBOX)
        irrlicht = AABBOX.replace("\taabbox3d() {}", "\taabbox3d() {}\n\tbool operator==(const aabbox3d<T>& o) const { return true; }\n\tvoid repair() {}\n\tbool isEmpty() const { return true; }")
        (self.root / "third_party/irrlicht-0.7/include/aabbox3d.h").write_text(irrlicht)

    def harvest(self, period):
        import concord.complete as module

        harvest = FakeHarvest(self.root)

        def state(_, units, overlays=None):
            count = harvest.count(overlays or {})
            rows = {("sensitive.cpp", "f"): (count % period == 1, 100.0), ("steady.cpp", "g"): (True, 100.0)}
            return {key: value for key, value in rows.items() if key[0] in units}

        original = module.state
        module.state = state
        self.addCleanup(setattr, module, "state", original)
        return harvest

    def test_absent_members_ignore_operator_spacing_and_keep_irrlicht_order(self):
        self.assertEqual(absent(self.root, "CAabbox3d"), ["repair", "isEmpty", "reset"])

    def test_a_lower_score_a_lost_match_and_a_missing_function_are_worse(self):
        before = {("a", "f"): (True, 100.0), ("b", "g"): (False, 90.0), ("c", "h"): (False, 50.0), ("d", "i"): (True, 100.0)}
        after = {("a", "f"): (False, 100.0), ("b", "g"): (False, 80.0), ("d", "i"): (True, 100.0)}
        self.assertEqual(worse(before, after), {"a", "b", "c"})
        self.assertEqual(worse(before, before), set())

    def test_further_members_restore_a_function_that_depends_on_the_count(self):
        harvest = self.harvest(period=3)  # exact with 1 member (as it stands) or 4
        complete(self.root, {("CAabbox3d", "reset")})  # two overloads: 3 members
        self.assertTrue(settle(harvest, {"ox/core/CAabbox3d.h": OX_AABBOX}))
        self.assertIn("void repair()", self.header.read_text())
        self.assertNotIn("isEmpty", self.header.read_text())

    def test_an_overload_the_header_has_is_not_added_again(self):
        self.header.write_text(OX_AABBOX.replace("    CVector3d<T> MinEdge;", "    void reset(T x, T y, T z) {}\n    CVector3d<T> MinEdge;"))
        complete(self.root, {("CAabbox3d", "reset")})
        text = self.header.read_text()
        self.assertEqual(text.count("void reset(T x, T y, T z)"), 1)
        self.assertIn("void reset(const CVector3d<T>& initValue)", text)

    def test_settle_fails_when_no_count_within_reach_suits(self):
        harvest = self.harvest(period=31)
        complete(self.root, {("CAabbox3d", "reset")})
        self.assertFalse(settle(harvest, {"ox/core/CAabbox3d.h": OX_AABBOX}))


class SharedEnums(unittest.TestCase):
    FULL = """namespace ox {
namespace scene {

enum EMD2_ANIMATION_TYPE
{
    EMAT_STAND = 0,
    EMAT_RUN,
};

}
}
"""
    PLACEHOLDER = """#include "ISceneNode.h"

namespace ox {
namespace scene {

//! Animation types of MD2 meshes; the enumerators are not recovered.
enum EMD2_ANIMATION_TYPE
{
};

class IAnimatedMeshSceneNode : public ISceneNode
{
};

}
}
"""

    def test_a_placeholder_enum_becomes_an_include_of_the_header_that_defines_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scene = root / "src" / "ox" / "scene"
            scene.mkdir(parents=True)
            (scene / "IAnimatedMeshMD2.h").write_text(self.FULL)
            (scene / "IAnimatedMeshSceneNode.h").write_text(self.PLACEHOLDER)
            self.assertEqual(shared_enums(root), ["ox/scene/IAnimatedMeshSceneNode.h"])
            text = (scene / "IAnimatedMeshSceneNode.h").read_text()
            self.assertEqual(
                text,
                '#include "ISceneNode.h"\n#include "IAnimatedMeshMD2.h"\n\nnamespace ox {\nnamespace scene {\n\nclass IAnimatedMeshSceneNode : public ISceneNode\n{\n};\n\n}\n}\n',
            )
            self.assertEqual((scene / "IAnimatedMeshMD2.h").read_text(), self.FULL)
            self.assertEqual(shared_enums(root), [])  # idempotent

    def test_an_enum_with_enumerators_in_two_headers_is_left_for_the_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scene = root / "src" / "ox" / "scene"
            scene.mkdir(parents=True)
            (scene / "A.h").write_text(self.FULL)
            (scene / "B.h").write_text(self.FULL)
            self.assertEqual(shared_enums(root), [])


if __name__ == "__main__":
    unittest.main()
