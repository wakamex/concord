"""Applying a definition-order winner from an hv search run."""

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.order import OrderResult, apply_order, definition_placements


class FakeHarvest:
    def __init__(self, root: Path):
        self.root = root


class DefinitionPlacements(unittest.TestCase):
    def test_moves_unchanged_bodies_only_within_the_same_namespace(self):
        source = b'namespace a {\nvoid one() {}\nvoid two() {}\nvoid three() {}\n}\nnamespace b { void other() {} }\n'
        rows = definition_placements(source, 'two')
        self.assertEqual(len(rows), 4)
        for text in rows.values():
            self.assertEqual(sorted(text.split()), sorted(source.split()))
            self.assertIn(b'namespace b { void other() {} }', text)
        self.assertLess(rows['before one'].index(b'void two'), rows['before one'].index(b'void one'))
        self.assertGreater(rows['after three'].index(b'void two'), rows['after three'].index(b'void three'))

    @unittest.skipUnless(shutil.which('g++'), 'g++ is required')
    def test_generated_cpp_placements_compile(self):
        source = b'namespace n { class A { public: static int one(); static int two(); static int three(); };\nint A::one() { return 1; }\nint A::two() { return one()+1; }\nint A::three() { return two()+1; }\n}\n'
        for text in definition_placements(source, 'A::two').values():
            result = subprocess.run(['g++', '-std=c++98', '-x', 'c++', '-fsyntax-only', '-'],
                                    input=text, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_cli_accepts_reference_source(self):
        from concord.cli import build_parser
        args = build_parser().parse_args(['definition-placement', '--harvest', '/tmp/h', '--unit', 'u.cpp',
                                         '--method', 'A::two', '--reference-source', '/tmp/b.cpp',
                                         '--save', '/tmp/report.json'])
        self.assertEqual(args.method, 'A::two')
        self.assertEqual(args.budget, 72)

    def test_refuses_missing_definition(self):
        with self.assertRaisesRegex(ValueError, 'definition not found'):
            definition_placements(b'void one() {}', 'missing')

    def test_nested_method_cannot_cross_class_scope(self):
        self.assertEqual(definition_placements(b'class A { void one() {} }; void two() {}', 'one'), {})


class ApplyOrder(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.source = b"void a() {}\nvoid b() {}\n"
        (self.root / "src").mkdir()
        (self.root / "src" / "u.cpp").write_bytes(self.source)
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "context.json").write_text(
            json.dumps({"blocks": {"source_sha256": hashlib.sha256(self.source).hexdigest()}})
        )

    def result(self, candidate: bytes) -> OrderResult:
        (self.run / "candidate.cpp").write_bytes(candidate)
        return OrderResult("u.cpp", "budget", self.run, {"a"}, {"a", "b"})

    def test_writes_a_verified_reorder(self):
        apply_order(FakeHarvest(self.root), self.result(b"void b() {}\nvoid a() {}\n"))
        self.assertEqual((self.root / "src" / "u.cpp").read_bytes(), b"void b() {}\nvoid a() {}\n")

    def test_refuses_a_candidate_that_is_not_a_reorder(self):
        with self.assertRaises(ValueError):
            apply_order(FakeHarvest(self.root), self.result(b"void b() { x(); }\nvoid a() {}\n"))

    def test_refuses_a_source_changed_since_the_search(self):
        result = self.result(b"void b() {}\nvoid a() {}\n")
        (self.root / "src" / "u.cpp").write_bytes(b"void a() {}\nvoid b() {}\nvoid c() {}\n")
        with self.assertRaises(ValueError):
            apply_order(FakeHarvest(self.root), result)

    def test_refuses_a_result_that_loses_a_function(self):
        result = self.result(b"void b() {}\nvoid a() {}\n")
        result.verified = {"b"}
        with self.assertRaises(ValueError):
            apply_order(FakeHarvest(self.root), result)


if __name__ == "__main__":
    unittest.main()
