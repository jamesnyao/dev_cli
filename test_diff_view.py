"""Tests for the isolated, manifest-driven diff launcher."""

import argparse
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import dev
import diff_view


class TestDiffView(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.before = self.root / 'before' / 'SKILL.md'
        self.after = self.root / 'working files' / 'SKILL.md'
        self.before.parent.mkdir()
        self.after.parent.mkdir()
        self.before.write_bytes(b'baseline\r\n')
        self.after.write_bytes(b'user changes\r\n')
        self.manifest = self.root / 'review.json'
        self.review = {
            'title': 'project main',
            'diffs': [['before/SKILL.md', str(self.after)]],
        }
        self._save()
        self.enterContext(patch('diff_view.shutil.which', return_value='code'))
        self.launch = self.enterContext(patch('diff_view.subprocess.run'))

    def _save(self):
        self.manifest.write_text(json.dumps(self.review), encoding='utf-8')

    def test_opens_only_ordered_diffs_in_one_folderless_workspace(self):
        other_before = self.root / 'before' / 'second.txt'
        other_after = self.root / 'working files' / 'second.txt'
        other_before.write_bytes(b'old\n')
        other_after.write_bytes(b'new\n')
        self.review['diffs'].append([str(other_before), str(other_after)])
        self._save()

        workspace = diff_view.open_review(self.manifest)

        commands = [call.args[0] for call in self.launch.call_args_list]
        self.assertEqual(commands, [
            ['code', '--new-window', str(workspace), '--diff', str(self.before), str(self.after)],
            ['code', str(workspace), '--diff', str(other_before), str(other_after)],
        ])
        settings = json.loads(workspace.read_text(encoding='utf-8'))
        self.assertEqual(settings['folders'], [])
        self.assertEqual(settings['settings']['workbench.startupEditor'], 'none')
        self.assertFalse(settings['settings']['workbench.editor.enablePreview'])
        self.assertFalse(settings['settings']['diffEditor.ignoreTrimWhitespace'])
        self.assertEqual(settings['settings']['files.readonlyInclude'], {
            self.before.as_posix(): True,
            other_before.as_posix(): True,
        })
        self.assertEqual(settings['settings']['window.title'], '${dirty}project main - Diff Review')
        self.assertEqual(self.before.read_bytes(), b'baseline\r\n')
        self.assertEqual(self.after.read_bytes(), b'user changes\r\n')

    def test_reopening_uses_fresh_editor_state_and_preserves_saved_edits(self):
        first = diff_view.open_review(self.manifest)
        self.after.write_bytes(b'more saved edits\r\n')

        second = diff_view.open_review(self.manifest)

        self.assertNotEqual(first, second)
        self.assertEqual(self.before.read_bytes(), b'baseline\r\n')
        self.assertEqual(self.after.read_bytes(), b'more saved edits\r\n')
        self.assertEqual(self.launch.call_args.args[0][-2:], [str(self.before), str(self.after)])
        self.assertEqual(
            json.loads(self.manifest.read_text(encoding='utf-8')), self.review)

    def test_allows_eight_pairs_in_supplied_order(self):
        self.review['diffs'] = []
        expected = []
        for number in range(8):
            before = self.root / 'before' / f'{number}.txt'
            after = self.root / 'working files' / f'{number}.txt'
            before.write_bytes(b'old\n')
            after.write_bytes(b'new\n')
            self.review['diffs'].append([str(before), str(after)])
            expected.append([str(before), str(after)])
        self._save()

        diff_view.open_review(self.manifest)

        self.assertEqual([call.args[0][-2:] for call in self.launch.call_args_list], expected)

    def test_invalid_reviews_never_launch_or_create_a_workspace(self):
        cases = [
            ([], ValueError),
            ({'diffs': []}, ValueError),
            ({'diffs': self.review['diffs'] * 9}, ValueError),
            ({'diffs': [[str(self.before)]]}, ValueError),
            ({'diffs': [[str(self.before), 42]]}, ValueError),
            ({'diffs': [[str(self.before), str(self.before)]]}, ValueError),
            ({'diffs': [[str(self.before), str(self.after)]], 'title': ''}, ValueError),
            ({'diffs': [[str(self.before), str(self.root / 'missing.txt')]]}, FileNotFoundError),
            ({'diffs': [[str(self.before), str(self.root)]]}, ValueError),
            ({'diffs': [
                [str(self.before), str(self.after)],
                [str(self.after), str(self.before)],
            ]}, ValueError),
        ]
        for review, error in cases:
            with self.subTest(review=review):
                self.review = review
                self._save()
                with self.assertRaises(error):
                    diff_view.open_review(self.manifest)
                self.launch.assert_not_called()
                self.assertEqual(list(self.root.glob('*.code-workspace')), [])
        self.assertEqual(self.before.read_bytes(), b'baseline\r\n')
        self.assertEqual(self.after.read_bytes(), b'user changes\r\n')

    def test_missing_vscode_fails_before_creating_a_workspace(self):
        with patch('diff_view.shutil.which', return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, 'VS Code was not found'):
                diff_view.open_review(self.manifest)
        self.launch.assert_not_called()
        self.assertEqual(list(self.root.glob('*.code-workspace')), [])

    def test_command_reports_launch_failures_without_changing_sources(self):
        self.launch.side_effect = subprocess.CalledProcessError(
            1, ['code'], stderr='editor launch rejected')
        with patch('sys.stderr', new_callable=StringIO) as errors:
            result = dev.cmd_diff(argparse.Namespace(review_file=self.manifest))
        self.assertEqual(result, 1)
        self.assertIn('editor launch rejected', errors.getvalue())
        self.assertEqual(self.before.read_bytes(), b'baseline\r\n')
        self.assertEqual(self.after.read_bytes(), b'user changes\r\n')

    def test_dev_diff_dispatches_the_manifest(self):
        with patch('sys.argv', ['dev', 'diff', '--review-file', str(self.manifest)]), \
             patch('dev.get_base_path', return_value=str(self.root)), \
             patch('dev.trust_claude_workspace'), \
             patch('sys.stdout', new_callable=StringIO) as output:
            result = dev.main()
        self.assertEqual(result, 0)
        self.assertIn('Diff review opened:', output.getvalue())
        self.assertEqual(self.launch.call_args.args[0][-2:], [str(self.before), str(self.after)])


if __name__ == '__main__':
    unittest.main()
