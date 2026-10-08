"""Comparing memory-access lists, in the shape harvest-oracle accesses writes."""

import unittest

from concord.layout import compare


def access(base, offset, size, kind):
    return {"instruction": "0x0", "base": base, "offset": offset, "size": size, "access": kind}


class Compare(unittest.TestCase):
    def test_offsets_are_compared_per_shared_base_ignoring_order(self):
        original = [access("this", "0x30", 4, "read"), access("this", "0x40", 4, "write"),
                    access("argument 1", "0x0", 8, "read"), access("this.0x98", None, 4, "read"),
                    access("_ZN1gE", "0x0", 8, "read")]  # fmt: skip
        ours = [access("this", "0x40", 4, "write"), access("this", "0x30", 4, "read"), access("this", "0x30", 4, "read"),
                access("argument 1", "0x8", 8, "read"), access("this.0x98", "0x10", 4, "read")]  # fmt: skip
        [mismatch] = compare(original, ours)  # this agrees; _ZN1gE and the indexed access are one-sided
        self.assertEqual((mismatch.base, mismatch.original, mismatch.ours), ("argument 1", [(0, 8, "read")], [(8, 8, "read")]))


if __name__ == "__main__":
    unittest.main()
