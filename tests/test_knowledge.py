import tomllib
import unittest
from pathlib import Path


class InlinedStringReferenceKnowledgeTests(unittest.TestCase):
    def test_dropship_call_distribution_evidence_is_recorded(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        knowledge = tomllib.loads(path.read_text())
        idiom = next(item for item in knowledge["idiom"] if item["id"] == "inlined-string-reference-counts")
        evidence = next(item for item in idiom["evidence"] if item.get("commit") == "9a39d4d")

        self.assertIn("all 77 sprite loads", evidence["note"])
        self.assertIn("out-of-line CString<char>::CString(const char*)", evidence["note"])
        self.assertIn("All 52 method placements", evidence["note"])


if __name__ == "__main__":
    unittest.main()
