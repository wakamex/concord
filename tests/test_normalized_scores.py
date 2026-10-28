"""The normalized gate changes only its private worktree, even on failure."""

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
            (root/'src/a.cpp').write_text('dirty')
            with self.assertRaisesRegex(ValueError, 'committed measurement inputs'):
                normalized(root, base)
