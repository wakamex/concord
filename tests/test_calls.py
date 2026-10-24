"""Call alignment on real object relocations, and the address-learning guard."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile

from concord.calls import CallSite, alias_claims, object_sites, proven


class Learning(unittest.TestCase):
    def test_constructor_aliases_need_identical_bodies_and_agreeing_calls(self):
        sites = [self.site(0x400200, symbol="C1"), self.site(0x400100, symbol="C2")]
        known = {"C1": 0x400100, "C2": 0x400200}
        identical = {("C1", "C2")}
        self.assertEqual({n: row[0] for n, row in alias_claims(sites, known, identical).items()}, {"C1": 0x400200, "C2": 0x400100})
        self.assertEqual(alias_claims(sites, known, set()), {})
        self.assertEqual(alias_claims(sites + [self.site(0x400100, symbol="C1")], known, identical), {})
        self.assertEqual(alias_claims(sites, {**known, "other": 0x400100}, identical), {})

    def site(self, address=0x400100, offset=10, symbol="callee"):
        return CallSite(symbol, address, "d/A.cpp", "caller", offset, offset)

    def test_agreeing_sites_require_the_compiled_extent_size(self):
        sites = [self.site(), self.site(offset=30)]
        learned = proven(sites, {"callee": {3}}, {0x400100: 3}, {})
        self.assertEqual(learned["callee"][0], 0x400100)
        self.assertTrue(learned["callee"][1].startswith("aligned-call:d/A.cpp:caller+"))

    def test_disagreeing_sites_reject_the_symbol(self):
        self.assertEqual(proven([self.site(), self.site(0x400200)], {"callee": {3}}, {0x400100: 3, 0x400200: 3}, {}), {})

    def test_size_mismatch_or_missing_extent_rejects_the_address(self):
        for extents in ({}, {0x400100: 4}):
            self.assertEqual(proven([self.site()], {"callee": {3}}, extents, {}), {})
        self.assertEqual(proven([self.site()], {"callee": {3, 4}}, {0x400100: 3}, {}), {})

    def test_claimed_address_and_competing_new_claims_are_rejected(self):
        self.assertEqual(proven([self.site()], {"callee": {3}}, {0x400100: 3}, {"other": 0x400100}), {})
        self.assertEqual(proven([self.site()], {"callee": {3}}, {0x400100: 3}, {"callee": 0x400200}), {})
        sites = [self.site(), self.site(symbol="other")]
        self.assertEqual(proven(sites, {"callee": {3}, "other": {3}}, {0x400100: 3}, {}), {})


@unittest.skipUnless(shutil.which("as") and shutil.which("ld"), "GNU assembler and linker are needed")
class ObjectCalls(unittest.TestCase):
    assembly = '''.text
.global caller, callee
.type caller,@function
caller:
.cfi_startproc
push %rbx
mov %rdi,%rbx
call callee
test %eax,%eax
add $1,%eax
mov %rbx,%rdi
call callee
pop %rbx
ret
.cfi_endproc
.size caller,.-caller
.type callee,@function
callee:
.cfi_startproc
xor %eax,%eax
ret
.cfi_endproc
.size callee,.-callee
'''

    def test_inexact_caller_uses_linked_call_destinations_and_real_extents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, obj, target = root / "a.s", root / "a.o", root / "target"
            source.write_text(self.assembly)
            subprocess.run(["as", str(source), "-o", str(obj)], check=True)
            source.write_text(self.assembly.replace("add $1", "add $2"))
            subprocess.run(["as", str(source), "-o", str(root / "target.o")], check=True)
            subprocess.run(["ld", "-e", "caller", "-Ttext=0x400000", str(root / "target.o"), "-o", str(target)], check=True)
            with target.open("rb") as stream:
                elf = ELFFile(stream)
                text = elf.get_section_by_name(".text")
                data, base = text.data(), text["sh_addr"]
                extents = {e["initial_location"]: e["address_range"] for e in elf.get_dwarf_info().EH_CFI_entries() if hasattr(e, "lsda_pointer")}
                symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
            sites, sizes = object_sites(obj, data, base, extents, {"caller": symbols["caller"]}, "d/A.cpp")
            self.assertEqual(len(sites), 2)
            learned = proven(sites, sizes, extents, {"caller": symbols["caller"]})
            self.assertEqual(learned["callee"][0], symbols["callee"])
            self.assertEqual(sizes["callee"], {3})
            self.assertEqual(object_sites(obj, data, base, extents, {}, "d/A.cpp")[0], [])


if __name__ == "__main__":
    unittest.main()
