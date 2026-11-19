import unittest

from concord.diff import Instruction
from concord.normalized import classify, renaming


def ins(mnemonic, *operands):
    return Instruction(0, mnemonic, tuple(operands), mnemonic, 1)


def reg(name):
    return ("reg", name)


def mem(base, disp):
    return ("mem", base, None, 1, disp, 4)


class Classify(unittest.TestCase):
    def test_a_consistent_register_swap(self):
        ours = [ins("mov", reg("rsi"), mem("rcx", 4)), ins("push", reg("rsi")), ins("mov", reg("rdi"), reg("rax"))]
        retail = [ins("mov", reg("rdi"), mem("rcx", 4)), ins("push", reg("rdi")), ins("mov", reg("rsi"), reg("rax"))]
        found = classify(ours, retail)
        self.assertEqual(found["level"], "registers")
        self.assertEqual(found["renaming"], {"rsi": "rdi", "rdi": "rsi"})

    def test_registers_no_single_renaming_explains(self):
        ours = [ins("mov", reg("rcx"), reg("rax")), ins("mov", reg("rcx"), reg("rdx"))]
        retail = [ins("mov", reg("rcx"), reg("rax")), ins("mov", reg("rdx"), reg("rdx"))]
        self.assertEqual(classify(ours, retail), {"level": "registers", "renaming": None})

    def test_stack_displacements(self):
        ours = [ins("mov", mem("rsp", 0x10), reg("rax"))]
        retail = [ins("mov", mem("rsp", 0x14), reg("rax"))]
        self.assertEqual(classify(ours, retail)["level"], "stack")

    def test_a_field_offset_is_not_a_stack_slot(self):
        ours, retail = [ins("mov", mem("rcx", 0x10), reg("rax"))], [ins("mov", mem("rcx", 0x14), reg("rax"))]
        self.assertEqual(classify(ours, retail)["level"], "opcodes")

    def test_a_condition_the_other_way_round(self):
        ours, retail = [ins("cmp", reg("rax"), ("imm", 1)), ins("je", ("label",))], [ins("cmp", reg("rax"), ("imm", 1)), ins("jne", ("label",))]
        self.assertEqual(classify(ours, retail)["level"], "branches")

    def test_a_missing_instruction_is_one_stretch_with_a_signature(self):
        ours = [ins("push", reg("rsi")), ins("ret")]
        retail = [ins("push", reg("rsi")), ins("xor", reg("rax"), reg("rax")), ins("ret")]
        self.assertEqual(classify(ours, retail), {"level": "sequence", "stretches": 1, "instructions": 1, "signature": "- / xor"})

    def test_identical_code_is_filed_under_the_first_view(self):
        same = [ins("ret")]
        self.assertEqual(classify(same, same)["level"], "registers")


class Renaming(unittest.TestCase):
    def test_one_register_cannot_map_to_two(self):
        ours = [ins("mov", reg("rax"), reg("rax"))]
        self.assertIsNone(renaming(ours, [ins("mov", reg("rcx"), reg("rdx"))]))

    def test_unchanged_registers_are_left_out(self):
        self.assertEqual(renaming([ins("push", reg("rax"))], [ins("push", reg("rax"))]), {})


if __name__ == "__main__":
    unittest.main()
