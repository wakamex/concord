import unittest

from concord.first_differences import at, callees, every_cause, rank


class Callees(unittest.TestCase):
    def test_calls_members_and_macros_are_named_once_in_order(self):
        line = b"    SET_SORT_KEY(m_object->GetSortKey(), Clamp(y)); m_object->GetSortKey();"
        self.assertEqual(callees(line), ["SET_SORT_KEY", "GetSortKey", "Clamp"])

    def test_keywords_casts_and_comments_are_not_calls(self):
        line = b"if (static_cast<int>(x) > 0) return Foo(sizeof(T)); // Bar(1)"
        self.assertEqual(callees(line), ["Foo"])


class Lines(unittest.TestCase):
    def test_an_offset_takes_the_line_of_the_last_record_at_or_before_it(self):
        records = [(0, 10), (8, 12), (20, 15)]
        self.assertEqual([at(records, offset) for offset in (0, 7, 8, 30)], [10, 10, 12, 15])
        self.assertIsNone(at([(4, 10)], 0))


class Rank(unittest.TestCase):
    def test_callees_rank_by_functions_then_bytes(self):
        functions = [
            {"symbol": "a", "size": 10, "callees": ["Push", "Snap"]},
            {"symbol": "b", "size": 50, "callees": ["Snap"]},
            {"symbol": "c", "size": 5, "callees": ["Push"]},
        ]
        ranked = rank(functions, "callees")
        self.assertEqual([(row["callee"], row["functions"], row["bytes"]) for row in ranked], [("Snap", 2, 60), ("Push", 2, 15)])
        self.assertEqual(ranked[0]["symbols"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()


class EveryCauseTests(unittest.TestCase):
    def test_counts_every_cause_and_functions_it_alone_blocks(self):
        functions = [
            {"size": 100, "causes": {"register-allocation": 3}},
            {"size": 40, "causes": {"register-allocation": 1, "stack-layout": 2}},
        ]
        found = every_cause(functions)
        self.assertEqual(found["register-allocation"], {"functions": 2, "bytes": 140, "differences": 4, "only_cause": 1, "only_cause_bytes": 100})
        self.assertEqual(found["stack-layout"]["only_cause"], 0)
        self.assertEqual(list(found), ["register-allocation", "stack-layout"])
