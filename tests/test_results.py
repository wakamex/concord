"""The search log and its patches."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from concord import results


class SearchLog(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.log = self.root / "results" / "harvest.jsonl"

    def test_rows_append_with_a_date(self):
        results.record(self.log, {"command": "match", "unit": "a.cpp"})
        results.record(self.log, {"command": "sweep", "unit": "b.cpp"})
        rows = results.load(self.log)
        self.assertEqual([r["unit"] for r in rows], ["a.cpp", "b.cpp"])
        self.assertTrue(all("date" in r for r in rows))

    def test_a_patch_applies_to_the_original(self):
        original = b"int a() { return 1; }\nint b() { return 2; }\n"
        candidate = b"int b() { return 2; }\nint a() { return 1; }\n"
        relative = results.save_patch(self.log, "x/u.cpp", original, candidate)
        tree = self.root / "tree"
        (tree / "src" / "x").mkdir(parents=True)
        (tree / "src" / "x" / "u.cpp").write_bytes(original)
        subprocess.run(["git", "apply", str(self.log.parent / relative)], cwd=tree, check=True)
        self.assertEqual((tree / "src" / "x" / "u.cpp").read_bytes(), candidate)

    def test_commit_marks_a_dirty_source_tree(self):
        repo = self.root / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "u.cpp").write_text("int a;\n")
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "x"], check=True)
        self.assertFalse(results.commit(repo).endswith("-dirty"))
        (repo / "src" / "u.cpp").write_text("int b;\n")
        self.assertTrue(results.commit(repo).endswith("-dirty"))


if __name__ == "__main__":
    unittest.main()
