"""Audit real libstdc++ objects and linked call sites."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from capstone import x86
from elftools.elf.elffile import ELFFile

from concord.symbol_audit import (
    analyze,
    decoder,
    explicit_return,
    return_users,
    sections,
    void_contract,
)
from concord.symbol_table import Row


@unittest.skipUnless(shutil.which('g++'), 'g++ is needed')
class SymbolAudit(unittest.TestCase):
    def test_stack_protected_return_and_rax_modification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'guarded.cpp'
            helper = root/'helper.cpp'
            source.write_text('''extern "C" long value_from(const char *);\nextern "C" void use_buffer(char *);\nextern "C" __attribute__((noinline)) long guarded(const char *input) {\n    char buffer[16]; buffer[0] = input[0]; buffer[15] = input[0] + 1;\n    long result = value_from(input); use_buffer(buffer); return result;\n}\nextern "C" __attribute__((noinline)) long guarded_modified(const char *input) {\n    char buffer[16]; buffer[0] = input[0]; buffer[15] = input[0] + 1;\n    long result = value_from(input); use_buffer(buffer); return result ^ 85;\n}\nint main() { return guarded("x") + guarded_modified("y"); }\n''')
            helper.write_text('''extern "C" long value_from(const char *input) { return input[0] + 17; }\nextern "C" void use_buffer(char *buffer) { volatile char used = buffer[0]; (void)used; }\n''')
            target = root/'guarded'
            subprocess.run(['g++', '-O2', '-fno-inline', '-fstack-protector', '-fno-pie', '-no-pie',
                            str(source), str(helper), '-o', str(target)], check=True, capture_output=True)
            with target.open('rb') as stream:
                symbols = {symbol.name: symbol for symbol in ELFFile(stream).get_section_by_name('.symtab').iter_symbols()}

            codes = {}
            for name in ('guarded', 'guarded_modified'):
                symbol = symbols[name]
                start, size = symbol['st_value'], symbol['st_size']
                codes[name] = next(data[start - base:start - base + size] for base, data in sections(target)
                                   if base <= start and start + size <= base + len(data))

            protected = list(decoder().disasm(codes['guarded'], symbols['guarded']['st_value']))
            self.assertTrue(any(instruction.mnemonic in ('sub', 'xor')
                                and len(instruction.operands) == 2
                                and instruction.operands[1].type == x86.X86_OP_MEM
                                and instruction.reg_name(instruction.operands[1].mem.segment) == 'fs'
                                and instruction.operands[1].mem.disp == 0x28
                                for instruction in protected))
            self.assertTrue(explicit_return(codes['guarded'], symbols['guarded']['st_value']))
            modified = list(decoder().disasm(codes['guarded_modified'], symbols['guarded_modified']['st_value']))
            rax_touch_after_saved_move = False
            for index, instruction in enumerate(modified[:-1]):
                operands = instruction.operands
                saved_move = (instruction.mnemonic == 'mov' and len(operands) == 2
                              and operands[0].type == x86.X86_OP_REG
                              and instruction.reg_name(operands[0].reg) == 'rax'
                              and operands[1].type == x86.X86_OP_REG
                              and instruction.reg_name(operands[1].reg) in ('rbx', 'rbp', 'r12', 'r13', 'r14', 'r15'))
                reads, writes = modified[index + 1].regs_access()
                if saved_move and any(modified[index + 1].reg_name(register) in {'rax', 'eax', 'ax', 'al', 'ah'}
                                      for register in (*reads, *writes)):
                    rax_touch_after_saved_move = True
                    break
            self.assertTrue(rax_touch_after_saved_move)
            self.assertFalse(explicit_return(codes['guarded_modified'], symbols['guarded_modified']['st_value']))

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
