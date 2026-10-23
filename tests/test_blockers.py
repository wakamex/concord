"""Restoring Irrlicht's body into a stubbed method, and reducing compiler errors to causes."""

import unittest

from concord.blockers import Stub, cause, errors_in, ranked, restored

CURRENT = b"""int A::kept() const
{
    return 1;
}

void A::stubbed(int n)
{
    // Not recovered: Irrlicht 0.7's body does not compile against daisy's interfaces.
    __builtin_trap();
}
"""
IRRLICHT = b"""int A::kept() const
{
    return 2;
}

void A::stubbed(int n)
{
    Items.set_used(n);
}
"""


class Blockers(unittest.TestCase):
    def test_only_the_stubbed_body_is_restored(self):
        [(name, text)] = restored(CURRENT, IRRLICHT).items()
        self.assertEqual(name, "A::stubbed")
        self.assertIn(b"return 1;", text)
        self.assertIn(b"void A::stubbed(int n)\n{\n    Items.set_used(n);\n}", text)

    def test_errors_are_kept_only_inside_the_method(self):
        text = restored(CURRENT, IRRLICHT)["A::stubbed"]
        output = "src/d/A.cpp:3: error: other\nsrc/d/A.cpp:8: error: 'class ox::TArray<int>' has no member named 'set_used'\nsrc/d/B.h:8: error: elsewhere\n"
        self.assertEqual(errors_in(output, "d/A.cpp", text, "A::stubbed"), ["'class ox::TArray<int>' has no member named 'set_used'"])

    def test_causes_name_the_type_or_member(self):
        self.assertEqual(cause("'class ox::TArray<int>' has no member named 'set_used'"), "ox::TArray lacks set_used")
        self.assertEqual(cause("'Randomizer' has not been declared"), "Randomizer undeclared")
        self.assertEqual(cause("no matching function for call to 'ox::core::CVector3d<float>::CVector3d(int)'"), "no overload of ox::core::CVector3d::CVector3d")
        self.assertEqual(cause("invalid use of incomplete type 'struct ox::scene::IMesh'"), "ox::scene::IMesh is incomplete")

    def test_causes_rank_by_stubs_blocked(self):
        found = [
            Stub("a", "f", ["'class ox::TArray<int>' has no member named 'set_used'"]),
            Stub("a", "g", ["'class ox::TArray<int>' has no member named 'set_used'", "'Randomizer' has not been declared"]),
            Stub("b", "h", ["'Randomizer' has not been declared"]),
            Stub("b", "i", ["'X' was not declared in this scope"]),
        ]
        self.assertEqual(ranked(found)[:2], [("Randomizer undeclared", 2, 1), ("ox::TArray lacks set_used", 2, 1)])


if __name__ == "__main__":
    unittest.main()
