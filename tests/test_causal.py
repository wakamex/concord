"""Instruction histories from pinned GCC 4.4.3 dump fixtures."""

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from concord import causal
from concord.model import Cause

FIXTURE = Path(__file__).parent / 'fixtures' / 'gcc-4.4.3-causal'
CODE = bytes.fromhex('8d460583c7030fafc7c3')


class CompilerDecisions(unittest.TestCase):
    @unittest.skipUnless(shutil.which('as'), 'GNU assembler is needed')
    def test_long_labels_are_recovered_from_real_assembler_line_numbers(self):
        symbol = '_Z' + 'f' * 180
        assembly = f'.text\n.globl {symbol}\n.type {symbol}, @function\n{symbol}:\nret # 12 return_internal\n.size {symbol}, .-{symbol}\n'
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'long.s'
            source.write_text(assembly)
            done = subprocess.run(['as', '--64', '-aln', str(source), '-o', str(Path(tmp) / 'long.o')], capture_output=True, text=True, check=True)
        listing = causal.complete_listing(done.stdout, assembly)
        records = causal.listing_instructions(listing, symbol, 0, b'\xc3')
        self.assertEqual(records[0]['uid'], 12)

    def test_real_reader_scratch_choices_are_introduced_in_peephole2(self):
        directory = FIXTURE.parent / 'gcc-4.4.3-reader-scratch'
        metadata = json.loads((directory / 'provenance.json').read_text())
        wanted = {symbol: {uid} for symbol, uid in metadata['instructions'].items()}
        histories = causal.histories_many(directory, wanted)
        for symbol, uid in metadata['instructions'].items():
            decision = causal.decision(histories[symbol][uid], Cause.REGISTER_ALLOCATION)
            self.assertEqual(decision['kind'], 'instruction-introduction')
            self.assertEqual(decision['after']['pass'], 'peephole2')
            self.assertEqual(decision['after']['pass_number'], 182)
            pattern = decision['after']['pattern']
            if 'getNextTokenAsString' in symbol:
                self.assertIn('<variable>.used', pattern)
                self.assertEqual(decision['after']['source']['path'], 'src/ox/core/CString.h')
                self.assertIn('r9', pattern)
            else:
                self.assertIn('(reg:DI 6 bp)', pattern)
                self.assertIn('(const_int 8', pattern)
                self.assertEqual(decision['after']['source']['path'], 'src/ox/TArray.h')
        for stage, evidence in metadata['inputs'].items():
            self.assertEqual(hashlib.sha256((directory / ('trace.' + stage)).read_bytes()).hexdigest(),
                             evidence['fixture_sha256'])

    def test_ira_assigns_real_registers_to_the_expression(self):
        history = causal.histories(FIXTURE, '_Z1fii', {8})[8]
        decision = causal.decision(history, Cause.REGISTER_ALLOCATION)
        self.assertEqual(decision['kind'], 'physical-register-assignment')
        self.assertEqual(decision['after']['pass'], 'ira')
        self.assertIn('(reg:SI 63)', decision['before']['pattern'])
        self.assertIn('(reg:SI 5 di [63])', decision['after']['pattern'])
        self.assertEqual(decision['after']['source'], {'path': 'probe.cpp', 'line': 1})

    def test_late_split_has_its_own_instruction_id(self):
        history = causal.histories(FIXTURE, '_Z1fii', {25})[25]
        decision = causal.decision(history, Cause.INSTRUCTION_SELECTION)
        self.assertEqual(decision['after']['pass'], 'split2')
        self.assertIn('plus:DI', decision['after']['pattern'])
        self.assertEqual(decision['kind'], 'instruction-introduction')

    def test_reordered_unchanged_records_are_retained(self):
        text = (FIXTURE / 'probe.cpp.172r.ira').read_text()
        records = {int(m[1]): causal.expression(text, m.start()) for m in causal.INSN.finditer(text)}
        header = ';; Function int f(int, int) (_Z1fii)\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'trace.001r.before').write_text(header + records[8] + '\n' + records[9])
            (path / 'trace.002r.after').write_text(header + records[9] + '\n' + records[8])
            history = causal.histories(path, '_Z1fii', {8})[8]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['pattern'], history[1]['pattern'])
        decision = causal.decision(history, Cause.BLOCK_ORDER)
        self.assertEqual(decision['after']['pass'], 'after')
        self.assertEqual(decision['kind'], 'instruction-order-change')

    def test_listing_maps_actual_bytes_to_ids_and_rejects_disagreement(self):
        listing = (FIXTURE / 'listing.txt').read_text()
        records = causal.listing_instructions(listing, '_Z1fii', 0, CODE)
        self.assertEqual({o: r['uid'] for o, r in records.items()}, {0: 25, 3: 8, 6: 9, 9: 28})
        with self.assertRaisesRegex(ValueError, 'bytes disagree'):
            causal.listing_instructions(listing, '_Z1fii', 0, b'\x90' * len(CODE))

    def test_unknown_function_is_not_confused_with_reused_ids(self):
        self.assertEqual(causal.rtl_function((FIXTURE / 'probe.cpp.172r.ira').read_text(), '_Z7missingii'), {})
        self.assertEqual(causal.histories(FIXTURE, '_Z7missingii', {8}), {8: []})

    def test_balanced_parser_ignores_quoted_parentheses(self):
        text = '(insn 1 (asm_input "x(\\\")")) trailing'
        self.assertEqual(causal.expression(text, 0), text.removesuffix(' trailing'))
        with self.assertRaises(ValueError):
            causal.expression('(insn 1', 0)

    def test_source_line_is_returned_without_reading_outside_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'probe.cpp').write_text('int x = a + 3;\n')
            self.assertEqual(causal.source_construct(root, {'path': '/work/probe.cpp', 'line': 1})['text'], 'int x = a + 3;')
            self.assertNotIn('text', causal.source_construct(root, {'path': '/etc/passwd', 'line': 1}))

    def test_diagnostic_codegen_change_is_rejected(self):
        old = mock.Mock(code=b'abc', relocations=frozenset())
        new = mock.Mock(code=b'abd', relocations=frozenset())
        harvest = mock.Mock()
        harvest.slug.return_value = 'u'
        harvest.objects = Path('/objects')
        with mock.patch.object(causal, 'read_function', side_effect=[old, new]), self.assertRaisesRegex(ValueError, 'diagnostic flags changed'):
            causal.analyze(harvest, 'u.cpp', 'f', Path('/dumps'))

    def test_fixture_provenance_has_pinned_compiler_and_real_command(self):
        metadata = json.loads((FIXTURE / 'provenance.json').read_text())
        self.assertEqual(metadata['compiler_version'], 'g++ (Ubuntu 4.4.3-4ubuntu5) 4.4.3')
        self.assertIn('-fdump-rtl-all', metadata['command'])

    def test_stale_or_wrong_unit_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            source = root / 'src' / 'u.cpp'
            source.write_bytes(b'int a;')
            metadata = {'inputs': {'src/u.cpp': hashlib.sha256(source.read_bytes()).hexdigest()}}
            causal.verify_inputs(root, 'u.cpp', metadata)
            with self.assertRaisesRegex(ValueError, 'does not name'):
                causal.verify_inputs(root, 'v.cpp', metadata)
            source.write_bytes(b'int b;')
            with self.assertRaisesRegex(ValueError, 'source input changed'):
                causal.verify_inputs(root, 'u.cpp', metadata)


if __name__ == '__main__':
    unittest.main()
