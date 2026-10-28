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

    def test_smaller_function_rejections_keep_their_candidate(self):
        report = load(FIXTURE)
        reason = '--param max-inline-insns-auto limit reached'
        self.assertGreater(report['rejections'].get(reason, 0), 0)
        rejected = [event for event in report['events'] if event.get('reason') == reason]
        self.assertTrue(all(event['outcome'] == 'rejected' for event in rejected))
        self.assertTrue(all('pthread_equal' not in event['callee'] for event in rejected))
        self.assertTrue(any('parseDataObject' in event['callee'] for event in rejected))

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

    def test_order_growth_and_caller_estimates_from_real_dump(self):
        report = load(FIXTURE)
        edge = next(e for e in report['events'] if e.get('caller', '').startswith('bool daisy::scene::CXFileReader::parseDataObjectAnimationSet'))
        self.assertEqual(edge['growth_all_calls'], 2)
        self.assertEqual(edge['caller_insns_after'], 381)
        self.assertNotIn('caller_insns_before', edge)  # this excerpt omits its caller's graph record
        self.assertEqual([e['order'] for e in report['events']], list(range(1, len(report['events']) + 1)))
        self.assertEqual(edge['phase'], 'smaller-functions')

    def test_callee_insns_optional_with_and_called_once_punctuation(self):
        report = parse('''Deciding on inlining.  Starting with 100 insns.
Deciding on functions called once:
Considering helper() 15 insns.
 Called once from caller() 100 insns.
 Inlined into caller() which now has 103 insns for a net change of +3 insns.
Inlined 1 calls, eliminated 1 functions, 100 insns turned to 103 insns.
''')
        [edge] = report['events']
        self.assertEqual(edge['callee'], 'helper()')
        self.assertEqual(edge['callee_insns'], 15)
        self.assertEqual(edge['caller'], 'caller()')
        self.assertEqual(edge['edge_growth'], 3)

    def test_literal_sites_do_not_infer_repeated_or_unusual_counts(self):
        from concord.inline_budget import literal_sites
        from tests.test_calls_constants import refs

        sections = {'.rodata.str1.1': b'One\0Two\0Repeat\0Odd\0'}
        def side(offsets):
            return refs(datarefs=[(i * 5, '.rodata.str1.1', dest, 'mov', 'mov edi, literal') for i, dest in enumerate(offsets)])
        source = b'CString<char>("One"); CString<char>("Two"); CString<char>("Repeat"); CString<char>("Repeat"); CString<char>("Odd");'
        rows = literal_sites(source, side([0, 4, 4, 4, 8, 8, 15, 15]), side([0, 0, 0, 4, 8, 8, 15]), sections, sections)
        self.assertEqual(rows[0]['ours']['decision'], 'out-of-line')
        self.assertEqual(rows[0]['target']['decision'], 'inlined')
        self.assertEqual(rows[1]['ours']['decision'], 'inlined')
        self.assertEqual(rows[1]['target']['decision'], 'out-of-line')
        self.assertEqual(rows[2]['ours']['decision'], 'unresolved')
        self.assertEqual(rows[-1]['ours']['decision'], 'unresolved')

    def test_budgets_use_integer_cap_and_preserve_crossing_edge(self):
        from concord.inline_budget import budgets

        report = parse('''Deciding on inlining.  Starting with 10000 insns.
Deciding on smaller functions:
Considering helper() with 3200 insns
 to be inlined into caller()
 Inlined into caller() which now has 13200 insns,net change of +3200 insns.
Deciding on functions called once:
Inlined 1 calls, eliminated 0 functions, 10000 insns turned to 13200 insns.
caller()/1(1): availability:available 10000 insns (13200 after inlining)
helper()/2(2): availability:available 3200 insns
''')
        params = {'large-unit-insns': 10000, 'inline-unit-growth': 30,
                  'large-function-insns': 2700, 'large-function-growth': 100}
        result = budgets(report, params)
        self.assertEqual(result['unit_limit'], 13000)
        self.assertTrue(result['complete_net_change_trace'])
        self.assertEqual(result['events'][0]['unit_insns_after'], 13200)
        self.assertEqual(result['events'][0]['function_growth_limit'], 20000)
        report['final_insns'] += 1
        incomplete = budgets(report, params)
        self.assertFalse(incomplete['complete_net_change_trace'])
        self.assertIsNone(incomplete['unit_limit'])

    def test_parameters_preserve_command_line_overrides(self):
        import tempfile

        from concord.inline_budget import parameters

        defaults = {'large-unit-insns': 10000, 'inline-unit-growth': 30, 'large-function-insns': 2700,
                    'large-function-growth': 100, 'inline-call-cost': 12}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'params.def'
            path.write_text('\n'.join(f'DEFPARAM(ID, "{key}", "description", {value}, 0, 0)' for key, value in defaults.items()))
            result = parameters(path, ['--param', 'inline-unit-growth=31', '--param=large-unit-insns=9000'])
        self.assertEqual(result, {**defaults, 'inline-unit-growth': 31, 'large-unit-insns': 9000})

    def test_constructor_aliases_keep_separate_graph_estimates(self):
        report = parse('''Deciding on inlining.  Starting with 100 insns.
Inlined 0 calls, eliminated 0 functions, 100 insns turned to 100 insns.
Tower::Tower(float, float)/10(5): availability:available 40 insns (50 after inlining)
Tower::Tower(float, float)/11(6): availability:available 60 insns (70 after inlining)
''')
        self.assertEqual([r['initial_insns'] for r in report['functions']], [60, 40])
        self.assertEqual({r['node_id'] for r in report['functions']}, {10, 11})

    def test_named_construction_keeps_statement_lifetime_and_other_body(self):
        from concord.inline_budget import construction_forms

        source = b'''void load() {
    sprites[0] = package->add(CString<char>("One"));
    sprites[1] = package->add(CString<char>("Two"));
}
void other() { sink(CString<char>("One")); }
'''
        forms = construction_forms(source, 'load()', {'One'})
        self.assertEqual(len(forms), 1)
        candidate = next(iter(forms.values()))
        self.assertIn(b'{\n        CString<char> spriteName("One");\n        sprites[0] = package->add(spriteName);\n    }', candidate)
        self.assertIn(b'void other() { sink(CString<char>("One")); }', candidate)
        self.assertIn(b'sprites[1] = package->add(CString<char>("Two"));', candidate)

    def test_wide_and_named_literals_keep_their_encoding(self):
        from concord.inline_budget import literal_sites
        from tests.test_calls_constants import refs

        source = b'CString<wchar_t> name(L"A"); sink(CString<char>("A"));'
        sections = {'.rodata.str4.4': 'A\0'.encode('utf-32le'), '.rodata.str1.1': b'A\0'}
        body = refs(datarefs=[(0, '.rodata.str4.4', 0, 'mov', ''), (5, '.rodata.str1.1', 0, 'mov', '')])
        rows = literal_sites(source, body, body, sections, sections)
        self.assertEqual([r['encoding'] for r in rows], ['utf32le', 'utf8'])
        self.assertTrue(all(r['ours']['decision'] == 'out-of-line' for r in rows))

    def test_many_sites_can_be_serialized_with_absolute_source_lines(self):
        # Repeated Point.row access on a tree-sitter node can corrupt memory
        # on the primary interpreter. Exercise the real large-function path
        # in a child so a regression is a test failure instead of a lost suite.
        import subprocess
        import sys
        import textwrap

        result = subprocess.run([sys.executable, '-c', textwrap.dedent('''
            import json
            import tempfile
            from pathlib import Path
            from unittest.mock import patch
            from concord.harvest import Harvest
            from concord.inline_budget import sites
            from concord.calls_constants import FunctionRefs
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                reports = root / 'build/match/1.18-linux-amd64'
                target = root / 'build/objdiff/1.18-linux-amd64/target'
                for path in (root / 'src', reports, target): path.mkdir(parents=True)
                source = b'// heading\\nvoid load() {\\n' + b'\\n'.join(
                    b'sink(CString<char>("Sprite%04d"));' % n for n in range(200)) + b'\\n}'
                (root / 'src/u.cpp').write_bytes(source)
                (reports / 'u.json').write_text(json.dumps({'unit': 'u.cpp'}))
                for path in (reports / 'u.o', target / 'u.o'): path.write_bytes(b'object')
                empty = FunctionRefs(0, b'', (), (), (), 0)
                with patch('concord.calls_constants.read_refs', return_value={'load()': empty}), patch(
                        'concord.calls_constants.read_string_sections', return_value={}):
                    rows = sites(Harvest(root), 'u.cpp', ['load()'])
                json.dumps(rows)
                assert len(rows[0]['sites']) == 200
                assert rows[0]['sites'][0]['source_line'] == 3
                assert rows[0]['sites'][-1]['source_line'] == 202
        ''')], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
