"""Target selection for the permute search."""

import unittest

from concord.permute import drop_twins


class Twins(unittest.TestCase):
    def test_variants_with_a_listed_base_twin_are_dropped(self):
        functions = {
            ("a.cpp", "_ZN1AC1Ev"), ("a.cpp", "_ZN1AC2Ev"),  # C1 goes, C2 stays
            ("a.cpp", "_ZN1AD0Ev"), ("a.cpp", "_ZN1AD1Ev"), ("a.cpp", "_ZN1AD2Ev"),  # only D2 stays
            ("b.cpp", "_ZN1BC1Ev"),  # its C2 is exact or elsewhere: kept
            ("c.cpp", "_ZN1AC1Ev"),  # a twin in another unit does not count
        }  # fmt: skip
        self.assertEqual(
            drop_twins(functions),
            {("a.cpp", "_ZN1AC2Ev"), ("a.cpp", "_ZN1AD2Ev"), ("b.cpp", "_ZN1BC1Ev"), ("c.cpp", "_ZN1AC1Ev")},
        )


if __name__ == "__main__":
    unittest.main()
