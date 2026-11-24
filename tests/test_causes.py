import unittest

from concord.causes import analyse, census
from concord.diff import ObjectFunction, diff_code


def function(code: bytes, bits: int = 32, targets=()) -> ObjectFunction:
    relocations = frozenset(offset for offset, _ in targets)
    return ObjectFunction("f", ".text", 0, code, relocations, bits, tuple(targets))


def kinds(retail: ObjectFunction, ours: ObjectFunction):
    analysis = analyse(retail, ours, diff_code(retail, ours).findings)
    return [p.kind for p in analysis.primaries], analysis


class FrameTests(unittest.TestCase):
    def test_a_larger_frame_explains_every_moved_offset(self):
        # sub esp, N; mov eax, [esp + N + 4]; add esp, N; ret
        retail = function(bytes.fromhex("83ec10" "8b442414" "8b4c2418" "83c410" "c3"))
        ours = function(bytes.fromhex("83ec18" "8b44241c" "8b4c2420" "83c418" "c3"))
        found, analysis = kinds(retail, ours)
        self.assertEqual(found, ["frame"])
        self.assertIn("8 bytes larger", analysis.primaries[0].detail)
        self.assertGreater(analysis.fallout["offsets moved by the frame"], 0)

    def test_x86_64_frame(self):
        retail = function(bytes.fromhex("4883ec28" "488b442430" "4883c428" "c3"), bits=64)
        ours = function(bytes.fromhex("4883ec38" "488b442440" "4883c438" "c3"), bits=64)
        self.assertEqual(kinds(retail, ours)[0], ["frame"])


class CallTests(unittest.TestCase):
    def test_an_extra_call_is_one_cause_named_by_its_callee(self):
        call = bytes.fromhex("e800000000")
        retail = function(call + call + b"\xc3", targets=[(1, "helper"), (6, "other")])
        ours = function(call + call + call + b"\xc3", targets=[(1, "helper"), (6, "helper"), (11, "other")])
        found, analysis = kinds(retail, ours)
        self.assertEqual(found, ["calls"])
        self.assertIn("1 more calls to helper", analysis.primaries[0].detail)

    def test_the_function_itself_is_not_a_callee(self):
        call = bytes.fromhex("e800000000")
        retail = function(call + b"\xc3", targets=[(1, "helper")])
        ours = function(call + call + b"\xc3", targets=[(1, "helper"), (6, "f")])
        self.assertNotIn("calls", kinds(retail, ours)[0])


class RegisterTests(unittest.TestCase):
    def test_swapped_registers_alone_are_register_allocation(self):
        # mov eax, [esp+4]; mov ecx, [esp+8]; add eax, ecx; ret  against the two loads' registers swapped
        retail = function(bytes.fromhex("8b442404" "8b4c2408" "01c8" "c3"))
        ours = function(bytes.fromhex("8b4c2404" "8b442408" "01c8" "c3"))
        self.assertEqual(kinds(retail, ours)[0], ["register-allocation"])

    def test_register_choices_are_fallout_of_a_structural_cause(self):
        retail = function(bytes.fromhex("83ec10" "8b442414" "8b4c2418" "01c8" "83c410" "c3"))
        ours = function(bytes.fromhex("83ec18" "8b4c241c" "8b442420" "01c8" "83c418" "c3"))
        found = kinds(retail, ours)[0]
        self.assertNotIn("register-allocation", found)
        self.assertIn("frame", found)


class CensusTests(unittest.TestCase):
    def test_counts_kinds_by_bytes(self):
        rows = [
            {"bytes": 100, "differences": 10, "fallout": {"register choices": 8}, "primaries": [{"kind": "frame"}, {"kind": "calls"}]},
            {"bytes": 40, "differences": 3, "fallout": {}, "primaries": [{"kind": "register-allocation"}]},
        ]
        result = census(rows)
        self.assertEqual(result["by_kind"]["frame"], {"functions": 1, "bytes": 100})
        self.assertEqual(result["register_allocation_only"], {"functions": 1, "bytes": 40})
        self.assertEqual((result["differences"], result["fallout"]), (13, 8))


if __name__ == "__main__":
    unittest.main()
