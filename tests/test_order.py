"""Applying a definition-order winner from an hv search run."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from concord.order import OrderResult, apply_order


class FakeHarvest:
    def __init__(self, root: Path):
        self.root = root


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
