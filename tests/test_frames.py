"""Frame partitions come from pinned compiler output, slot pairs from real objects."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord import causal, frames
from concord.diff import read_function
from concord.model import Cause

FIXTURE = Path(__file__).parent / 'fixtures/gcc-4.4.3-frame/render.128r.expand'
SYMBOL = '_ZN7harvest6states10CPlayState6renderEv'


class FramesCommand(unittest.TestCase):
    def test_command_modes_parse_their_required_inputs(self):
        from concord.cli import build_parser

        args = build_parser().parse_args(['frames', '--harvest', '/checkout', '--census', '--save', '/tmp/frames.json'])
        self.assertTrue(args.census)
        args = build_parser().parse_args(['frames', '--harvest', '/checkout', '--unit', 'u.cpp', '--symbol', 'f', '--call', 'measure', '--probe-constructor', 'S', '--save', '/tmp/probe.json'])
        self.assertEqual(args.probe_constructor, 'S')
        self.assertFalse(args.apply)


class Partitions(unittest.TestCase):
    def test_anonymous_temporary_links_to_real_source_and_virtual_frame(self):
        text = FIXTURE.read_text()
        rows = frames.partitions(text, SYMBOL)
        group = next(g for g in rows if g['partition'] == 29)
        self.assertEqual((group['size'], group['alignment']), (8, 16))
        self.assertEqual(group['members'], [{'name': 'D.68804', 'offset': 0}])
        self.assertEqual([r['uid'] for r in group['references']], [784, 785])
        self.assertEqual(group['references'][0]['virtual_offsets'], [-64])
        self.assertEqual(group['references'][1]['virtual_offsets'], [-60])
        self.assertIn('CPosition2d.h', group['references'][0]['source']['path'])
        records = causal.rtl_function(text, SYMBOL)
        history = [{'pass': 'expand', **records[784]}]
        self.assertEqual(causal.decision(history, Cause.STACK_LAYOUT)['kind'], 'expand-stack-storage')

    def test_named_variables_sharing_a_partition_stay_together(self):
        rows = frames.partitions(FIXTURE.read_text(), SYMBOL)
        group = next(g for g in rows if g['partition'] == 36)
        self.assertEqual([m['name'] for m in group['members']], ['position'] * 4)
        self.assertEqual(frames.partitions(FIXTURE.read_text(), 'missing'), [])

    def test_late_slots_are_font_height_and_line_count_not_expand_partitions(self):
        symbol = '_ZN5daisy3gui15CGUIEnvironment13addStaticTextEPKwiPN2ox3gui11IGUIElementEPNS5_8IGUIFontEiS3_'
        directory = FIXTURE.parent
        records = causal.rtl_function((directory / 'static-text.128r.expand').read_text(), symbol)
        origins = frames.value_origins(records, ['D.67396', 'D.67401'])
        self.assertEqual(origins['D.67396']['fields'], ['D.67400.Height'])
        self.assertEqual(len(origins['D.67401']['fields']), 2)
        self.assertTrue(all('lines.' in f for f in origins['D.67401']['fields']))
        history = causal.histories(directory, symbol, {121})[121]
        decision = causal.decision(history, Cause.STACK_LAYOUT)
        self.assertEqual(decision['kind'], 'post-expand-stack-storage')
        self.assertEqual(decision['after']['pass'], 'ira')
        self.assertIn('D.67401', decision['before']['values'])

    @unittest.skipUnless(shutil.which('as'), 'GNU assembler is needed')
    def test_slot_constraints_use_real_stack_instructions_and_reject_repeated_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = 'mov 32(%rsp),%rax\nadd 64(%rsp),%rax\nmov %rax,16(%rsp)\nadd $3,%rax\nret\n'
            def build(name, code):
                source = root / (name + '.s')
                obj = root / (name + '.o')
                source.write_text('.text\n.global f\n.type f,@function\nf:\n' + code + '.size f,.-f\n')
                subprocess.run(['as', str(source), '-o', str(obj)], check=True)
                return read_function(obj, 'f')
            a = build('a', body)
            b = build('b', body.replace('32(%rsp)', '48(%rsp)'))
            rows = frames.slot_pairs(a, b)
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]['target_slot'], rows[0]['candidate_slot']), (32, 48))
            self.assertEqual(frames.slot_pairs(a, a), [])
            self.assertEqual(frames.slot_pairs(build('bp-a', body.replace('%rsp', '%rbp')), build('bp-b', body.replace('%rsp', '%rbp').replace('32(%rbp)', '48(%rbp)'))), [])
            self.assertEqual(frames.slot_pairs(build('twice-a', body * 2), b), [])


class TemporaryForms(unittest.TestCase):
    source = b'int A::f(const char* text, int width) {\n    int height = measure(S(text), width);\n    return height;\n}\n'

    def test_scoped_argument_dies_before_the_next_statement(self):
        forms = frames.temporary_forms(self.source, '_ZN1A1fEPKci', 'measure', 0, 'S')
        self.assertIn(b'measure(text, width)', forms['implicit-construction'])
        self.assertIn(b'int height;\n    {\n        S argumentValue(text);', forms['scoped-construction'])
        self.assertIn(b'height = measure(argumentValue, width);\n    }\n    return height;', forms['scoped-construction'])
        self.assertEqual(frames.temporary_forms(self.source, '_ZN1A1fEPKci', 'measure', 0, 'S'), forms)

    def test_callsite_source_returns_the_full_multiline_statement(self):
        source = self.source.replace(b'measure(S(text), width)', b'measure(S(text),\n        width)')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'src').mkdir()
            (root / 'src/u.cpp').write_bytes(source)
            row = frames.callsite_source(root, 'u.cpp', '_ZN1A1fEPKci', 3)
            self.assertEqual(row['line'], 2)
            self.assertEqual(row['end_line'], 3)
            self.assertTrue(row['text'].startswith('int height = measure('))

    def test_callsite_lookup_in_a_large_translation_unit(self):
        padding = b''.join(b'int padding%d() { return %d; }\n' % (i, i) for i in range(300))
        source = padding + self.source.replace(b'measure(S(text), width)', b'measure(S(text),\n        width)')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'src').mkdir()
            (root / 'src/u.cpp').write_bytes(source)
            for _ in range(5):
                row = frames.callsite_source(root, 'u.cpp', '_ZN1A1fEPKci', 303)
                self.assertEqual((row['line'], row['end_line']), (302, 303))
                self.assertTrue(row['text'].startswith('int height = measure('))

    def test_effectful_other_arguments_and_const_results_are_rejected(self):
        for source in (self.source.replace(b', width);', b', next());'), self.source.replace(b'int height', b'const int height')):
            with self.assertRaises(ValueError):
                frames.temporary_forms(source, '_ZN1A1fEPKci', 'measure', 0, 'S')

    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler is needed')
    def test_actual_constructor_conversion_and_destruction_contract(self):
        prefix = b'''#include <stdio.h>
struct S { S(const char*) { putchar('c'); } ~S() { putchar('d'); } };
int measure(const S&, int n) { putchar('m'); return n; }
struct A { static int f(const char*, int); };
'''
        suffix = b'int main() { int n=A::f("x", 3); printf("%d", n); }\n'
        forms = {'original': self.source, **frames.temporary_forms(self.source, '_ZN1A1fEPKci', 'measure', 0, 'S')}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in forms.items():
                path, executable = root / (name + '.cpp'), root / name
                path.write_bytes(prefix + source + suffix)
                subprocess.run(['g++', '-std=c++98', str(path), '-o', str(executable)], check=True)
                self.assertEqual(subprocess.check_output([str(executable)]), b'cmd3')
