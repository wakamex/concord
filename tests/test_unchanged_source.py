"""Overlay exemptions require unchanged bodies and complete compiler evidence."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from concord import authored
from concord.harvest import Evaluation
from concord.unchanged_source import _dependencies, changed_functions


class UnchangedOverlay(unittest.TestCase):
    def measure(self, candidate, *, early=False, missing=False, unresolved=False, after_only=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'src/H.h'
            source.parent.mkdir()
            source.write_bytes(b'int helper() { return 1; }\nint edited() { return 1; }\n')
            config = root / 'config/1.18-linux-amd64'
            config.mkdir(parents=True)
            (config / 'symbols.tsv').write_text('address\tsize\tsymbol\n')
            reports = root / 'reports'
            reports.mkdir()
            harvest = Mock(root=root, build='1.18-linux-amd64', reports=reports)
            harvest.dependents.return_value = ['A.cpp']
            evaluations = {}
            for side, score in [('before', 50), ('after', 60)]:
                path = root / f'{side}.o'
                path.touch()
                name = 'unresolved ???' if unresolved else 'int helper()'
                edge = f'{name}/2(2): (inline copy in int caller()/1) availability:available 2 insns\n'
                if early or (after_only and side == 'before'):
                    edge = ''
                (root / f'{side}.042i.inline').write_text(
                    'Deciding on inlining.  Starting with 10 insns.\n'
                    'Inlined 1 calls, eliminated 0 functions, 10 insns turned to 12 insns.\n'
                    'Reclaiming functions:callgraph:\n' + edge +
                    'int caller()/1(1): availability:available 10 insns\n'
                    ';; Function int caller() (_Z6callerv)\n')
                (root / f'{side}.025t.einline2').write_text(
                    ';; Function int caller() (_Z6callerv)\n' +
                    (' Inlining int helper() into int caller().\n' if early else ''))
                evaluations[f'{side}-0'] = Evaluation(side, 'A', object=path,
                    sections=[{'functions': [{'symbol': '_Z6callerv', 'exact': False, 'size': 100}]}],
                    fuzzy={'_Z6callerv': score})
            if missing:
                (root / 'before.042i.inline').unlink()
            harvest.evaluate_overlays.return_value = evaluations
            with patch('concord.authored.oracle.gate_objects', return_value=(True, {'verdict': 'agree'})) as gate:
                result = authored.overlay(harvest, {'H.h': candidate})
            return result, gate

    def test_unchanged_body_and_inlined_callee_skip_oracle(self):
        result, gate = self.measure(b'int helper() { return 1; }\nint edited() { return 2; }\n')
        self.assertTrue(result['accepted'])
        gate.assert_not_called()
        self.assertEqual(result['gains'][0]['unchanged_source']['inlined'], ['helper'])
        self.assertNotIn('oracle', result['gains'][0])

    def test_changed_inlined_callee_runs_oracle_in_either_compile(self):
        for options in ({}, {'early': True}, {'after_only': True}):
            with self.subTest(options=options):
                result, gate = self.measure(b'int helper() { return 2; }\nint edited() { return 1; }\n', **options)
                self.assertTrue(result['accepted'])
                gate.assert_called_once()

    def test_declaration_change_disables_all_exemptions(self):
        result, gate = self.measure(b'int helper() { return 1; }\nint edited() { return 1; }\nint value;\n')
        self.assertTrue(result['accepted'])
        gate.assert_called_once()

    def test_missing_dump_or_unresolved_inline_name_runs_oracle(self):
        for options in ({'missing': True}, {'unresolved': True}):
            with self.subTest(options=options):
                _, gate = self.measure(b'int helper() { return 1; }\nint edited() { return 2; }\n', **options)
                gate.assert_called_once()


class SourceEvidence(unittest.TestCase):
    def test_real_gcc_early_inline_closure(self):
        fixture = Path(__file__).parent / 'fixtures/gcc-4.4.3-unchanged-source'
        ipa = (fixture / 'fixture.042i.inline').read_text()
        early = (fixture / 'fixture.025t.einline2').read_text()
        self.assertEqual(_dependencies(ipa, early, '_Z6calleri'), ('caller', {'middle', 'leaf'}))
        self.assertEqual(_dependencies(ipa, early, '_Z4leafi'), ('leaf', set()))
        with self.assertRaises(ValueError):
            _dependencies(ipa, early, '_Z7missingv')

    def test_transitive_ipa_copies_and_early_inline_are_combined(self):
        ipa = '''Deciding on inlining.  Starting with 10 insns.
Inlined 2 calls, eliminated 0 functions, 10 insns turned to 12 insns.
Reclaiming functions:callgraph:
int caller()/1(1): availability:available 10 insns
int middle()/2(2): (inline copy in int caller()/1) availability:available 2 insns
int leaf()/3(3): (inline copy in int middle()/2) availability:available 2 insns
;; Function int caller() (_Z6callerv)
'''
        early = ';; Function int leaf() (_Z4leafv)\n Inlining int helper() into int leaf().\n'
        self.assertEqual(_dependencies(ipa, early, '_Z6callerv'), ('caller', {'middle', 'leaf', 'helper'}))

    def test_body_changes_resolve_namespaces_templates_and_overloads(self):
        old = b'namespace n { template<class T> struct Box { int f(T a) { return 1; } }; }'
        new = old.replace(b'return 1', b'return 2')
        self.assertEqual(changed_functions({'H.h': old}, {'H.h': new}), {'n::Box::f'})
        old = b'namespace n { int f(int value) { return 1; } int f(float value) { return 2; } }'
        new = old.replace(b'return 1', b'return 3')
        self.assertEqual(changed_functions({'H.h': old}, {'H.h': new}), {'n::f'})
        old = b'namespace n { int Box<int>::f() { return 1; } }'
        new = old.replace(b'return 1', b'return 2')
        self.assertEqual(changed_functions({'H.h': old}, {'H.h': new}), {'n::Box::f'})

    def test_non_body_changes_anywhere_disable_the_overlay(self):
        old = b'int f() { return 1; }\n'
        for declaration in (b'int value;', b'#define VALUE 1', b'#include "H.h"', b'struct X { int value; };', b'int g();'):
            with self.subTest(declaration=declaration):
                self.assertIsNone(changed_functions({'A.h': old, 'B.h': b''},
                    {'A.h': old.replace(b'return 1', b'return 2'), 'B.h': declaration}))
        for new in (b'int f() {\n#define VALUE 1\nreturn 1; }', b'int f() { return ; ??? }'):
            self.assertIsNone(changed_functions({'H.h': old}, {'H.h': new}))
