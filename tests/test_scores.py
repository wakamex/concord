"""Comparing per-function score snapshots."""

import unittest

from concord.scores import compare


class Compare(unittest.TestCase):
    def test_changes_are_listed_and_regressions_marked(self):
        before = {
            "a.cpp/f": {"score": 100.0, "exact": True},
            "a.cpp/g": {"score": 98.0, "exact": False},
            "a.cpp/h": {"score": 90.0, "exact": False},
            "a.cpp/same": {"score": 50.0, "exact": False},
            "a.cpp/gone": {"score": 70.0, "exact": False},
        }
        after = {
            "a.cpp/f": {"score": 100.0, "exact": False},  # bytes match but no longer proven
            "a.cpp/g": {"score": 100.0, "exact": True},
            "a.cpp/h": {"score": 89.5, "exact": False},
            "a.cpp/same": {"score": 50.0, "exact": False},
            "a.cpp/new": {"score": 60.0, "exact": False},
        }
        changes = {c.function: c.worse for c in compare(before, after)}
        self.assertEqual(changes, {"a.cpp/f": True, "a.cpp/g": False, "a.cpp/h": True, "a.cpp/gone": True, "a.cpp/new": False})


if __name__ == "__main__":
    unittest.main()
