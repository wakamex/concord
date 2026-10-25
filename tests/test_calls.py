"""Call alignment on real object relocations, and the address-learning guard."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile

from concord.calls import CallSite, alias_claims, object_sites, proven, unique_caller


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

    def test_unnamed_caller_needs_unique_full_code_and_size_match(self):
        code = b"abcdefgh" + bytes(4) + b"ijklmnop"
        target = code[:8] + b"CALL" + code[12:]
        self.assertEqual(unique_caller(code, {8}, target, 0x400000, {0x400000: 20}), 0x400000)
        self.assertIsNone(unique_caller(code, {8}, target + target, 0x400000, {0x400000: 20, 0x400014: 20}))
        self.assertIsNone(unique_caller(code, {8}, b"x" + target[1:], 0x400000, {0x400000: 20}))
        self.assertIsNone(unique_caller(code, {8}, target, 0x400000, {0x400000: 19}))

    def test_agreeing_sites_require_the_compiled_extent_size(self):
        sites = [self.site(), self.site(offset=30)]
        learned = proven(sites, {"callee": {3}}, {0x400100: 3}, {})
        self.assertEqual(learned["callee"][0], 0x400100)
        self.assertTrue(learned["callee"][1].startswith("aligned-call:d/A.cpp:caller+"))

    def test_only_exact_callers_can_prove_a_different_size_callee(self):
        site = CallSite("callee", 0x400100, "u.cpp", "caller", 1, 1, "exact-caller")
        self.assertEqual(proven([site], {"callee": {2}}, {0x400100: 30}, {}), {})
        learned = proven([site], {"callee": {2}}, {0x400100: 30}, {}, exact_callers=True)
        self.assertTrue(learned["callee"][1].startswith("exact-caller:"))
        self.assertEqual(proven([self.site()], {"callee": {2}}, {0x400100: 30}, {}, exact_callers=True), {})
        self.assertEqual(proven([site, self.site(0x400200)], {"callee": {2}}, {0x400100: 30}, {}, exact_callers=True), {})
        self.assertEqual(proven([site], {"callee": {2}}, {}, {}, exact_callers=True), {})
        self.assertEqual(proven([site], {"callee": {2}}, {0x400100: 30}, {"other": 0x400100}, exact_callers=True), {})

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

    def test_exact_window_in_an_inexact_unnamed_caller_proves_a_different_size_callee(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            block = 'mov $11,%r8d\nmov $22,%r9d\nmov $33,%r10d\nmov $44,%r11d\nmov $55,%esi\nmov $66,%edi\nmov $77,%edx\nmov $88,%ecx\ncall callee\nmov %eax,%edx\n'
            source_text = '.text\n.global caller,callee\n.type caller,@function\ncaller:\n.cfi_startproc\nmov $1,%eax\njmp .Lbody\n.Lbody:\n' + block + 'ret\n.cfi_endproc\n.size caller,.-caller\n.type callee,@function\ncallee:\n.cfi_startproc\nxor %eax,%eax\nret\n.cfi_endproc\n.size callee,.-callee\n'
            source = root / 'a.s'
            obj = root / 'a.o'
            source.write_text(source_text)
            subprocess.run(['as', str(source), '-o', str(obj)], check=True)

            def linked(text):
                source.write_text(text)
                subprocess.run(['as', str(source), '-o', str(root / 'target.o')], check=True)
                subprocess.run(['ld', '-e', 'caller', '-Ttext=0x400000', str(root / 'target.o'), '-o', str(root / 'target')], check=True)
                with (root / 'target').open('rb') as stream:
                    elf = ELFFile(stream)
                    data = elf.get_section_by_name('.text').data()
                    extents = {e['initial_location']: e['address_range'] for e in elf.get_dwarf_info().EH_CFI_entries() if hasattr(e, 'lsda_pointer')}
                    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
                return data, extents, symbols

            target_text = source_text.replace('mov $1,%eax', 'mov $2,%eax').replace('xor %eax,%eax', 'nop\nxor %eax,%eax')
            data, extents, symbols = linked(target_text)
            sites, sizes = object_sites(obj, data, 0x400000, extents, {}, 'u.cpp', windows=True)
            self.assertEqual(len(sites), 1)
            self.assertEqual(sites[0].proof, 'exact-window')
            self.assertEqual(sites[0].target_caller, symbols['caller'])
            self.assertEqual(proven(sites, sizes, extents, {}), {})
            self.assertEqual(proven(sites, sizes, extents, {}, exact_callers=True)['callee'][0], symbols['callee'])
            self.assertEqual(proven(sites, sizes, extents, {'other': symbols['callee']}, exact_callers=True), {})
            self.assertEqual(proven(sites + [CallSite('callee', symbols['callee'] + 1, 'u.cpp', 'other', 0, 0)], sizes, extents, {}, exact_callers=True), {})
            # A changed physical register invalidates the full block proof.
            data, extents, _ = linked(target_text.replace('mov $11,%r8d', 'mov $11,%r9d'))
            self.assertEqual(object_sites(obj, data, 0x400000, extents, {}, 'u.cpp', windows=True)[0], [])
            # A second identical block anywhere in the image makes it ambiguous.
            duplicate = '.type duplicate,@function\nduplicate:\n.cfi_startproc\n' + block + 'ret\n.cfi_endproc\n.size duplicate,.-duplicate\n'
            data, extents, _ = linked(target_text + duplicate)
            self.assertEqual(object_sites(obj, data, 0x400000, extents, {}, 'u.cpp', windows=True)[0], [])

    def test_learning_revisits_a_newly_named_inexact_caller(self):
        from unittest import mock

        from concord import calls
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assembly = self.assembly.replace('caller, callee', 'caller, callee, leaf').replace('xor %eax,%eax\nret', 'push %rbx\nmov %rdi,%rbx\ncall leaf\nadd $5,%eax\nmov %rbx,%rdi\npop %rbx\nret')
            assembly += '.type leaf,@function\nleaf:\n.cfi_startproc\nxor %eax,%eax\nret\n.cfi_endproc\n.size leaf,.-leaf\n'
            source = root / 'a.s'
            source.write_text(assembly)
            obj = root / 'a.o'
            subprocess.run(['as', str(source), '-o', str(obj)], check=True)
            source.write_text(assembly.replace('add $1', 'add $2').replace('add $5', 'add $6'))
            subprocess.run(['as', str(source), '-o', str(root / 'target.o')], check=True)
            subprocess.run(['ld', '-e', 'caller', '-Ttext=0x400000', str(root / 'target.o'), '-o', str(root / 'target')], check=True)
            with (root / 'target').open('rb') as stream:
                elf = ELFFile(stream)
                data = elf.get_section_by_name('.text').data()
                extents = {e['initial_location']: e['address_range'] for e in elf.get_dwarf_info().EH_CFI_entries() if hasattr(e, 'lsda_pointer')}
                symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
            known = {'caller': symbols['caller']}

            def discover(*args, **kwargs):
                sites, sizes = object_sites(obj, data, 0x400000, extents, known, 'u.cpp')
                return sites, proven(sites, sizes, extents, known, exact_callers=True)

            def apply(root, rows):
                known.update({name: value[0] for name, value in rows.items()})

            with mock.patch.object(calls, 'discover', side_effect=discover), mock.patch.object(calls, 'apply', side_effect=apply):
                learned = calls.learn_chain(root, 'u.cpp', obj)
            self.assertEqual({name: row[0] for name, row in learned.items()}, {name: symbols[name] for name in ('callee', 'leaf')})

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
            subprocess.run(["ld", "-e", "caller", "-Ttext=0x400000", str(obj), "-o", str(target)], check=True)
            with target.open("rb") as stream:
                text = ELFFile(stream).get_section_by_name(".text").data()
            inferred, _ = object_sites(obj, text, base, extents, {}, "d/A.cpp")
            self.assertEqual(len(inferred), 2)
            self.assertEqual({s.proof for s in inferred}, {"unique-caller"})
            self.assertTrue(proven(inferred, sizes, extents, {})["callee"][1].startswith("aligned-unique-call:"))
            self.assertEqual({s.address for s in inferred}, {symbols["callee"]})
            # The linked target gives the callee a different body and extent,
            # while keeping every non-relocation byte of the caller exact.
            source.write_text(self.assembly.replace("xor %eax,%eax", "nop\nxor %eax,%eax"))
            subprocess.run(["as", str(source), "-o", str(root / "larger.o")], check=True)
            subprocess.run(["ld", "-e", "caller", "-Ttext=0x400000", str(root / "larger.o"), "-o", str(target)], check=True)
            with target.open("rb") as stream:
                elf = ELFFile(stream)
                text = elf.get_section_by_name(".text").data()
                larger_extents = {e["initial_location"]: e["address_range"] for e in elf.get_dwarf_info().EH_CFI_entries() if hasattr(e, "lsda_pointer")}
            exact, _ = object_sites(obj, text, base, larger_extents, {"caller": symbols["caller"]}, "d/A.cpp")
            self.assertEqual({s.proof for s in exact}, {"exact-caller"})
            self.assertEqual(proven(exact, sizes, larger_extents, {}), {})
            self.assertEqual(proven(exact, sizes, larger_extents, {}, exact_callers=True)["callee"][0], symbols["callee"])


if __name__ == "__main__":
    unittest.main()
