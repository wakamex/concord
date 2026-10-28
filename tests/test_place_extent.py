"""Exact candidate-object matches for unnamed target function extents."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.place import extent_match


@unittest.skipUnless(shutil.which("as") and shutil.which("ld"), "GNU assembler and linker are needed")
class ExtentMatch(unittest.TestCase):
    candidate = """.text
.global candidate
.type candidate,@function
candidate:
.cfi_startproc
push %rbx
mov %rdi,%rbx
call helper
add $1,%eax
mov %rbx,%rdi
pop %rbx
ret
.cfi_endproc
.size candidate,.-candidate
"""

    target_helper = """.section .text.helper,"ax",@progbits
.global helper
.type helper,@function
helper:
.cfi_startproc
xor %eax,%eax
ret
.cfi_endproc
.size helper,.-helper
"""

    def make_target(self, directory: Path, source: str) -> tuple[Path, int]:
        asm = directory / "target.s"
        obj = directory / "target.o"
        target = directory / "target"
        asm.write_text(source + self.target_helper)
        subprocess.run(["as", str(asm), "-o", str(obj)], check=True)
        subprocess.run(["ld", "-e", "candidate", "-Ttext=0x400000", str(obj), "-o", str(target)], check=True)
        from elftools.elf.elffile import ELFFile

        with target.open("rb") as stream:
            elf = ELFFile(stream)
            address = next(
                symbol["st_value"]
                for symbol in elf.get_section_by_name(".symtab").iter_symbols()
                if symbol.name == "candidate"
            )
        return target, address

    def test_relocation_fields_are_masked_but_other_bytes_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate.s"
            obj = root / "candidate.o"
            source.write_text(self.candidate)
            subprocess.run(["as", str(source), "-o", str(obj)], check=True)

            target, address = self.make_target(root, self.candidate)
            match = extent_match(obj, target, "candidate", address)
            self.assertIsNotNone(match)
            self.assertGreater(match.fixed_bytes, 0)
            self.assertEqual(match.size, 17)
            self.assertEqual(match.relocation_bytes, 4)

            changed_target, changed_address = self.make_target(root, self.candidate.replace("add $1", "add $2"))
            self.assertIsNone(extent_match(obj, changed_target, "candidate", changed_address))

    def test_symbol_and_target_address_must_name_function_extents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate.s"
            obj = root / "candidate.o"
            source.write_text(self.candidate)
            subprocess.run(["as", str(source), "-o", str(obj)], check=True)
            target, address = self.make_target(root, self.candidate)

            self.assertIsNone(extent_match(obj, target, "missing", address))
            self.assertIsNone(extent_match(obj, target, "candidate", address + 1))
