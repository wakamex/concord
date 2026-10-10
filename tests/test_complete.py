"""Completing Harvest's ox core types with Irrlicht's members."""

import unittest

from concord.complete import _indent, _members, _rename, missing_members

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

    def test_every_overload_is_taken_and_translated(self):
        members = [_indent(_rename(m)) for m in _members(AABBOX, "aabbox3d", "reset")]
        self.assertEqual(len(members), 2)
        self.assertEqual(members[0], "    void reset(T x, T y, T z)\n    {\n        MaxEdge.set(x,y,z);\n        MinEdge = MaxEdge;\n    }")
        self.assertIn("void reset(const CVector3d<T>& initValue)", members[1])


if __name__ == "__main__":
    unittest.main()
