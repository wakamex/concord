"""Restoring Irrlicht's body into a stubbed method, and reducing compiler errors to causes."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from concord import blockers
from concord.blockers import Stub, _reused_helpers, cause, errors_in, ranked, restored
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

    def test_refresh_can_replace_a_selected_existing_body_without_reseeding(self):
        candidates = restored(CURRENT, IRRLICHT, stubs_only=False)
        self.assertEqual(set(candidates), {'A::kept', 'A::stubbed'})
        self.assertIn(b'return 2;', candidates['A::kept'])
        self.assertIn(b'__builtin_trap();', candidates['A::kept'])
        self.assertIn(b'return 1;', candidates['A::stubbed'])

    def test_overloaded_stubs_are_restored_independently(self):
        current = b"void A::f(int n) { __builtin_trap(); }\nvoid A::f(float n) { __builtin_trap(); }\n"
        original = b"void A::f(int n) { useInt(n); }\nvoid A::f(float n) { useFloat(n); }\n"
        candidates = restored(current, original)
        self.assertEqual(len(candidates), 2)
        self.assertTrue(any(b"useInt(n)" in text for text in candidates.values()))
        self.assertTrue(any(b"useFloat(n)" in text for text in candidates.values()))
        self.assertTrue(all(text.count(b"__builtin_trap();") == 1 for text in candidates.values()))

    def test_the_restored_unit_carries_the_translations_new_includes_and_helpers(self):
        current = (
            b'#include "daisy/os.h"\n'
            b"namespace daisy {\nvoid A::stubbed(int n)\n{\n    __builtin_trap();\n}\n}\n"
        )
        irrlicht = (
            b'#include "os.h"\n#include "ox/algo/CArrayFunctions.h"\n'
            b"inline int _concord_helper(int n)\n{\n\treturn n;\n}\n"
            b"namespace daisy {\nvoid A::stubbed(int n)\n{\n    go();\n}\n}\n"
        )
        [text] = restored(current, irrlicht, {"_concord_helper"}).values()
        self.assertIn(b'#include "daisy/os.h"\n#include "ox/algo/CArrayFunctions.h"', text)
        self.assertNotIn(b'#include "os.h"\n#', text)  # satisfied by the relocated daisy/os.h
        self.assertIn(b"inline int _concord_helper(int n)", text)
        self.assertLess(text.index(b"_concord_helper"), text.index(b"namespace daisy"))

    def test_a_numbered_helper_the_unit_already_has_is_reused(self):
        current = (
            b"inline int inner(int n)\n{\n\treturn n;\n}\n"
            b"inline int outer(int n)\n{\n\treturn inner(n) + inner(n);\n}\n"
        )
        translation = (
            b"inline int inner2(int n)\n{\n\treturn n;\n}\n"
            b"inline int outer2(int n)\n{\n\treturn inner2(n) + inner2(n);\n}\n"
            b"void A::f()\n{\n\touter2(1);\n}\n"
        )
        text, names = _reused_helpers(
            current, translation, {"_concord_inner": "inner2", "_concord_outer": "outer2"}
        )
        self.assertEqual(names, {"_concord_inner": "inner", "_concord_outer": "outer"})
        self.assertIn(b"outer(1);", text)
        self.assertNotIn(b"2", text)

    def test_a_helper_differing_from_the_units_keeps_its_numbered_name(self):
        current = b"inline int helper(int n)\n{\n\treturn n + 1;\n}\n"
        translation = b"inline int helper2(int n)\n{\n\treturn n;\n}\nvoid A::f()\n{\n\thelper2(1);\n}\n"
        text, names = _reused_helpers(current, translation, {"_concord_helper": "helper2"})
        self.assertEqual(names, {"_concord_helper": "helper2"})
        self.assertEqual(text, translation)

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

    def run_trial(self, apply=False, combined_error=False, outside_error=False, current=None, original=None, only=None, refresh=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "src" / "d" / "A.cpp"
            path.parent.mkdir(parents=True)
            source = current if current is not None else self.current
            if outside_error:
                source = source.replace(b"return 1;", b"return missing;", 1)
            path.write_bytes(source)
            ancestor = root / IRRLICHT_DIRECTORY / "source" / "Irrlicht" / "A.cpp"
            ancestor.parent.mkdir(parents=True)
            ancestor.write_bytes(original if original is not None else self.original)

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
                found = blockers.stubs(harvest, ["d/A.cpp"], apply=apply, only=only, refresh=refresh)
            return found, path.read_bytes(), source

    def test_apply_changes_only_the_compiling_stub_body(self):
        found, text, source = self.run_trial(apply=True)
        self.assertEqual({s.name for s in found if s.applied}, {"A::stubbed"})
        self.assertEqual(text, restored(source, self.original)["A::stubbed"])
        self.assertTrue(next(s for s in found if s.name == "A::broken").errors)

    def test_refresh_compiles_and_applies_only_the_selected_existing_body(self):
        found, text, source = self.run_trial(apply=True, only={'A::kept'}, refresh=True)
        self.assertEqual([s.name for s in found if s.applied], ['A::kept'])
        self.assertEqual(text, restored(source, self.original, stubs_only=False)['A::kept'])
        self.assertEqual(text.count(b'__builtin_trap();'), 2)

    def test_selected_restore_leaves_other_compiling_stubs_untouched(self):
        original = self.original.replace(b'missing();', b'(void)0;')
        found, text, source = self.run_trial(apply=True, original=original, only={'A::broken'})
        self.assertEqual([s.name for s in found], ['A::broken'])
        self.assertTrue(found[0].applied)
        self.assertEqual(text, restored(source, original)['A::broken'])

    def test_empty_selection_changes_nothing(self):
        found, text, source = self.run_trial(apply=True, only=set())
        self.assertEqual(found, [])
        self.assertEqual(text, source)

    def test_apply_adds_the_includes_a_restored_body_needs(self):
        current = b"class A { public: void stubbed(); };\nvoid A::stubbed() { __builtin_trap(); }\n"
        original = b'#include <vector>\nclass A { public: void stubbed(); };\nvoid A::stubbed() { std::vector<int> v; (void)v; }\n'
        found, text, source = self.run_trial(apply=True, current=current, original=original)
        self.assertEqual({s.name for s in found if s.applied}, {"A::stubbed"})
        self.assertEqual(text, restored(source, original)["A::stubbed"])
        self.assertIn(b"#include <vector>", text)

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


class RefreshHeader(unittest.TestCase):
    @unittest.skipUnless(shutil.which('g++'), 'g++ required')
    def test_inline_constructor_overlay_is_compiled_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'src/d/A.h'
            path.parent.mkdir(parents=True)
            current = b'struct A { A(int n) { __builtin_trap(); } int value; };\n'
            path.write_bytes(current)
            ancestor = root / IRRLICHT_DIRECTORY / 'source/Irrlicht/A.h'
            ancestor.parent.mkdir(parents=True)
            ancestor.write_bytes(b'struct A { A(int n) { value = n; } int value; };\n')

            def evaluate(rows):
                unit, overlay = rows['header']
                self.assertEqual(unit, 'd/A.cpp')
                text = overlay['src/d/A.h'] + b'int main() { A a(7); return a.value != 7; }'
                binary = root / 'test'
                result = subprocess.run(['g++', '-std=c++98', '-x', 'c++', '-', '-o', str(binary)], input=text, capture_output=True, check=False)
                if result.returncode == 0:
                    self.assertEqual(subprocess.run([str(binary)], check=False).returncode, 0)
                return {'header': Evaluation('header', unit, error=result.stderr.decode() if result.returncode else '')}

            harvest = SimpleNamespace(root=root, evaluate_overlays=evaluate)
            with mock.patch.object(blockers, 'qualify', side_effect=lambda root, text, unit: text):
                row, = blockers.refresh_header(harvest, 'd/A.cpp', 'd/A.h', {'A'}, apply=False)
                self.assertFalse(row.errors)
                self.assertEqual(path.read_bytes(), current)
                row, = blockers.refresh_header(harvest, 'd/A.cpp', 'd/A.h', {'A'}, apply=True)
                self.assertTrue(row.applied)
                self.assertIn(b'value = n;', path.read_bytes())
                accepted = path.read_bytes()
                ancestor.write_bytes(b'struct A { A(int n) { missing(n); } int value; };\n')
                row, = blockers.refresh_header(harvest, 'd/A.cpp', 'd/A.h', {'A'}, apply=True)
                self.assertTrue(row.errors)
                self.assertFalse(row.applied)
                self.assertEqual(path.read_bytes(), accepted)
