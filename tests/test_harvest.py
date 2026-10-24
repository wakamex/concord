"""Findings for Harvest functions whose bytes match but which its matcher does not
prove exact, from report rows in the shape `hv match` writes."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from concord.harvest import BUILD, FunctionVerdict, Harvest, _stop_drivers
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

    def test_clean_removes_only_old_work_directories(self):
        import os
        import time

        old = self.root / "build" / "concord" / "a__b" / "old"
        new = self.root / "build" / "concord" / "a__b" / "new"
        for path in (old, new):
            path.mkdir(parents=True)
            (path / "c.o").write_bytes(b"x" * 10)
        os.utime(old, (time.time() - 7200, time.time() - 7200))
        self.assertEqual(self.harvest.clean(3600), (1, 10))
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_stop_forgets_driver_before_termination_finishes(self):
        process = Mock()
        process.poll.return_value = None
        drivers = {self.root: process}
        process.terminate.side_effect = lambda: self.assertEqual(drivers, {})
        with patch('concord.harvest._drivers', drivers):
            _stop_drivers()
            _stop_drivers()
        process.stdin.close.assert_called_once()
        process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
