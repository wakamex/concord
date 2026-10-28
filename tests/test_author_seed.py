"""Authored units without an ancestor are measured before installation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from concord import authored
from concord.harvest import Evaluation


class AuthorSeed(unittest.TestCase):
    def test_missing_unit_is_measured_as_overlay_and_registered_only_on_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config/1.18-linux-amd64'
            config.mkdir(parents=True)
            (config / 'units.toml').write_text('')
            harvest = Mock(root=root)
            harvest.dependents.return_value = []
            harvest.evaluate_overlays.return_value = {
                'new-0': Evaluation('new-0', 'a', sections=[{'functions': [{'symbol': '_Z1fv', 'exact': True}]}])}
            files = {'d/A.cpp': b'int f() { return 1; }'}
            units = {'d/A.cpp': {'.text': 0x1000}}
            report = authored.seed(harvest, files, units, write=False)
            self.assertTrue(report['accepted'])
            self.assertFalse((root / 'src/d/A.cpp').exists())
            self.assertEqual((config / 'units.toml').read_text(), '')
            self.assertEqual(harvest.evaluate_overlays.call_args.kwargs['units'], units)
            with patch('concord.seed._match', return_value=''):
                report = authored.seed(harvest, files, units, write=True)
            self.assertTrue(report['applied'])
            self.assertEqual((root / 'src/d/A.cpp').read_bytes(), files['d/A.cpp'])
            self.assertIn('d/A.cpp', (config / 'units.toml').read_text())

    def test_compile_failure_or_regression_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            header = root / 'src/H.h'
            header.parent.mkdir()
            header.write_bytes(b'old')
            harvest = Mock(root=root)
            harvest.dependents.return_value = ['old.cpp']
            before = Evaluation('before', 'old', fuzzy={'f': 100})
            after = Evaluation('after', 'old', fuzzy={'f': 99})
            harvest.evaluate_overlays.return_value = {
                'new-0': Evaluation('new', 'a', sections=[{'functions': [{'symbol': 'new', 'exact': True}]}]),
                'before-0': before, 'after-0': after}
            report = authored.seed(harvest, {'d/A.cpp': b'int f();', 'H.h': b'new'}, {'d/A.cpp': {}}, write=True)
            self.assertFalse(report['accepted'])
            self.assertEqual(header.read_bytes(), b'old')
            self.assertFalse((root / 'src/d/A.cpp').exists())

    def test_existing_unit_and_escaping_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src').mkdir()
            (root / 'src/A.cpp').write_bytes(b'old')
            harvest = Mock(root=root)
            for files, units in [({'A.cpp': b'new'}, {'A.cpp': {}}), ({'../x.cpp': b'new'}, {'../x.cpp': {}})]:
                with self.assertRaises(ValueError):
                    authored.seed(harvest, files, units, write=True)
            harvest.evaluate_overlays.assert_not_called()
