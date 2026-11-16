import unittest
from pathlib import Path

from concord.diff import Instruction
from concord.model import Cause, DiffFinding
from concord.regtrace import changed, other, parse, retail_registers, step, writes

# winedbg's log of c2.exe compiling gruntz-decomp's behindcandy unit (CBehindCandy's constructor and the
# functions it uses), from `concord regtrace`, cut where c2 exits
LOG = (Path(__file__).parent / "fixtures" / "msvc-5.0-regtrace" / "behindcandy.log").read_text()


def ins(offset, text, mnemonic, first):
    return Instruction(offset, mnemonic, (("reg", first),) if first else (), text, 3)


class Parse(unittest.TestCase):
    def test_grants_carry_their_function_rule_and_value(self):
        grants, functions = parse(LOG)
        self.assertEqual(functions, 23)  # the object defines 23 functions
        self.assertEqual(len(grants), 13)
        constructor = [g for g in grants if g.function == 1]  # second in the object, after SerializeDispatch
        self.assertEqual([g.value for g in constructor], [0xCD, 0xA2, 0x152, 0x151, 0x150, 0x14F, 0x153, 0x154, 0x155, 0x156, 0x157])
        self.assertEqual([g.register for g in constructor[2:]], ["eax", "ecx", "edx"] * 3)
        self.assertEqual({g.path for g in constructor[2:]}, {"round-robin"})
        self.assertEqual([g.index for g in constructor][-1], 13)
        # only the exception-unwind stubs' temporaries prefer a register: ecx, for the destructor's this
        self.assertEqual([(g.value, g.prefers) for g in constructor if g.prefers], [(0xCD, "ecx"), (0xA2, "ecx")])

    def test_a_preference_names_the_register_whose_descriptor_it_points_at(self):
        from concord.regtrace import Grant
        self.assertEqual(Grant(1, 0, "ecx", 0x47, "round-robin", 0x4947A8).prefers, "eax")  # 0x494758 + 80 * 1
        self.assertIsNone(Grant(1, 0, "ecx", 0x47, "round-robin", 0x4947A9).prefers)


class Registers(unittest.TestCase):
    def test_steps_count_places_past_retail_in_the_rotation(self):
        self.assertEqual(step("ecx", "eax"), 1)
        self.assertEqual(step("eax", "ecx"), 2)
        self.assertIsNone(step("eax", "eax"))
        self.assertIsNone(step("esi", "eax"))
        self.assertEqual([other(r) for r in ("eax", "ecx", "edx", "esi")], ["ecx", "edx", "eax", "eax"])

    def test_retail_registers_read_concord_diffs_register_findings(self):
        findings = [
            DiffFinding(Cause.REGISTER_ALLOCATION, 352, "eax->ecx", 352, 2, (352, 355), (352, 355)),
            DiffFinding(Cause.REGISTER_ALLOCATION, 400, "target only, spills, reloads or copies: mov ecx, 0", 400, 1, (400,), (400,)),
            DiffFinding(Cause.INSTRUCTION_SELECTION, 500, "eax->ecx", 500, 1, (500,), (500,)),
        ]
        self.assertEqual(retail_registers(findings), {352: {"ecx": "eax"}, 355: {"ecx": "eax"}})

    def test_changed_and_writes_find_the_instruction_a_grant_holds(self):
        before = [ins(0, "mov ecx, dword ptr [esi + 0x10]", "mov", "rcx"), ins(3, "cmp eax, ebp", "cmp", "rax")]
        after = [ins(0, "mov eax, dword ptr [esi + 0x10]", "mov", "rax"), ins(3, "cmp eax, ebp", "cmp", "rax")]
        self.assertEqual([i.offset for i in changed(before, after)], [0])
        self.assertEqual(changed(before, after[:1]), [])
        self.assertTrue(writes(before[0], "ecx"))
        self.assertFalse(writes(before[1], "eax"))


if __name__ == "__main__":
    unittest.main()
