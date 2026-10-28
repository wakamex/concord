"""The normalized gate changes only its private worktree, even on failure."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from concord.scores import normalized


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


class NormalizedScores(unittest.TestCase):
    def repository(self, root):
        git(root, 'init', '-q')
        git(root, 'config', 'user.name', 'Test')
        git(root, 'config', 'user.email', 'test@example.invalid')
        for directory in ['src', 'third_party', 'config/test']:
            (root / directory).mkdir(parents=True)
        (root / 'src/a.cpp').write_text('base')
        (root / 'third_party/a.h').write_text('header')
        (root / 'config/test/symbols.tsv').write_text('base addresses')
        (root / 'builds.json').write_text(json.dumps({'canonical': 'test'}))
        git(root, 'add', 'src/a.cpp', 'third_party/a.h', 'config/test/symbols.tsv', 'builds.json')
        git(root, 'commit', '-qm', 'base')
        base = git(root, 'rev-parse', 'HEAD').decode().strip()
        (root / 'src/a.cpp').write_text('head')
        (root / 'src/new.cpp').write_text('new')
        (root / 'config/test/symbols.tsv').write_text('learned addresses')
        git(root, 'add', 'src/a.cpp', 'src/new.cpp', 'config/test/symbols.tsv')
        git(root, 'commit', '-qm', 'head')
        return base

    def test_real_git_worktree_captures_source_and_configuration_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self.repository(root)
            seen = []
            def capture(private, capture=False):
                self.assertTrue(capture)
                self.assertNotEqual(private, root)
                seen.append((
                    (private/'src/a.cpp').read_text(),
                    (private/'src/new.cpp').exists(),
                    (private/'config/test/symbols.tsv').read_text(),
                ))
                return {'units': [], 'concord_matched_data': []}
            with patch('concord.scores.report', side_effect=capture):
                before, after, evidence = normalized(root, base)
            self.assertEqual(seen, [
                ('base', False, 'learned addresses'),
                ('base', False, 'base addresses'),
                ('head', True, 'learned addresses'),
            ])
            self.assertEqual(before, after)
            self.assertEqual(evidence['source_paths'], ['src/a.cpp', 'src/new.cpp'])
            self.assertEqual(evidence['configuration_changes'], ['config/test/symbols.tsv'])
            self.assertIsNotNone(evidence['configuration_baseline'])
            self.assertEqual(git(root, 'status', '--porcelain'), b'')
            self.assertEqual(len(git(root, 'worktree', 'list', '--porcelain').decode().split('worktree ')), 2)

    def test_capture_failure_cleans_worktree_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self.repository(root)
            with patch('concord.scores.report', side_effect=RuntimeError('compile failed')), self.assertRaisesRegex(RuntimeError, 'compile failed'):
                normalized(root, base)
            self.assertEqual((root/'src/a.cpp').read_text(), 'head')
            self.assertEqual(git(root, 'status', '--porcelain'), b'')
            self.assertNotIn('concord-gate', git(root, 'worktree', 'list').decode())
            (root/'config/test/symbols.tsv').write_text('dirty')
            with self.assertRaisesRegex(ValueError, 'committed measurement inputs'):
                normalized(root, base)

    def test_tracked_source_overlay_is_measured_without_changing_checkout_or_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self.repository(root)
            (root / 'src/a.cpp').write_text('staged')
            git(root, 'add', 'src/a.cpp')
            (root / 'src/a.cpp').write_text('working')
            (root / 'src/new.cpp').unlink()
            original_status = git(root, 'status', '--porcelain')
            original_index = git(root, 'diff', '--cached', '--binary')
            seen = []

            def capture(private, capture=False):
                self.assertTrue(capture)
                seen.append(((private / 'src/a.cpp').read_text(), (private / 'src/new.cpp').exists()))
                return {'units': [], 'concord_matched_data': []}

            with patch('concord.scores.report', side_effect=capture):
                _, _, evidence = normalized(root, base)

            self.assertEqual(seen, [('base', False), ('base', False), ('working', False)])
            self.assertEqual(evidence['source_paths'], ['src/a.cpp'])
            self.assertEqual(evidence['workspace_source'], {
                'src/a.cpp': hashlib.sha256(b'working').hexdigest(), 'src/new.cpp': None,
            })
            self.assertEqual((root / 'src/a.cpp').read_text(), 'working')
            self.assertFalse((root / 'src/new.cpp').exists())
            self.assertEqual(git(root, 'status', '--porcelain'), original_status)
            self.assertEqual(git(root, 'diff', '--cached', '--binary'), original_index)

    def test_untracked_source_is_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self.repository(root)
            (root / 'src/untracked.cpp').write_text('not in the index')
            with patch('concord.scores.report') as capture, self.assertRaisesRegex(ValueError, 'committed measurement inputs'):
                normalized(root, base)
            capture.assert_not_called()

    def test_deleted_new_unit_is_still_omitted_from_base_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self.repository(root)
            units = root / 'config/test/units.toml'
            units.write_text('["new.cpp"]\naddress = 1\n')
            git(root, 'add', 'config/test/units.toml')
            git(root, 'commit', '-qm', 'register unit')
            (root / 'src/new.cpp').unlink()
            seen = []

            def capture(private, capture=False):
                path = private / 'config/test/units.toml'
                seen.append(path.read_text() if path.exists() else None)
                return {'units': [], 'concord_matched_data': []}

            with patch('concord.scores.report', side_effect=capture):
                _, _, evidence = normalized(root, base)
            self.assertEqual(seen, ['', None, '["new.cpp"]\naddress = 1\n'])
            self.assertEqual(evidence['absent_base_units'], ['new.cpp'])

    def test_source_change_during_capture_cannot_pass_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self.repository(root)
            (root / 'src/a.cpp').write_text('candidate')

            def capture(private, capture=False):
                (root / 'src/a.cpp').write_text('changed while compiling')
                return {'units': [], 'concord_matched_data': []}

            with patch('concord.scores.report', side_effect=capture), self.assertRaisesRegex(ValueError, 'measurement inputs changed'):
                normalized(root, base)
            self.assertEqual((root / 'src/a.cpp').read_text(), 'changed while compiling')
            self.assertNotIn('concord-gate', git(root, 'worktree', 'list').decode())
