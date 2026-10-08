"""Findings for Harvest functions whose bytes match but which its matcher does not
prove exact, from report rows in the shape `hv match` writes."""

import json
import tempfile
import unittest
from pathlib import Path

from concord.harvest import BUILD, FunctionVerdict, Harvest
from concord.model import Cause

APPEND = "_ZN2ox4core7CStringIcE6appendERKS2_"
ON_EVENT = "_ZN2ox3net22CHTTPConnectionHandler7OnEventERKNS_5event6SEventE"


class UnknownReferences(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        reports = self.root / "build" / "match" / BUILD
        reports.mkdir(parents=True)
        report = {"unit": "ox/net/CHTTPConnectionHandler.cpp", "sections": []}
        (reports / "ox__net__CHTTPConnectionHandler.json").write_text(json.dumps(report))
        self.harvest = Harvest(self.root)

    def detail(self, candidates):
        row = {"symbol": ON_EVENT, "address": "0x5ed7a0", "size": 1682, "extent": "fde", "global": True,
               "exact": False, "exact_but_unknown": True, "candidates": candidates}  # fmt: skip
        verdict = FunctionVerdict("ox__net__CHTTPConnectionHandler", ON_EVENT, row, {"bad_references": []})
        [finding] = self.harvest._unproven(verdict, 0)
        self.assertEqual(finding.cause, Cause.REFERENCE)
        return finding.detail

    def test_agreeing_references_point_at_hv_match_learn(self):
        detail = self.detail([[APPEND, "0x5ede40"]] * 9)
        self.assertIn("hv match --learn ox/net/CHTTPConnectionHandler.cpp", detail)

    def test_disagreeing_references_do_not(self):
        detail = self.detail([[APPEND, "0x5ede40"], [APPEND, "0x5ede50"]])
        self.assertNotIn("--learn", detail)


if __name__ == "__main__":
    unittest.main()
