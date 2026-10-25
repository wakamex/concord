"""Three-way table merges preserve independent edits and refuse ambiguity."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from concord.cli import main
from concord.symbol_table import Row, load, merge, render


class SymbolTables(unittest.TestCase):
    def test_independent_changes_additions_and_deletions_are_sorted(self):
        a, b = Row(0x20, 4, 'a', 'manual:a'), Row(0x30, 6, 'b', 'learned:b')
        moved = Row(0x10, 4, 'a', 'manual:moved')
        base = {'a': a, 'b': b}
        self.assertEqual(merge(base, {'a': moved, 'b': b}, {'a': a}), [moved])
        c = Row(0x40, 7, 'c', 'manual:c')
        self.assertEqual(merge(base, {**base, 'c': c}, {**base, 'c': c}), [a, b, c])

    def test_conflicting_edits_delete_modify_and_address_claims_refuse(self):
        a, b = Row(0x10, 4, 'a', 'manual:a'), Row(0x20, 4, 'a', 'manual:b')
        with self.assertRaisesRegex(ValueError, 'both sides changed'):
            merge({'a': a}, {}, {'a': b})
        with self.assertRaisesRegex(ValueError, 'claimed by'):
            merge({}, {'a': a}, {'b': Row(0x10, 5, 'b', 'manual:b')})

    def test_real_tsv_round_trip_and_failed_command_preserves_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = Row(0x10, 4, 'a', 'manual:quoted\tfield')
            for side in ['base', 'ours', 'theirs']:
                (root / side).write_text(render([a]))
            self.assertEqual(load(root / 'base'), {'a': a})
            (root / 'ours').write_text(render([]))
            (root / 'theirs').write_text(render([Row(0x20, 4, 'a', 'manual:b')]))
            output = root / 'out'
            output.write_text('keep me')
            with contextlib.redirect_stdout(io.StringIO()):
                status = main(['symbols-merge', '--base', str(root/'base'), '--ours', str(root/'ours'), '--theirs', str(root/'theirs'), '--output', str(output)])
            self.assertEqual(status, 1)
            self.assertEqual(output.read_text(), 'keep me')
            (root / 'base').write_text(render([a, a]))
            with self.assertRaisesRegex(ValueError, 'duplicate symbol'):
                load(root / 'base')
