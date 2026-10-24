"""Header ownership comes from real compiler declarations, not caller names."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.census import rank
from concord.copy_context import annotate, definitions, header_symbols


class CopyContext(unittest.TestCase):
    def test_exact_copy_redirects_header_site_to_its_caller(self):
        report = {'function': {'address': 1, 'size': 50, 'unit': 'caller.cpp', 'symbol': 'caller'},
                  'findings': [{'target_offset': 2, 'status': 'observed-candidate-decision', 'cause': 'operand-order',
                                'source': {'path': 'src/header.h', 'line': 3}}]}
        annotate(Path('/repo'), [report], {('src/header.h', 1, 5): {'f'}}, {'f': {'emitted': ['a', 'b'], 'exact': ['b']}})
        result = rank(Path('/repo'), [report])
        [row] = result['constructs']
        self.assertEqual((row['classification'], row['path'], row['text']), ('caller-context', 'src/caller.cpp', 'caller'))
        self.assertEqual(row['header_evidence'][0]['copies'][0]['exact'], ['b'])
        self.assertFalse(row['header'])
        self.assertEqual(result['header_candidates'], [])

    def test_inexact_and_absent_copies_remain_distinct_and_inconclusive(self):
        finding = {'target_offset': 1, 'status': 'observed-candidate-decision', 'cause': 'operand-order',
                   'source': {'path': 'src/header.h', 'line': 3}}
        annotate(Path('/repo'), [{'findings': [finding]}], {('src/header.h', 1, 5): {'f'}}, {'f': {'emitted': ['a'], 'exact': []}})
        self.assertEqual(finding['context']['classification'], 'header-candidate')
        annotate(Path('/repo'), [{'findings': [finding]}], {}, {})
        self.assertEqual(finding['context']['classification'], 'header-unverified')
        result = rank(Path('/repo'), [{'function': {'address': 1, 'size': 50, 'unit': 'u.cpp', 'symbol': 'f'}, 'findings': [finding]}])
        self.assertEqual(result['constructs'], [])
        self.assertEqual(len(result['unverified_headers']), 1)

    @unittest.skipUnless(shutil.which('g++'), 'g++ is needed')
    def test_real_compiler_links_header_definition_to_its_own_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            header = b'inline int f(int x) {\n return x + 1;\n}\n'
            (root / 'src/header.h').write_bytes(header)
            (root / 'u.cpp').write_text('#include "src/header.h"\nint (*p)(int) = f;\nint caller(int x) { return f(x); }\n')
            obj = root / 'u.o'
            subprocess.run(['g++', '-g', '-O2', '-c', str(root / 'u.cpp'), '-o', str(obj)], check=True, capture_output=True)
            self.assertEqual(definitions(header), [(1, 3)])
            self.assertEqual(header_symbols(root, obj, {'src/header.h'}), {('src/header.h', 1, 3): {'_Z1fi'}})
