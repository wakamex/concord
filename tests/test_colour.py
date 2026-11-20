import unittest

from concord.colour import TABLE, memory, ranges, register, signed, wanted

LOG = """Stopped on breakpoint 1 at 0x0000000042a90e c2+0x2a90e
 00cff82c
0x00000000493244 c2+0x93244:  00f795a8 00000000 00000000 00000000
0x00000000f795a8:  00fb872c 00000c02 00f795a8 0000006d
"""


def record(at, symbol, priority, descriptor, rid, references, chained=0):
    words = [symbol, 0xC02, at, priority, descriptor, 0, 0, rid, 0, references, 0, chained, 0, 0, 0, 0, 0]
    return {at + 4 * i: w for i, w in enumerate(words)}


class Memory(unittest.TestCase):
    def test_reads_words_with_and_without_a_module_label(self):
        words = memory(LOG)
        self.assertEqual(words[0x493244], 0xF795A8)
        self.assertEqual(words[0xF795A8 + 12], 0x6D)


class Fields(unittest.TestCase):
    def test_a_register_is_a_descriptor_in_c2s_table(self):
        self.assertEqual(register(0x494758 + 80 * 2), "ecx")
        self.assertIsNone(register(0x494758 + 81))
        self.assertIsNone(register(0))

    def test_priority_is_signed(self):
        self.assertEqual(signed(0xFFFFFFF3), -13)


class Ranges(unittest.TestCase):
    def test_colouring_order_follows_priority_and_names_named_symbols(self):
        words = record(0x1000, 0x5000, 16, 0x494758 + 80 * 3, 9, 3) | record(0x2000, 0x6000, 57, 0x494758 + 80 * 2, 6, 5, chained=0x1000)
        words |= {0x5000 + 4: 3, 0x5000 + 0x1C: 179, 0x6000: 0x7000, 0x6000 + 4: 0x20804, 0x6000 + 0x1C: 4, 0x7000 + 0xC: 0xFFFFFFF4}
        found = ranges(words, [0x2000])
        self.assertEqual([r["id"] for r in found], [6, 9])
        self.assertEqual(found[0] | {}, {"id": 6, "priority": 57, "register": "ecx", "references": 5, "symbol_kind": "named", "symbol_number": 4, "frame_offset": -12})
        self.assertEqual(found[1]["symbol_kind"], "temporary")

    def test_wanted_asks_for_unread_records_then_their_symbols(self):
        self.assertEqual(wanted({}, [0x2000]), [(0x2000, 17)])
        words = record(0x2000, 0x6000, 1, 0, 1, 1)
        self.assertEqual(wanted(words, [0x2000]), [(0x6000, 12)])

    def test_the_table_address(self):
        self.assertEqual(TABLE, 0x493238)


if __name__ == "__main__":
    unittest.main()
