"""Tests for Windows Terminal default-shell setup."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import terminal


class TestWindowsTerminal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.test-terminal-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.local = self.root / 'local'
        self.programs = self.root / 'programs'
        self.bash = self.programs / 'Git' / 'bin' / 'bash.exe'
        self.bash.parent.mkdir(parents=True)
        self.bash.touch()
        self.settings = self.local / 'Packages' / \
            'Microsoft.WindowsTerminal_8wekyb3d8bbwe' / 'LocalState' / 'settings.json'
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({
            'defaultProfile': '{powershell}',
            'profiles': {'defaults': {}, 'list': [
                {'guid': '{powershell}', 'name': 'PowerShell', 'source': 'PowerShell.Core'},
            ]},
            'actions': [{'command': 'copy'}],
        }), encoding='utf-8')
        self.reporter = SimpleNamespace(emit_ok=Mock(), emit_error=Mock())
        self.enterContext(patch.object(terminal.platform, 'system', return_value='Windows'))
        self.enterContext(patch.dict(os.environ, {
            'LOCALAPPDATA': str(self.local),
            'ProgramFiles': str(self.programs),
        }))

    def test_system_default_preserves_settings(self):
        before = self.settings.read_bytes()
        self.assertEqual(terminal.setup_windows_terminal(
            {'defaultShell': 'system'}, self.reporter), 0)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_bash_adds_profile_and_preserves_other_settings(self):
        self.assertEqual(terminal.setup_windows_terminal(
            {'defaultShell': 'bash'}, self.reporter), 0)
        settings = json.loads(self.settings.read_text(encoding='utf-8'))
        self.assertEqual(settings['defaultProfile'], terminal.GIT_BASH_GUID)
        self.assertEqual(settings['actions'], [{'command': 'copy'}])
        profile = settings['profiles']['list'][-1]
        self.assertEqual(profile['guid'], terminal.GIT_BASH_GUID)
        self.assertEqual(profile['commandline'], f'"{self.bash}" --login -i')
        self.assertEqual(profile['startingDirectory'], '%USERPROFILE%')

    def test_existing_bash_profile_is_reused_idempotently(self):
        existing = json.loads(self.settings.read_text(encoding='utf-8'))
        existing['profiles']['list'].append({
            'guid': '{custom-bash}',
            'name': 'Customized Git Bash',
            'commandline': '"C:\\old\\Git\\bin\\bash.exe" --login',
            'colorScheme': 'Campbell',
        })
        self.settings.write_text(json.dumps(existing), encoding='utf-8')
        config = {'defaultShell': 'bash'}
        self.assertEqual(terminal.setup_windows_terminal(config, self.reporter), 0)
        first = self.settings.read_bytes()
        self.assertEqual(terminal.setup_windows_terminal(config, self.reporter), 0)
        self.assertEqual(self.settings.read_bytes(), first)
        settings = json.loads(first)
        self.assertEqual(settings['defaultProfile'], '{custom-bash}')
        self.assertEqual(settings['profiles']['list'][-1]['colorScheme'], 'Campbell')

    def test_invalid_default_is_reported_without_changes(self):
        before = self.settings.read_bytes()
        self.assertEqual(terminal.setup_windows_terminal(
            {'defaultShell': 'zsh'}, self.reporter), 1)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertIn('defaultShell', self.reporter.emit_error.call_args.args[0])

    def test_missing_git_bash_is_actionable(self):
        self.bash.unlink()
        self.assertEqual(terminal.setup_windows_terminal(
            {'defaultShell': 'bash'}, self.reporter), 1)
        self.assertIn('Git Bash was not found', self.reporter.emit_error.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
