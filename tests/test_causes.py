import unittest

from concord.causes import Analysis, Primary, analyse, census, place, sites
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
        self.assertIsNotNone(analysis.primaries[0].candidate_offset)  # placed at a differing call, so it gets a line

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
        found, analysis = kinds(retail, ours)
        self.assertEqual(found, ["register-allocation"])
        self.assertEqual((analysis.primaries[0].target_offset, analysis.primaries[0].candidate_offset), (0, 0))

    def test_register_choices_are_fallout_of_a_structural_cause(self):
        retail = function(bytes.fromhex("83ec10" "8b442414" "8b4c2418" "01c8" "83c410" "c3"))
        ours = function(bytes.fromhex("83ec18" "8b4c241c" "8b442420" "01c8" "83c418" "c3"))
        found = kinds(retail, ours)[0]
        self.assertNotIn("register-allocation", found)
        self.assertIn("frame", found)


class SiteTests(unittest.TestCase):
    def test_another_member_offset_is_a_field(self):
        # mov eax, [ecx + 0x10] against mov eax, [ecx + 0x14]
        retail = function(bytes.fromhex("8b4110" "c3"))
        ours = function(bytes.fromhex("8b4114" "c3"))
        self.assertEqual(kinds(retail, ours)[0], ["field"])

    def test_another_operation_is_an_operation(self):
        # xor eax, 0x100 against and eax, 0xffffffbf
        retail = function(bytes.fromhex("3500010000" "c3"))
        ours = function(bytes.fromhex("25bfffffff" "c3"))
        self.assertEqual(kinds(retail, ours)[0], ["operation"])

    def test_an_extra_copy_is_fallout_of_a_structural_cause(self):
        # a larger frame, and one extra mov ecx, eax that only copies a register
        retail = function(bytes.fromhex("83ec10" "8b442414" "83c410" "c3"))
        ours = function(bytes.fromhex("83ec18" "8b44241c" "89c1" "83c418" "c3"))
        found, analysis = kinds(retail, ours)
        self.assertEqual(found, ["frame"])
        self.assertGreater(sum(analysis.fallout.values()), 0)

    def test_copies_alone_are_register_allocation(self):
        # the same loads, with the result copied through another register on our side only
        retail = function(bytes.fromhex("8b442404" "8b4c2408" "01c8" "c3"))
        ours = function(bytes.fromhex("8b442404" "8b4c2408" "01c8" "89c2" "89d0" "c3"))
        self.assertEqual(kinds(retail, ours)[0], ["register-allocation"])


class SourceSiteTests(unittest.TestCase):
    def test_causes_within_two_lines_share_a_site_and_unplaced_ones_form_one(self):
        primaries = [{"line": 10}, {"line": 30}, {"line": 12}, {"line": None}, {"line": 14}, {"line": None}]
        self.assertEqual(sites(primaries), [[0, 2, 4], [1], [3, 5]])

    def test_sites_in_different_files_stay_apart(self):
        primaries = [{"file": "src/a.cpp", "line": 10}, {"file": "src/a.h", "line": 11}]
        self.assertEqual(sites(primaries), [[0], [1]])

    def test_a_cause_takes_the_unit_line_or_for_a_header_function_its_own_body(self):
        def analysis():
            return Analysis(primaries=[Primary("operation", "", 0, 4), Primary("calls", "", 0, 8)])

        places = {4: [("src/u.cpp", 12), ("src/a.h", 30)], 8: [("src/a.h", 31)]}
        inside = analysis()
        place(inside, places, "src/u.cpp", (10, 20))
        self.assertEqual([(p.file, p.line) for p in inside.primaries], [("src/u.cpp", 12), (None, None)])
        header = analysis()
        place(header, places, "src/u.cpp", None)  # defined outside the unit: the outermost frame, its own body
        self.assertEqual([(p.file, p.line) for p in header.primaries], [("src/a.h", 30), ("src/a.h", 31)])
        unnamed = analysis()  # line records that name no file are taken as the unit's, so a header function gets none
        place(unnamed, {4: [("src/u.cpp", 30)], 8: []}, "src/u.cpp", None)
        self.assertEqual([(p.file, p.line) for p in unnamed.primaries], [(None, None), (None, None)])

    def test_a_site_with_more_causes_comes_first(self):
        primaries = [{"line": 5}, {"line": 40}, {"line": 41}, {"line": None}]
        self.assertEqual(sites(primaries), [[1, 2], [0], [3]])


class CensusTests(unittest.TestCase):
    def test_counts_kinds_by_bytes(self):
        rows = [
            {"bytes": 100, "differences": 10, "fallout": {"register choices": 8}, "primaries": [{"kind": "frame"}, {"kind": "calls"}], "sites": [[0, 1]]},
            {"bytes": 40, "differences": 3, "fallout": {}, "primaries": [{"kind": "register-allocation"}], "sites": [[0]]},
        ]
        result = census(rows)
        self.assertEqual(result["by_kind"]["frame"], {"functions": 1, "bytes": 100})
        self.assertEqual(result["register_allocation_only"], {"functions": 1, "bytes": 40})
        self.assertEqual((result["differences"], result["fallout"]), (13, 8))
        self.assertEqual(result["sites_per_function"], {})  # neither row's causes have a line
        self.assertEqual(result["without_source_lines"], {"functions": 2, "bytes": 140})


if __name__ == "__main__":
    unittest.main()
