"""Static object layouts inferred from aligned references in real Harvest objects."""

import os
import subprocess
import unittest
from pathlib import Path

from concord.harvest import Harvest
from concord.statics import analyze_unit


@unittest.skipUnless(os.environ.get("CONCORD_HARVEST"), "needs a Harvest checkout after hv match")
class HarvestStaticLayouts(unittest.TestCase):
    def test_baseline_table_order_is_inferred_from_target_references(self):
        root = Path(os.environ["CONCORD_HARVEST"])
        harvest = Harvest(root)
        unit = "HarvestFull/harvest/states/CPlayState.cpp"
        source = subprocess.run(
            ["git", "-C", str(root), "show", "c23bb1e:src/" + unit],
            capture_output=True,
            check=True,
        ).stdout
        baseline = harvest.evaluate(unit, {"baseline": source})["baseline"]
        result = analyze_unit(harvest, unit, object_path=baseline.object, function_sections=baseline.sections)
        objects = {row["symbol"]: row for row in result["objects"]}
        entity = objects["_ZN7harvest6entityL16ENTITY_KEY_NAMESE"]
        achievements = objects["_ZN7harvest8settingsL17ACHIEVEMENT_NAMESE"]
        ratings = objects["_ZN7harvest8settingsL24ACHIEVEMENT_RATING_NAMESE"]

        self.assertEqual((entity["offset"], entity["target"]["offset"]), (0x6e0, "0x520"))
        self.assertEqual((achievements["offset"], achievements["target"]["offset"]), (0x520, "0x5e0"))
        self.assertEqual((ratings["offset"], ratings["target"]["offset"]), (0x640, "0x700"))
        self.assertEqual((entity["target"]["size"], achievements["target"]["size"], ratings["target"]["size"]),
                         (168, 264, 136))
        self.assertTrue(all(row["target"]["size_basis"] == "target-content-match"
                            for row in (entity, achievements, ratings)))
        self.assertTrue(all(row["target"]["relocation_content_matches"] for row in (entity, achievements, ratings)))
        self.assertTrue(all(row["status"] == "layout-differs" for row in (entity, achievements, ratings)))
        self.assertTrue(result["sections"][".rodata"]["order_differs"])

    def test_play_state_tables_match_target_relocation_content(self):
        root = Path(os.environ["CONCORD_HARVEST"])
        result = analyze_unit(Harvest(root), "HarvestFull/harvest/states/CPlayState.cpp")
        objects = {row["symbol"]: row for row in result["objects"]}
        entity = objects["_ZN7harvest6entityL16ENTITY_KEY_NAMESE"]
        achievements = objects["_ZN7harvest8settingsL17ACHIEVEMENT_NAMESE"]
        ratings = objects["_ZN7harvest8settingsL24ACHIEVEMENT_RATING_NAMESE"]

        self.assertEqual((entity["target"]["section"], entity["target"]["offset"]), (".rodata", "0x520"))
        self.assertEqual((achievements["target"]["section"], achievements["target"]["offset"]),
                         (".rodata", "0x5e0"))
        self.assertEqual((ratings["target"]["section"], ratings["target"]["offset"]), (".rodata", "0x700"))
        self.assertEqual((entity["target"]["size"], achievements["target"]["size"], ratings["target"]["size"]),
                         (168, 264, 136))
        self.assertTrue(all(row["target"]["relocation_content_matches"] for row in (entity, achievements, ratings)))
        self.assertEqual(entity["status"], "same-layout")
        self.assertEqual(achievements["status"], "same-layout")
        self.assertEqual(ratings["status"], "same-layout")

        order = result["sections"][".rodata"]["proposed_definition_order"]
        self.assertLess(order.index(entity["symbol"]), order.index(achievements["symbol"]))
        self.assertLess(order.index(achievements["symbol"]), order.index(ratings["symbol"]))
        self.assertFalse(result["sections"][".rodata"]["order_differs"])

    def test_shuttle_target_arrays_keep_their_target_positions_and_sizes(self):
        root = Path(os.environ["CONCORD_HARVEST"])
        result = analyze_unit(Harvest(root), "HarvestFull/harvest/entity/CShuttleEntity.cpp")
        objects = {row["symbol"]: row for row in result["objects"]}
        end = objects["_ZN7harvest6entity16g_bestEndTargetsE"]
        middle = objects["_ZN7harvest6entity19g_bestMiddleTargetsE"]

        self.assertEqual(end["status"], "same-layout")
        self.assertEqual(end["section"], ".bss")
        self.assertEqual(end["offset"], 0x20)
        self.assertEqual(end["size"], 112)
        self.assertEqual(end["target"]["offset"], "0x20")
        self.assertEqual(end["target"]["size"], 112)
        self.assertGreater(end["references"], 0)

        self.assertEqual(middle["status"], "same-layout")
        self.assertEqual(middle["target"]["offset"], "0xa0")
        self.assertEqual(middle["target"]["size"], 112)
        proposed = result["sections"][".bss"]["proposed_definition_order"]
        self.assertLess(proposed.index(end["symbol"]), proposed.index(middle["symbol"]))


if __name__ == "__main__":
    unittest.main()
