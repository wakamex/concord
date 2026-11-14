import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.harvest import Evaluation
from concord.history import at_commit, tree_at


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


class Repository(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "test")
        (self.root / "include/Gruntz").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "include/Gruntz/State.h").write_bytes(b"old\n")
        (self.root / "src/a.cpp").write_bytes(b"#include <Gruntz/State.h>\n")
        git(self.root, "add", "include", "src")
        git(self.root, "commit", "-q", "-m", "one")
        self.first = git(self.root, "rev-parse", "HEAD")
        (self.root / "include/Gruntz/State.h").write_bytes(b"new\n")
        (self.root / "include/Gruntz/Extra.h").write_bytes(b"\0binary\xff")
        git(self.root, "add", "include")
        git(self.root, "commit", "-q", "-m", "two")

    def tearDown(self):
        self.directory.cleanup()

    def test_tree_at_reads_each_file_as_it_was_including_binary_bytes(self):
        self.assertEqual(tree_at(self.root, self.first, ["include"]), {"include/Gruntz/State.h": b"old\n"})
        now = tree_at(self.root, "HEAD", ["include", "src/a.cpp"])
        self.assertEqual(now["include/Gruntz/Extra.h"], b"\0binary\xff")
        self.assertEqual(sorted(now), ["include/Gruntz/Extra.h", "include/Gruntz/State.h", "src/a.cpp"])

    def test_at_commit_compiles_the_commit_source_against_its_headers(self):
        calls = []

        class Target:
            root = self.root

            def source(self, unit):
                return "src/a.cpp"

            def evaluate(self, unit, sources, headers):
                calls.append((sources, headers))
                rows = [{"symbol": "f", "fuzzy": 100.0, "exact": True}]
                return {"at": Evaluation("at", unit, sections=[{"functions": rows}])}

        results = at_commit(Target(), "a", [self.first])
        self.assertEqual(calls, [({"at": b"#include <Gruntz/State.h>\n"}, {"include/Gruntz/State.h": b"old\n"})])
        self.assertEqual(results[0]["functions"][0]["symbol"], "f")


if __name__ == "__main__":
    unittest.main()
