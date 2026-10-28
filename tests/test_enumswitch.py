import os
import unittest
from pathlib import Path

from concord.diff import read_function
from concord.enumswitch import enum_members, missing_range_checks, source_candidates
from concord.harvest import Harvest

HARVEST = Path(os.environ.get("HARVEST_ROOT", "/code/harvest-swe2c"))
UNIT = "daisy/scene/CShadowVolumeSceneNode.cpp"
SYMBOL = "_ZN5daisy5scene22CShadowVolumeSceneNode19setMeshToRenderFromEPN2ox5scene5IMeshE"


class EnumSwitchTests(unittest.TestCase):
    def test_finds_the_pre_fix_enum_default_check_in_real_objects(self):
        report = HARVEST / "build" / "match" / "1.18-linux-amd64" / "daisy__scene__CShadowVolumeSceneNode.json"
        if not report.is_file():
            self.skipTest("run hv match in Harvest to provide the target object and unit report")

        harvest = Harvest(HARVEST)
        path = HARVEST / "src" / UNIT
        fixed = path.read_bytes()
        cast = b"switch((int)b->getVertexType())"
        original = b"switch(b->getVertexType())"
        self.assertEqual(fixed.count(cast), 1)
        before_fix = fixed.replace(cast, original, 1)

        evaluation = harvest.evaluate(UNIT, {"before_fix": before_fix})["before_fix"]
        self.assertFalse(evaluation.error)
        edits = source_candidates(before_fix, SYMBOL, enum_members(HARVEST))
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].line, 341)
        self.assertEqual(edits[0].kind, "switch")
        self.assertEqual(edits[0].source, fixed)

        target = read_function(harvest.objects / "target" / f"{harvest.slug(UNIT)}.o", SYMBOL)
        candidate = read_function(evaluation.object, SYMBOL)
        self.assertEqual(len(target.code), 4393)
        self.assertEqual(len(candidate.code), 4377)
        self.assertAlmostEqual(evaluation.fuzzy[SYMBOL], 99.44793, places=2)
        self.assertGreater(missing_range_checks(target, candidate), 0)


if __name__ == "__main__":
    unittest.main()
