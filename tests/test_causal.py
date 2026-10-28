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
    def test_search_finishes_a_selected_candidate_before_the_author_gate(self):
        from concord.harvest import Evaluation
        from concord.transforms import Rewrite

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'src/u.cpp').write_bytes(b'int f() { return 1; }\n')
            harvest = mock.Mock(root=root)
            harvest.source.return_value = 'u.cpp'
            harvest.evaluate_overlays.return_value = {
                'baseline': Evaluation('baseline', 'u', fuzzy={'f': 90}),
                'r0': Evaluation('r0', 'u', fuzzy={'f': 100}, sections=[{'functions': [{'symbol': 'f', 'exact': True}]}]),
            }
            rewrite = Rewrite('named-temporary', Cause.OPERAND_ORDER, 'line 1: a value named', b'int f() { __typeof__(1) concordTmp0 = 1; return concordTmp0; }\n')
            clean = b'int f() { int value = 1; return value; }\n'
            report = {'unit': 'u.cpp', 'symbol': 'f', 'directory': tmp, 'findings': []}
            accepted = {'chosen': 'causal-candidate', 'applied': False, 'candidates': {'causal-candidate': {'oracle': None}}}
            with (mock.patch.object(causal, 'proposals', return_value=[rewrite]),
                  mock.patch('concord.authored.keep', return_value=True),
                  mock.patch('concord.finish.finish', return_value=clean) as finish,
                  mock.patch('concord.authored.apply', return_value=accepted) as author):
                result = causal.search(harvest, report)
            self.assertEqual(finish.call_count, 1)
            self.assertEqual(author.call_args.kwargs['candidate'], clean)
            self.assertEqual(Path(result['candidate_path']).read_bytes(), clean)

    def test_proposal_locations_above_small_integer_cache_do_not_corrupt_nodes(self):
        import sys

        code = '''from concord import causal
source = b'\\n' * 300 + b'int f(int a,int b) {\\n int x = a+3;\\n int y = b+5;\\n return x*y;\\n}\\n'
rows = causal.proposals(source, '_Z1fii', {302,303,304}, set())
assert rows
assert all(row.source.startswith(b'\\n'*300) for row in rows)
'''
        done = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', code], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_expression_identity_preserves_precedence_and_rejects_non_cpp(self):
        self.assertEqual(causal.expression_key('(a + (b * c))'), causal.expression_key('a + b * c'))
        self.assertNotEqual(causal.expression_key('(a + b) * c'), causal.expression_key('a + b * c'))
        self.assertIsNone(causal.expression_key('MEM[index: ivtmp.5]'))

    def test_inline_scope_locations_belong_to_the_correct_declaration(self):
        dump = ''';; Function f (_Z1fv)
{ Scope block #0
  { Scope block #1 src/caller.cpp:42 Originating from : inline helper;
    { Scope block #2 src/text.h:99
      intD.2 lenD.4;
    }
  }
  { Scope block #3 src/caller.cpp:58
    intD.2 lenD.5;
  }
}
'''
        scopes = causal.tree_scopes(dump, '_Z1fv')
        self.assertEqual(scopes['lenD.4'], [{'path': 'src/caller.cpp', 'line': 42}, {'path': 'src/text.h', 'line': 99}])
        self.assertEqual(scopes['lenD.5'], [{'path': 'src/caller.cpp', 'line': 58}])

    def test_real_string_copy_joins_instruction_to_recycled_exit_phi(self):
        directory = FIXTURE.parent / 'gcc-4.4.3-string-order'
        provenance = json.loads((directory / 'provenance.json').read_text())
        for name, hashes in provenance['inputs'].items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), hashes['fixture_sha256'])
        trace = causal.tree_comparison(directory, provenance['symbol'], 1201)
        self.assertEqual(trace['status'], 'observed-ssa-pair')
        self.assertEqual([v['name'] for v in trace['operands']], ['lD.27351_697', 'lenD.27349_929'])
        length = trace['operands'][1]
        self.assertEqual(length['creation']['pass'], 'vrp1')
        self.assertEqual(length['recycled_from']['name'], 'D.27535_929')
        self.assertEqual(length['recycled_from']['definition']['inputs'], ['amountD.27533_870'])
        self.assertEqual(trace['join']['expanded']['text'], 'if (l <= len)')
        self.assertEqual(trace['order_changes'][0]['after']['pass'], 'vrp1')
        self.assertEqual(trace['orientation_changes'][0]['after']['pass'], 'dce1')

    def test_real_tree_dumps_preserve_versions_and_diagnostic_block_mapping(self):
        directory = FIXTURE.parent / 'gcc-4.4.3-tree'
        symbol = '_Z1fPKci'
        provenance = json.loads((directory / 'provenance.json').read_text())
        for name, digest in provenance['artifacts'].items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest)
        parsed = causal.tree_function((directory / 'probe.122t.uncprop').read_text(), symbol)
        self.assertEqual(parsed['comparisons'][-1]['operands'], ['lenD.2073_23', 'iD.2075_45'])
        history = causal.tree_history(directory, symbol, ['lenD.2073_23', 'iD.2075_45'])
        self.assertEqual(history['operands'][0]['creation']['pass'], 'vrp1')
        self.assertEqual(history['comparisons'][2]['pass'], 'ch')
        text = (directory / 'probe.128r.expand').read_text()
        self.assertEqual(causal.expanded_statements(text, symbol)[43]['block'], 7)
        trace = causal.tree_comparison(directory, symbol, 43)
        self.assertEqual(trace['status'], 'observed-ssa-pair')
        self.assertEqual(trace['join']['method'], 'temporary-expression-replacement')
        self.assertEqual(trace['operands'][1]['name'], 'iD.2075_45')

    def test_final_body_is_used_after_eh_tree_and_intermediate_diagnostics(self):
        text = ''';; Function f (_Z1fv)
f ()
Eh tree:
  1 cleanup tree_label:<L0>
{
<bb 2>:
  if (aD.1_2 < bD.2_3)
}
Folding statement:
<bb 2>:
  if (aD.1_2 < bD.2_3)
f ()
Eh tree:
  1 cleanup tree_label:<L0>
{
<bb 2>:
  if (bD.2_3 > aD.1_2)
}
'''
        comparisons = causal.tree_function(text, '_Z1fv')['comparisons']
        self.assertEqual(len(comparisons), 1)
        self.assertEqual(comparisons[0]['operands'], ['bD.2_3', 'aD.1_2'])

    def test_proposals_leave_the_compared_line_and_other_functions_unchanged(self):
        source = b'''int f(int a, int b)
{
    int first = a + 3;
    int second = b + 5;
    if (first < second)
        return first;
    return second;
}
int g() { return 1; }
'''
        rewrites = causal.proposals(source, '_Z1fii', {4}, {5})
        self.assertTrue(rewrites)
        for rewrite in rewrites:
            self.assertIn(b'if (first < second)', rewrite.source)
            self.assertTrue(rewrite.source.endswith(b'int g() { return 1; }\n'))

    def test_tree_definitions_keep_phi_inputs_locations_and_function_identity(self):
        dump = ''';; Function f (_Z1fPKc)
<bb 2>:
  [/out/probe.cpp : 3] lenD.123_4 = 0;
<bb 3>:
  # lenD.123_5 = PHI <lenD.123_4(2), lenD.123_6(3)>
  [/out/probe.cpp : 4] lenD.123_6 = lenD.123_5 + 1;
  [/out/probe.cpp : 7] if (iD.124_8 <= lenD.123_5)
    goto <bb 3>;
;; Function g (_Z1gv)
<bb 2>:
  lenD.123_5 = 99;
'''
        parsed = causal.tree_function(dump, '_Z1fPKc')
        self.assertEqual(parsed['definitions']['lenD.123_5']['inputs'], ['lenD.123_4', 'lenD.123_6'])
        self.assertEqual(parsed['definitions']['lenD.123_6']['source'], {'path': '/out/probe.cpp', 'line': 4})
        self.assertEqual(parsed['comparisons'][0]['operands'], ['iD.124_8', 'lenD.123_5'])
        self.assertEqual(parsed['comparisons'][0]['block'], 3)
        self.assertEqual(causal.tree_function(dump, '_Z7missingv')['definitions'], {})

    def test_tree_history_reports_exit_phi_creation_before_mirrored_condition(self):
        header = ';; Function f (_Z1fv)\n'
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'trace.059t.mergephi2').write_text(header + '''<bb 2>:
  # lenD.1_10 = PHI <0(1), lenD.1_11(2)>
  lenD.1_11 = lenD.1_10 + 1;
<bb 3>:
  # iD.2_20 = PHI <0(1), iD.2_21(3)>
  iD.2_21 = iD.2_20 + 1;
  if (lenD.1_10 >= iD.2_20)
''')
            (directory / 'trace.060t.vrp1').write_text(header + '''<bb 2>:
  # lenD.1_10 = PHI <0(1), lenD.1_11(2)>
  lenD.1_11 = lenD.1_10 + 1;
<bb 4>:
  # lenD.1_30 = PHI <lenD.1_10(2)>
<bb 3>:
  # iD.2_20 = PHI <0(1), iD.2_21(3)>
  iD.2_21 = iD.2_20 + 1;
  if (lenD.1_30 >= iD.2_20)
''')
            (directory / 'trace.066t.ch').write_text(header + '''<bb 4>:
  # lenD.1_30 = PHI <lenD.1_11(2), 0(1)>
<bb 3>:
  # iD.2_20 = PHI <0(1), iD.2_21(3)>
  iD.2_21 = iD.2_20 + 1;
  if (iD.2_21 <= lenD.1_30)
''')
            history = causal.tree_history(directory, '_Z1fv', ['iD.2_21', 'lenD.1_30'])
        self.assertEqual([s['pass'] for s in history['comparisons']], ['mergephi2', 'vrp1', 'ch'])
        self.assertEqual(history['operands'][1]['creation']['pass'], 'vrp1')
        self.assertEqual(history['operands'][1]['creation']['definition']['kind'], 'phi')
        self.assertEqual(history['order_changes'][0]['after']['pass'], 'vrp1')
        self.assertEqual(history['order_changes'][0]['before']['operands'], ['lenD.1_10', 'iD.2_20'])
        self.assertEqual(history['orientation_changes'][0]['after']['pass'], 'ch')

    def test_recycled_version_follows_its_previous_owner_before_reuse(self):
        header = ';; Function f (_Z1fv)\n'
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'trace.057t.fre').write_text(header + '''<bb 2>:
  [probe.cpp : 3] amountD.3_4 = countD.4_5 + 1;
  [probe.cpp : 4] D.6_30 = amountD.3_4;
  if (lenD.1_10 >= iD.2_20)
''')
            (directory / 'trace.060t.vrp1').write_text(header + '''<bb 2>:
  # lenD.1_30 = PHI <lenD.1_10(1)>
  iD.2_20 = 0;
  if (iD.2_20 <= lenD.1_30)
''')
            trace = causal.tree_history(directory, '_Z1fv', ['iD.2_20', 'lenD.1_30'])
        recycled = trace['operands'][1]['recycled_from']
        self.assertEqual(recycled['name'], 'D.6_30')
        self.assertEqual(recycled['definition']['source'], {'path': 'probe.cpp', 'line': 4})
        dependencies = {v['name']: v for v in trace['recycling_dependencies']}
        self.assertEqual(dependencies['amountD.3_4']['creation']['definition']['source']['line'], 3)
        self.assertEqual(dependencies['D.6_30']['last_definition']['definition']['text'], 'D.6_30 = amountD.3_4;')

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
