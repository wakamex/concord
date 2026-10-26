"""Scratch-state replay rejects histories that omit resets or skip searches."""

import copy
import json
import unittest
from pathlib import Path

from concord.scratch import _records, compare, summarize, validate_events


def events():
    rows = []
    for index, (before, after, register) in enumerate([(0, 3, 2), (3, 0, None), (0, 1, 0)]):
        rows.append({'index': index, 'before': before, 'after': after, 'register': register,
                     'allocation_order': list(range(4)), 'function': 'f' if index < 2 else 'g',
                     'uid': index + 1, 'success': register is not None,
                     'register_name': str(register), 'class': 'r', 'mode': 15})
    return rows


class ScratchHistory(unittest.TestCase):
    def test_failed_search_is_a_reset_and_remains_in_history(self):
        rows = events()
        validate_events(rows, 4)
        report = summarize(rows, {'g'})
        self.assertEqual(report['failed_searches'], 1)
        self.assertEqual(report['targets'][0]['last_reset'], 1)
        self.assertEqual(report['targets'][0]['history_range'], [1, 2])

    def test_omitted_search_or_bad_transition_is_rejected(self):
        rows = events()
        for change in ('omit', 'state', 'reset', 'register'):
            damaged = copy.deepcopy(rows)
            if change == 'omit':
                del damaged[1]
            elif change == 'state':
                damaged[2]['before'] = 3
            elif change == 'reset':
                damaged[1]['after'] = 1
            else:
                damaged[0]['register'] = 1
            with self.assertRaises(ValueError):
                validate_events(damaged, 4)

    def test_runtime_allocation_order_is_used(self):
        rows = events()[:1]
        rows[0]['allocation_order'] = [3, 2, 1, 0]
        rows[0]['register'] = 1
        validate_events(rows, 4)

    def test_cli_accepts_existing_trace_and_reference(self):
        from concord.cli import build_parser
        args = build_parser().parse_args(['scratch-history', '--harvest', '/tmp/h', '--unit', 'u.cpp',
                                         '--directory', '/tmp/d', '--reference', '/tmp/r.json', '--save', '/tmp/o.json'])
        self.assertEqual(args.command, 'scratch-history')


class RealScratchHistory(unittest.TestCase):
    fixture = Path(__file__).parent / 'fixtures/gcc-4.4.3-scratch-history'

    def test_canonical_reader_execution_matches_real_rtl(self):
        trace = json.loads((self.fixture / 'events.json').read_text())
        rows = trace['events']
        validate_events(rows, trace['profile']['register_count'])
        self.assertEqual(len(rows), 25)
        wanted = {
            '_ZN2ox6TArrayIN5daisy5scene12CXFileReader14SXAnimationSetEED1Ev': ('di', 5),
            '_ZN5daisy5scene12CXFileReader20getNextTokenAsStringERN2ox4core7CStringIcEE': ('r12', 41),
        }
        records = _records(self.fixture, set(wanted))
        for row in rows:
            if row['function'] not in wanted:
                continue
            self.assertEqual((row['register_name'], row['before']), wanted[row['function']])
            record = records[row['function']][row['uid']]
            self.assertEqual(record['pattern'], row['input_pattern'])
        self.assertEqual(len(trace['object_sha256']), 64)
        self.assertEqual(trace['compiler_sha256'],
                         'cbd886f07b000b49ffd7d3f41ef42f27c8c286e6b597d79b21a35010b84a584c')

    def test_reader_history_names_predecessors_and_added_searches(self):
        old = json.loads((self.fixture / 'events.json').read_text())
        new = json.loads((self.fixture / 'restored-events.json').read_text())
        symbol = '_ZN5daisy5scene12CXFileReader20getNextTokenAsStringERN2ox4core7CStringIcEE'
        reference = {'events': old['events'], 'metadata': {'matched_functions': [symbol]}}
        differences = compare(new, reference)
        row = next(r for r in differences if r['function'] == symbol)
        self.assertEqual((row['reference_before'], row['current_before']), (41, 38))
        self.assertEqual(row['reference_register'], 'r12')
        self.assertEqual(row['current_register'], 'r9')
        self.assertIn('parseDataObjectAnimationKey', row['current_predecessor']['function'])
        self.assertIn('__uninitialized_move_a', row['reference_predecessor']['function'])
        self.assertTrue(row['reference_function_exact'])
        self.assertEqual(row['source_edit_status'], 'counterfactual-required')
        self.assertTrue(row['history_delta']['changed_search_counts'])
