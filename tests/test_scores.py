"""Comparing snapshots of Harvest's progress report, in the shape hv.progress writes."""

import unittest

from concord.scores import compare, snapshot


def function(name, address, size, fuzzy, matched):
    m = {"total_code": str(size), "matched_code": str(size if matched else 0), "total_data": "0", "matched_data": "0"}
    f = {"name": name, "size": str(size), "fuzzy_match_percent": fuzzy,
         "metadata": {"demangled_name": name, "virtual_address": str(address)}}  # fmt: skip
    return {"name": f"{name} @ {address:#x}", "measures": m, "functions": [f]}


def data(address, size):
    m = {"total_code": "0", "matched_code": "0", "total_data": str(size), "matched_data": str(size)}
    s = {"name": ".rodata", "size": str(size), "fuzzy_match_percent": 100, "metadata": {"virtual_address": str(address)}}
    return {"name": f"Matched .rodata data @ {address:#x}", "measures": m, "sections": [s]}


class Compare(unittest.TestCase):
    def test_functions_by_score_and_data_by_bytes(self):
        before = snapshot({"units": [
            function("f", 0x100, 10, 100.0, True), function("g", 0x200, 10, 70.0, False),
            function("h", 0x300, 10, 50.0, False), data(0x1000, 60), data(0x2000, 8),
        ]})  # fmt: skip
        after = snapshot({"units": [
            function("f", 0x100, 10, 100.0, False), function("g", 0x200, 10, 68.0, False),
            function("h", 0x300, 10, 55.0, False),
            data(0x0fa0, 284),  # absorbs the run at 0x1000: no byte lost
        ]})  # fmt: skip
        changes, lost, gained = compare(before, after)
        self.assertEqual({c.name: c.worse for c in changes}, {"f": True, "g": True, "h": False})
        self.assertEqual((lost, gained), (8, 284 - 60))


if __name__ == "__main__":
    unittest.main()
