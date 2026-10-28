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


class PlayStateOnEventCallEvidenceTests(unittest.TestCase):
    def test_current_call_audit_distinguishes_cleanup_from_event_calls(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        knowledge = tomllib.loads(path.read_text())
        idiom = next(item for item in knowledge["idiom"] if item["id"] == "ipa-inliner-distribution")
        evidence = next(
            item for item in idiom["evidence"]
            if item.get("function") == "Harvest e86091f CPlayState::OnEvent call audit"
        )

        for finding in (
            "13,487 target bytes and 13,325 candidate bytes at score 66.92",
            "six extra target calls to CString<wchar_t>::~CString()",
            "one extra target call to _Unwind_Resume",
            "no different or missing gameplay callee count",
            "807 call rows",
            "exception cleanup",
            "Two earlier int-cast overlays",
        ):
            self.assertIn(finding, evidence["note"])


if __name__ == "__main__":
    unittest.main()
