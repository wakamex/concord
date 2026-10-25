"""Budget attribution reads real pinned-compiler dump records."""
import unittest
from pathlib import Path

from concord.inline_budget import compare, load, parse

FIXTURE = Path(__file__).parent / 'fixtures/gcc-4.4.3-inline/reader.inline'


class InlineBudget(unittest.TestCase):
    def test_real_dump_estimates_and_decisions(self):
        report = load(FIXTURE)
        self.assertEqual(report['starting_insns'], 9029)
        self.assertEqual(report['final_insns'], 13045)
        self.assertEqual(report['inlined_calls'], 977)
        self.assertTrue(any(e.get('outcome') == 'inlined' for e in report['events']))
        self.assertTrue(report['functions'])
        self.assertEqual(len(report['sha256']), 64)
        self.assertEqual(compare(report, report)['functions'], [])
        self.assertEqual(compare(report, report)['inline_edge_deltas'], [])

    def test_changed_function_and_edge_are_reported_separately(self):
        before = load(FIXTURE)
        after = load(FIXTURE)
        after['starting_insns'] += 20
        after['functions'][0]['initial_insns'] += 20
        after['events'].append({'callee': 'copy', 'caller': 'parser', 'outcome': 'inlined'})
        result = compare(before, after)
        self.assertEqual(result['starting_insns_delta'], 20)
        self.assertEqual(result['functions'][0]['delta'], 20)
        self.assertEqual(result['inline_edge_deltas'], [{'callee': 'copy', 'caller': 'parser', 'before': 0, 'after': 1}])

    def test_command_saves_comparison_with_dump_hashes(self):
        import contextlib
        import io
        import json
        import tempfile

        from concord.cli import main

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / 'report.json'
            self.assertEqual(main(['inline-budget', str(FIXTURE), '--against', str(FIXTURE), '--save', str(output)]), 0)
            result = json.loads(output.read_text())
            self.assertEqual(result['sha256'], result['baseline']['sha256'])
            self.assertEqual(result['comparison']['starting_insns_delta'], 0)

    def test_incomplete_dump_is_rejected(self):
        with self.assertRaises(ValueError):
            parse('Deciding on inlining.  Starting with 9029 insns.')
