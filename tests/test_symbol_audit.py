"""Audit real libstdc++ objects and linked call sites."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile

from concord.symbol_audit import analyze, return_users, sections, void_contract
from concord.symbol_table import Row


@unittest.skipUnless(shutil.which('g++'), 'g++ is needed')
class SymbolAudit(unittest.TestCase):
    def test_void_list_helper_name_is_suspect_on_real_iterator_returning_erase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'list.cpp'
            source.write_text('#include <list>\ntemplate class std::list<int*>;\nint main() { std::list<int*> x; return x.size(); }\n')
            obj, target = root/'list.o', root/'list'
            # The target's Ubuntu GCC 4.4.3 uses -fstack-protector, which guards only functions with
            # character arrays, so its list::erase has no canary; newer Ubuntu g++ defaults to
            # -fstack-protector-strong and would add one here.
            subprocess.run(['g++', '-std=c++98', '-O2', '-fno-inline', '-fno-stack-protector', '-g', '-fno-pie', '-c', str(source), '-o', str(obj)], check=True, capture_output=True)
            subprocess.run(['g++', '-no-pie', str(obj), '-o', str(target)], check=True, capture_output=True)
            with target.open('rb') as stream:
                symbols = list(ELFFile(stream).get_section_by_name('.symtab').iter_symbols())
            helper = next(s for s in symbols if void_contract(s.name))
            erase = next(s for s in symbols if '4listI' in s.name and '5eraseESt14_List_iterator' in s.name)
            # The compiler's actual declaration agrees that the helper is void.
            with obj.open('rb') as stream:
                dwarf = ELFFile(stream).get_dwarf_info()
                declarations = [die for cu in dwarf.iter_CUs() for die in cu.iter_DIEs()
                                if die.tag == 'DW_TAG_subprogram' and die.attributes.get('DW_AT_name')
                                and die.attributes['DW_AT_name'].value == b'_M_erase']
                self.assertTrue(declarations)
                self.assertTrue(all('DW_AT_type' not in die.attributes for die in declarations))
            row = Row(erase['st_value'], erase['st_size'], helper.name, 'manual:wrong-helper')
            report = analyze([row], target, [obj])
            [finding] = report['findings']
            self.assertEqual(finding['status'], 'suspect')
            self.assertTrue(finding['emitted_copies'])
            self.assertTrue(finding['explicit_return_sites'])
            self.assertTrue(any('void helper' in reason for reason in finding['reasons']))
            correct = Row(helper['st_value'], helper['st_size'], helper.name, 'manual:correct-helper')
            self.assertEqual(analyze([correct], target, [obj])['findings'][0]['status'], 'checked')

    def test_real_caller_reads_return_value_and_missing_names_stay_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'call.cpp'
            source.write_text('extern "C" __attribute__((noinline)) long value(long x) { return x+2; }\nint main(int n, char**) { return value(n)+3; }\n')
            target = root/'call'
            subprocess.run(['g++', '-O2', '-fno-pie', '-no-pie', str(source), '-o', str(target)], check=True, capture_output=True)
            with target.open('rb') as stream:
                symbol = next(s for s in ELFFile(stream).get_section_by_name('.symtab').iter_symbols() if s.name == 'value')
            self.assertTrue(return_users(sections(target), {symbol['st_value']})[symbol['st_value']])
            row = Row(symbol['st_value'], symbol['st_size'], 'unknown_name', 'manual:unknown')
            finding = analyze([row], target, [])['findings'][0]
            self.assertEqual(finding['status'], 'unverified-not-emitted')
            self.assertTrue(finding['return_value_call_sites'])

    def test_zeroing_rax_after_a_void_call_is_not_return_value_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'void.cpp'
            source.write_text('volatile int n; extern "C" __attribute__((noinline)) void sink() { n=1; }\nint main() { sink(); return 0; }\n')
            target = root/'void'
            subprocess.run(['g++', '-O2', '-fno-pie', '-no-pie', str(source), '-o', str(target)], check=True, capture_output=True)
            with target.open('rb') as stream:
                symbols = list(ELFFile(stream).get_section_by_name('.symtab').iter_symbols())
            sink = next(s for s in symbols if s.name == 'sink')
            self.assertFalse(return_users(sections(target), {sink['st_value']}).get(sink['st_value']))
            data = next(s for s in symbols if s.name == 'n')
            row = Row(data['st_value'], data['st_size'], 'n', 'manual:global')
            self.assertEqual(analyze([row], target, [])['findings'][0]['status'], 'not-executable')
