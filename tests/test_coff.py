"""Reading functions from i386 COFF objects that clang assembles, and attributing
their differences in 32-bit mode."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord import coff
from concord.diff import diff_code, disassemble, read_function, read_functions
from concord.model import Cause

FUNCTION = '.def {name}; .scl 2; .type 32; .endef\n.section .text,"xr",one_only,{name}\n.globl {name}\n{name}:\n{body}\n'


def assemble(directory: Path, name: str, functions: dict[str, str]) -> Path:
    """An object with one section per function, as cl /Gy writes them."""
    source, output = directory / f"{name}.s", directory / f"{name}.obj"
    source.write_text(".intel_syntax noprefix\n" + "".join(FUNCTION.format(name=n, body=b) for n, b in functions.items()))
    subprocess.run(["clang", "--target=i686-pc-windows-msvc", "-c", "-o", str(output), str(source)], check=True)
    return output


@unittest.skipUnless(shutil.which("clang"), "needs clang to assemble COFF")
class CoffFunctions(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)

    def test_a_function_is_read_without_its_alignment_padding(self):
        body = "mov eax, [esp + 4]\ncall _g\nmov eax, dword ptr [_v]\nret\n.balign 16, 0x90"
        obj = assemble(self.directory, "padded", {"_f": body, "_h": "xor eax, eax\nret\n.balign 16, 0xcc"})
        found = read_functions(obj, {"_f", "_h", "_g"})
        self.assertEqual(set(found), {"_f", "_h"})  # _g is only referenced
        self.assertEqual(found["_f"].code, bytes.fromhex("8b442404" "e800000000" "a100000000" "c3"))
        self.assertEqual(found["_f"].relocations, frozenset({5, 10}))
        self.assertEqual(found["_h"].code, bytes.fromhex("31c0c3"))
        self.assertEqual(found["_f"].bits, 32)

    def test_functions_sharing_a_section_end_at_the_next_function(self):
        source, obj = self.directory / "shared.s", self.directory / "shared.obj"
        source.write_text(
            ".intel_syntax noprefix\n.text\n"
            ".def _a; .scl 2; .type 32; .endef\n.globl _a\n_a:\nmov eax, 1\nret\n.balign 16, 0x90\n"
            ".def _b; .scl 3; .type 32; .endef\n_b:\ncall _a\nret\n"
        )
        subprocess.run(["clang", "--target=i686-pc-windows-msvc", "-c", "-o", str(obj), str(source)], check=True)
        found = coff.functions(obj.read_bytes())
        self.assertEqual(found["_a"].code, bytes.fromhex("b801000000c3"))
        self.assertEqual((found["_b"].start, found["_b"].code[0], found["_b"].relocations), (16, 0xE8, frozenset({1})))

    def test_code_is_disassembled_as_32_bit(self):
        obj = assemble(self.directory, "mode", {"_f": "push ebp\nmov ebp, esp\ninc eax\npop ebp\nret"})
        # 0x40 is `inc eax` in 32-bit code and a prefix in 64-bit code
        self.assertEqual([i.mnemonic for i in disassemble(read_function(obj, "_f"))], ["push", "mov", "inc", "pop", "ret"])

    def test_a_register_choice_is_register_allocation(self):
        target = assemble(self.directory, "target", {"_f": "mov ecx, [esp + 4]\nadd ecx, edx\nmov eax, ecx\nret"})
        candidate = assemble(self.directory, "candidate", {"_f": "mov esi, [esp + 4]\nadd esi, edx\nmov eax, esi\nret"})
        result = diff_code(read_function(target, "_f"), read_function(candidate, "_f"))
        self.assertFalse(result.exact)
        self.assertEqual({finding.cause for finding in result.findings}, {Cause.REGISTER_ALLOCATION})

    def test_padding_alone_is_no_difference(self):
        target = assemble(self.directory, "target", {"_f": "xor eax, eax\nret"})
        candidate = assemble(self.directory, "candidate", {"_f": "xor eax, eax\nret\n.balign 16, 0x90"})
        self.assertTrue(diff_code(read_function(target, "_f"), read_function(candidate, "_f")).exact)

    def test_an_object_without_line_records_has_no_lines(self):
        obj = assemble(self.directory, "plain", {"_f": "xor eax, eax\nret"})
        self.assertEqual(coff.lines(obj.read_bytes(), "_f"), [])

    def test_an_elf_object_is_not_coff(self):
        self.assertFalse(coff.is_i386(b"\x7fELF" + bytes(16)))
        with self.assertRaises(ValueError):
            coff.functions(b"\x7fELF" + bytes(16))


class LineRecords(unittest.TestCase):
    """Line records as cl 5.0 /Zd wrote them for tests/fixtures/msvc-5.0-lines/lines.c."""

    FIXTURE = Path(__file__).parent / "fixtures" / "msvc-5.0-lines"

    def test_each_record_is_an_offset_and_a_source_line(self):
        data = (self.FIXTURE / "lines.obj").read_bytes()
        source = (self.FIXTURE / "lines.c").read_text().splitlines()
        first, second = coff.lines(data, "_first"), coff.lines(data, "_second")
        self.assertEqual(first, [(0, 7), (10, 8), (14, 9), (23, 11)])
        self.assertEqual(second, [(0, 15), (13, 16), (23, 18)])
        # the code at each offset is the statement on that line
        self.assertEqual([source[line - 1].strip() for _, line in first], ["int sum = a + b;", "if (sum < 0)", "return scale(sum);", "}"])
        self.assertEqual([source[line - 1].strip() for _, line in second], ["a = scale(a);", "a = scale(a + 1);", "}"])
        code = {i.offset: i.text for i in disassemble(read_function(self.FIXTURE / "lines.obj", "_first"))}
        self.assertEqual((code[10], code[23]), ("test eax, eax", "ret"))

    def test_a_symbol_the_object_only_references_has_no_lines(self):
        self.assertEqual(coff.lines((self.FIXTURE / "lines.obj").read_bytes(), "_scale"), [])


if __name__ == "__main__":
    unittest.main()
