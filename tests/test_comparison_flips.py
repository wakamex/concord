"""Find comparison flips in the compiled functions Harvest still reports inexact."""

import os
import unittest
from pathlib import Path

HARVEST = Path(os.environ.get("CONCORD_HARVEST", "../harvest"))
BUILD = "1.18-linux-amd64"


@unittest.skipUnless((HARVEST / "build" / "match" / BUILD).is_dir(), "needs a Harvest checkout after `hv match`")
class HarvestComparisonFlips(unittest.TestCase):
    def test_real_harvest_functions_differ_only_by_reversed_comparisons(self):
        from concord.comparison_flips import _source_inline_function, find
        from concord.harvest import Harvest

        harvest = Harvest(HARVEST)
        rows = find(harvest)
        found = {row["symbol"]: row for row in rows}
        self.assertEqual(len(rows), len({row["address"] for row in rows}))
        expected = {
            "_ZN5daisy2io12CZipFileListC2EPNS0_10CZipReaderEPKcS5_": (1, 0x7A5),
            "_ZN5daisy2io12CZipFileListC1EPNS0_10CZipReaderEPKcS5_": (1, 0x7A5),
            "_ZN2ox4core16CStringFunctions18millisecondsToWideEfb": (1, 0x577),
            "_ZN5daisy2io11CFileSystem21intern_resolveAliasesEPKc": (1, 0x7AD),
        }
        self.assertTrue(expected.keys() <= found.keys(), expected.keys() - found.keys())
        for symbol, (flips, offset) in expected.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(len(found[symbol]["flips"]), flips)
                self.assertEqual(found[symbol]["flips"][0]["comparison_offset"], offset)

        buildable_list = "_ZN7harvest6entity15CBuildableItems24loadCreativeBuildingListEPN2ox2io11IFileSystemEPNS2_5video12IVideoDriverEPKcPNS3_9IFileListESC_"
        octree_render = "_ZN5daisy5scene17COctTreeSceneNode6renderEv"
        self.assertIn(buildable_list, found, "five flipped comparisons in loadCreativeBuildingList were missed")
        self.assertEqual(
            sorted(flip["comparison_offset"] for flip in found[buildable_list]["flips"]),
            [0x1CD, 0x224, 0x2FD, 0xA7C, 0xB1A],
        )
        verdicts = {verdict.symbol: verdict for verdict in harvest.verdicts()}
        self.assertIn(octree_render, verdicts)
        self.assertEqual(verdicts[octree_render].row["address"], "0x5630d0")
        self.assertFalse(verdicts[octree_render].row["exact"])
        self.assertNotIn(octree_render, found)

        source = {"path": "src/ox/core/CString.h"}
        for line, function in ((159, "CString::operator="), (176, "CString::operator=="), (186, "CString::operator<")):
            with self.subTest(line=line):
                self.assertEqual(_source_inline_function(HARVEST, {**source, "line": line}), function)

        updated = found["_ZN2ox3gui11IGUIElement22updateAbsolutePositionEv"]
        self.assertEqual(len(updated["units"]), 22)


if __name__ == "__main__":
    unittest.main()
