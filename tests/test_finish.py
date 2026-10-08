"""Finding and inverting the negated comparisons a search wrote."""

import shutil
import unittest

from concord.finish import invert, negated_comparisons

ORIGINAL = b"""int Board::f(int a, float d)
{
    if (!(a < 3)) a = 1;
    if (d <= 2.5f) a = 2; else a = 3;
    return a;
}
"""
SEARCHED = ORIGINAL.replace(b"if (d <= 2.5f) a = 2; else a = 3;", b"if (!(d <= 2.5f)) a = 3; else a = 2;")


@unittest.skipUnless(shutil.which("c++filt"), "needs c++filt")
class Comparisons(unittest.TestCase):
    def test_only_the_searchs_own_negations_are_candidates(self):
        nodes = negated_comparisons(SEARCHED, ORIGINAL, "_ZN5Board1fEif")
        self.assertEqual([n.text for n in nodes], [b"!(d <= 2.5f)"])  # !(a < 3) was in the original
        self.assertIn(b"if (d > 2.5f) a = 3;", invert(SEARCHED, nodes[0]))


if __name__ == "__main__":
    unittest.main()
