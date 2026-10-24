"""Causal census selection, source identity, and distinct-function weighting."""

import shutil
import subprocess
import unittest
from pathlib import Path

from concord.census import delta, rank, rewrites, select
from concord.harvest import Evaluation


def function(address, size, symbol='f'):
    return {'address': address, 'size': size, 'symbol': symbol, 'unit': 'u.cpp', 'fuzzy': 99}


def finding(path, line, offset, status='observed-candidate-decision'):
    return {'target_offset': offset, 'status': status, 'source': {'path': path, 'line': line, 'text': 'a + b'},
            'cause': 'operand-order', 'decision': {'after': {'pass': 'expand'}}}


class Census(unittest.TestCase):
    def test_counterfactual_rewrites_only_the_named_line(self):
        source = b'int f(int a, int b) {\nfor (int i=0;i<a+1;++i) b+=i;\nreturn a<b;\n}\n'
        candidates = rewrites(source, 2)
        self.assertEqual({c.transform for c in candidates}, {'compare-operand-order', 'loop-form'})
        self.assertTrue(all(b'return a<b;' in c.source for c in candidates))
        self.assertEqual(rewrites(source, 1), [])
        if shutil.which('g++'):
            for candidate in candidates:
                done = subprocess.run(['g++', '-x', 'c++', '-std=c++98', '-fsyntax-only', '-'], input=candidate.source, capture_output=True, check=False)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_counterfactual_separates_exact_losses_and_inexact_score_losses(self):
        before = Evaluation('before', 'u', sections=[{'functions': [{'symbol': 'a', 'exact': True}]}], fuzzy={'a': 100, 'b': 99})
        after = Evaluation('after', 'u', sections=[{'functions': [{'symbol': 'a', 'exact': False}]}], fuzzy={'a': 99, 'b': 98})
        result = delta(before, after)
        self.assertEqual(result['lost_exact'], ['a'])
        self.assertEqual({c['symbol'] for c in result['worse']}, {'a', 'b'})

    def test_multiple_sites_and_duplicate_reports_count_function_bytes_once(self):
        a = {'function': function(100, 1000), 'findings': [finding('src/a.h', 4, 1), finding('/work/src/a.h', 4, 2)]}
        b = {'function': function(200, 600, 'g'), 'findings': [finding('src/x/../a.h', 4, 3)]}
        result = rank(Path('/repo'), [a, a, b])
        [row] = result['constructs']
        self.assertEqual((row['function_count'], row['function_bytes'], row['sites']), (2, 1600, 3))
        self.assertEqual(row['path'], 'src/a.h')
        self.assertTrue(row['header'])
        self.assertEqual(result['traced_sites'], 3)

    def test_count_precedes_size_and_unresolved_sites_are_separate(self):
        reports = [
            {'function': function(1, 10000), 'findings': [finding('big.cpp', 1, 1), finding('x.h', 9, 2, 'unresolved')]},
            {'function': function(2, 20), 'findings': [finding('shared.h', 2, 3)]},
            {'function': function(3, 30), 'findings': [finding('shared.h', 2, 4)]},
        ]
        result = rank(Path('/repo'), reports)
        self.assertEqual([r['path'] for r in result['constructs']], ['shared.h', 'big.cpp'])
        self.assertEqual(result['unresolved_sites'], 1)

    def test_selection_uses_target_size_excludes_gui_and_keeps_unproven_100(self):
        def unit(path, score, matched, address):
            return {'metadata': {'source_path': path}, 'measures': {'matched_code': '10' if matched else '0', 'total_code': '10'},
                    'functions': [{'name': 'f', 'size': '100', 'fuzzy_match_percent': score,
                                   'metadata': {'virtual_address': str(address)}}]}
        result = select({'units': [unit('src/u.cpp', 100, False, 1), unit('src/daisy/gui/u.cpp', 99, False, 2),
                                   unit('src/v.cpp', 96.9, False, 3), unit('src/w.cpp', 99, True, 4)]})
        self.assertEqual(result, [{'unit': 'u.cpp', 'symbol': 'f', 'address': 1, 'size': 100, 'fuzzy': 100}])
