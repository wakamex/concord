"""Restoring Irrlicht's body into a stubbed method, and reducing compiler errors to causes."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from concord import blockers
from concord.blockers import Stub, cause, errors_in, ranked, restored
from concord.harvest import Evaluation
from concord.seed import IRRLICHT as IRRLICHT_DIRECTORY

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

    def test_overloaded_stubs_are_restored_independently(self):
        current = b"void A::f(int n) { __builtin_trap(); }\nvoid A::f(float n) { __builtin_trap(); }\n"
        original = b"void A::f(int n) { useInt(n); }\nvoid A::f(float n) { useFloat(n); }\n"
        candidates = restored(current, original)
        self.assertEqual(len(candidates), 2)
        self.assertTrue(any(b"useInt(n)" in text for text in candidates.values()))
        self.assertTrue(any(b"useFloat(n)" in text for text in candidates.values()))
        self.assertTrue(all(text.count(b"__builtin_trap();") == 1 for text in candidates.values()))

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


@unittest.skipUnless(shutil.which("g++"), "g++ is needed to check restored bodies")
class Apply(unittest.TestCase):
    declarations = b"class A { public: int kept() const; void stubbed(int n); void broken(); };\n"
    current = declarations + CURRENT + b"void A::broken() { __builtin_trap(); }\n"
    original = declarations + IRRLICHT.replace(b"Items.set_used(n);", b"(void)n;") + b"void A::broken() { missing(); }\n"

    def run_trial(self, apply=False, combined_error=False, outside_error=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "src" / "d" / "A.cpp"
            path.parent.mkdir(parents=True)
            source = self.current.replace(b"return 1;", b"return missing;", 1) if outside_error else self.current
            path.write_bytes(source)
            ancestor = root / IRRLICHT_DIRECTORY / "source" / "Irrlicht" / "A.cpp"
            ancestor.parent.mkdir(parents=True)
            ancestor.write_bytes(self.original)

            def evaluate(unit, candidates):
                self.assertEqual(unit, "d/A.cpp")
                results = {}
                for name, text in candidates.items():
                    compile_result = subprocess.run(["g++", "-std=c++98", "-x", "c++", "-fsyntax-only", "-"], input=text, capture_output=True, check=False)
                    error = compile_result.stderr.decode() if compile_result.returncode else ""
                    if name == "combined" and combined_error:
                        error = "combined candidate rejected"
                    results[name] = Evaluation(name, unit, error=error)
                return results

            harvest = SimpleNamespace(root=root, evaluate=evaluate)
            with mock.patch.object(blockers, "qualify", side_effect=lambda root, text, unit: text):
                if combined_error:
                    with self.assertRaisesRegex(RuntimeError, "combined restored bodies"):
                        blockers.stubs(harvest, ["d/A.cpp"], apply=True)
                    self.assertEqual(path.read_bytes(), source)
                    return
                found = blockers.stubs(harvest, ["d/A.cpp"], apply=apply)
            return found, path.read_bytes(), source

    def test_apply_changes_only_the_compiling_stub_body(self):
        found, text, source = self.run_trial(apply=True)
        self.assertEqual({s.name for s in found if s.applied}, {"A::stubbed"})
        self.assertEqual(text, restored(source, self.original)["A::stubbed"])
        self.assertTrue(next(s for s in found if s.name == "A::broken").errors)

    def test_diagnosis_does_not_write(self):
        found, text, source = self.run_trial()
        self.assertEqual(text, source)
        self.assertFalse(any(s.applied for s in found))

    def test_combined_compile_failure_leaves_source_untouched(self):
        self.run_trial(combined_error=True)

    def test_failure_outside_the_restored_body_is_not_reported_as_compiling(self):
        found, text, source = self.run_trial(apply=True, outside_error=True)
        self.assertTrue(all(s.errors for s in found))
        self.assertEqual(text, source)


if __name__ == "__main__":
    unittest.main()
