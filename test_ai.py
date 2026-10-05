"""Network-free tests for AI provider setup and the bundled skills link."""

import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

import ai
import dev


ROOT = Path(__file__).resolve().parent


class TestAI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.test-ai-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.dev = SimpleNamespace(
            load_config=Mock(return_value={'ai': {'provider': 'none'}}),
            emit_ok=Mock(), emit_info=Mock(), emit_warn=Mock(), emit_error=Mock())
        self.enterContext(patch.object(ai.Path, 'home', return_value=self.home))
        self.enterContext(patch.dict(os.environ, {
            'LOCALAPPDATA': str(self.home / 'local'),
            'ProgramFiles': str(self.home / 'programs'),
        }))
        self.run = self.enterContext(patch.object(
            ai.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'Copilot 1.0', '')))
        self.which = self.enterContext(patch.object(ai.shutil, 'which', return_value=None))
        self.download = self.enterContext(patch.object(ai.urllib.request, 'urlopen'))
        self.download.return_value.__enter__.return_value.read.return_value = b'echo native installer'
        self.link = self.home / '.agents' / 'skills'
        self.personal = self.home / '.copilot' / 'skills'
        self.records = self.home / '.dev_temp' / 'dev_cli' / 'copilot-skills'
        self.config = {'ai': {'provider': 'ghcopilot'}}

    def setup_existing(self):
        self.which.side_effect = lambda name: 'copilot' if name == 'copilot' else None
        return self.setup_ai(self.config)

    def setup_ai(self, config):
        return ai.setup_ai(config, self.dev)

    def linked(self):
        return os.path.lexists(self.link)

    def bundles(self, *names):
        bundled = self.home / 'bundled'
        for name in names:
            directory = bundled / name
            directory.mkdir(parents=True)
            (directory / 'SKILL.md').write_text(f'# {name}\n', encoding='utf-8')
        self.enterContext(patch.object(ai, 'BUNDLED_SKILLS', bundled))
        return bundled

    def test_opt_out_does_not_touch_ai_files_or_download(self):
        for config in ({'ai': {'provider': 'none'}}, {'ai': {'provider': 'none', 'skills': True}}):
            with self.subTest(config=config), patch.object(ai.Path, 'home') as home:
                self.assertEqual(self.setup_ai(config), 0)
                home.assert_not_called()
        self.run.assert_not_called()
        self.which.assert_not_called()
        self.download.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_missing_provider_defaults_to_copilot(self):
        for config, skills in (({}, True), ({'ai': {}}, True), ({'ai': {'skills': False}}, False),
                               ({'ai': {'skills': ['dev-cli']}}, True), ({'ai': {'skills': []}}, False)):
            with self.subTest(config=config), patch.dict(
                    ai.PROVIDERS, {'ghcopilot': Mock()}) as providers:
                self.assertEqual(self.setup_ai(config), 0)
                providers['ghcopilot'].assert_called_once_with(skills, self.dev)

    def test_claude_reaches_unimplemented_handler_without_copilot_side_effects(self):
        with patch.object(ai, 'BUNDLED_SKILLS') as bundled, patch.object(ai.Path, 'home') as home:
            self.assertEqual(self.setup_ai({'ai': {'provider': 'claude'}}), 1)
            bundled.__truediv__.assert_not_called()
            home.assert_not_called()
        self.assertIn('Claude setup is not implemented', self.dev.emit_error.call_args.args[0])
        self.run.assert_not_called()
        self.which.assert_not_called()
        self.download.assert_not_called()

    def test_disabled_skills_verify_copilot_without_touching_skill_files(self):
        self.config['ai']['skills'] = False
        with patch.object(ai, 'BUNDLED_SKILLS') as bundled, patch.object(ai.Path, 'home') as home:
            self.assertEqual(self.setup_existing(), 0)
            bundled.resolve.assert_not_called()
            home.assert_not_called()
        self.run.assert_called_once()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_disabled_skills_still_install_selected_provider(self):
        self.config['ai']['skills'] = False
        with patch.object(ai, '_install_copilot', return_value='installed-copilot') as install:
            self.assertEqual(self.setup_ai(self.config), 0)
            install.assert_called_once_with()
        self.assertFalse(self.linked())

    def test_invalid_skills_values_fail_before_commands_or_file_writes(self):
        for provider in ('none', 'ghcopilot'):
            for value in (None, 'yes', 1, {}, 'dev-cli'):
                with self.subTest(provider=provider, value=value):
                    self.assertEqual(self.setup_ai({'ai': {'provider': provider, 'skills': value}}), 1)
                    self.assertIn('ai.skills must be', self.dev.emit_error.call_args.args[0])
        self.run.assert_not_called()
        self.which.assert_not_called()
        self.download.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_choices_are_explicit_errors(self):
        choices = [
            [], {'ai': None}, {'ai': 'ghcopilot'}, {'ai': []},
            {'ai': {'provider': 'other'}}, {'ai': {'provider': None}},
            {'ai': {'provider': []}}, {'ai': {'provider': 'none', 'unexpected': True}},
        ]
        for config in choices:
            with self.subTest(config=config):
                self.assertEqual(self.setup_ai(config), 1)
        self.assertEqual(self.dev.emit_error.call_count, len(choices))
        self.run.assert_not_called()
        self.download.assert_not_called()

    def test_existing_command_is_verified_without_installing(self):
        self.assertEqual(self.setup_existing(), 0)
        self.run.assert_called_once_with(
            ['copilot', '--version'], capture_output=True, text=True, timeout=30)
        self.download.assert_not_called()
        self.assertTrue(ai._same_path(self.link, ai.BUNDLED_SKILLS))
        self.assertIn('Authentication was not changed', self.dev.emit_info.call_args.args[0])

    def test_non_runnable_existing_command_is_an_error(self):
        self.run.return_value.returncode = 7
        self.assertEqual(self.setup_existing(), 1)
        self.assertFalse(self.linked())
        self.download.assert_not_called()

    def test_windows_uses_winget_and_verifies_installed_command(self):
        self.which.side_effect = lambda name: 'winget.exe' if name == 'winget' else None
        with patch.object(ai.platform, 'system', return_value='Windows'), \
                patch.object(ai, '_copilot_command', side_effect=[None, 'installed-copilot.exe']):
            self.assertEqual(self.setup_ai(self.config), 0)
        install, verify = self.run.call_args_list
        self.assertEqual(install.args[0][:4], ['winget.exe', 'install', '--id', 'GitHub.Copilot'])
        self.assertIn('--disable-interactivity', install.args[0])
        self.assertEqual(verify.args[0], ['installed-copilot.exe', '--version'])
        self.download.assert_not_called()

    def test_windows_missing_winget_is_actionable(self):
        with patch.object(ai.platform, 'system', return_value='Windows'):
            self.assertEqual(self.setup_ai(self.config), 1)
        self.assertIn('WinGet is required', self.dev.emit_error.call_args.args[0])
        self.run.assert_not_called()

    def test_unix_uses_official_native_installer_without_node(self):
        self.which.side_effect = lambda name: name if name in ('bash', 'curl') else None
        for system in ('Linux', 'Darwin'):
            with self.subTest(system=system), \
                    patch.object(ai.platform, 'system', return_value=system), \
                    patch.object(ai, '_copilot_command', side_effect=[None, 'installed-copilot']):
                self.assertEqual(self.setup_ai(self.config), 0)
                install, verify = self.run.call_args_list[-2:]
                self.assertEqual(install.args[0], ['bash'])
                self.assertEqual(install.kwargs['input'], b'echo native installer')
                self.assertEqual(install.kwargs['env']['PREFIX'], str(self.home / '.local'))
                self.assertTrue(install.kwargs['env']['PATH'].startswith(str(self.home / '.local' / 'bin')))
                self.assertEqual(verify.args[0], ['installed-copilot', '--version'])
        self.download.assert_called_with(ai.COPILOT_INSTALL_URL, timeout=60)

    def test_unix_missing_prerequisite_fails_without_download(self):
        with patch.object(ai.platform, 'system', return_value='Linux'):
            self.assertEqual(self.setup_ai(self.config), 1)
        self.run.assert_not_called()
        self.download.assert_not_called()

    def test_installer_failure_and_missing_executable_are_not_success(self):
        self.which.side_effect = lambda name: 'winget' if name == 'winget' else None
        for failure in (subprocess.CalledProcessError(8, 'winget'), None):
            with self.subTest(failure=failure), \
                    patch.object(ai.platform, 'system', return_value='Windows'), \
                    patch.object(ai, '_copilot_command', return_value=None):
                self.run.side_effect = failure
                self.assertEqual(self.setup_ai(self.config), 1)
                self.assertFalse(self.linked())

    def test_installer_success_with_broken_executable_fails(self):
        self.which.side_effect = lambda name: 'winget' if name == 'winget' else None
        self.run.side_effect = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 9)]
        with patch.object(ai.platform, 'system', return_value='Windows'), \
                patch.object(ai, '_copilot_command', side_effect=[None, 'broken-copilot']):
            self.assertEqual(self.setup_ai(self.config), 1)
        self.assertFalse(self.linked())

    def test_download_failure_is_reported(self):
        self.which.side_effect = lambda name: name if name in ('bash', 'wget') else None
        self.download.side_effect = urllib.error.URLError('offline fixture')
        with patch.object(ai.platform, 'system', return_value='Darwin'):
            self.assertEqual(self.setup_ai(self.config), 1)
        self.run.assert_not_called()
        self.assertIn('offline fixture', self.dev.emit_error.call_args.args[0])

    def test_incomplete_installer_download_does_not_execute(self):
        self.which.side_effect = lambda name: name if name in ('bash', 'curl') else None
        self.download.return_value.__enter__.return_value.read.side_effect = http.client.IncompleteRead(b'partial')
        with patch.object(ai.platform, 'system', return_value='Linux'):
            self.assertEqual(self.setup_ai(self.config), 1)
        self.run.assert_not_called()

    def test_timeout_and_launch_error_are_reported(self):
        for error in (subprocess.TimeoutExpired('copilot', 30), OSError('not executable')):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.assertEqual(self.setup_existing(), 1)
        self.assertFalse(self.linked())

    def test_missing_path_entry_is_discovered(self):
        for system in ('Windows', 'Linux'):
            with self.subTest(system=system), \
                    patch.object(ai.platform, 'system', return_value=system), \
                    patch.dict(os.environ, {'LOCALAPPDATA': str(self.home / 'local')}):
                target = self.home / 'local' / 'Microsoft' / 'WinGet' / 'Links' / 'copilot.exe' \
                    if system == 'Windows' else self.home / '.local' / 'bin' / 'copilot'
                target.parent.mkdir(parents=True)
                target.write_bytes(b'fixture')
                self.assertEqual(ai._copilot_command(), str(target))

    def test_windows_winget_package_is_discovered_before_path_refresh(self):
        target = self.home / 'local' / 'Microsoft' / 'WinGet' / 'Packages' / \
            'GitHub.Copilot_Microsoft.Winget.Source_fixture' / 'copilot.exe'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'fixture')
        with patch.object(ai.platform, 'system', return_value='Windows'):
            self.assertEqual(ai._copilot_command(), str(target))

    def test_links_bundled_skills_idempotently(self):
        bundled = self.bundles('dev-cli', 'release-notes')
        self.assertEqual(self.setup_existing(), 0)
        self.assertTrue(ai._is_link(self.link))
        self.assertTrue(ai._same_path(self.link, bundled))
        self.assertEqual((self.link / 'release-notes' / 'SKILL.md').read_text(), '# release-notes\n')
        (bundled / 'added').mkdir()
        (bundled / 'added' / 'SKILL.md').write_text('# added\n', encoding='utf-8')
        self.assertEqual(self.setup_existing(), 0)
        self.assertTrue((self.link / 'added' / 'SKILL.md').is_file())
        self.assertIn('Bundled skills are linked', self.dev.emit_ok.call_args_list[-1].args[0])
        self.dev.emit_warn.assert_not_called()
        self.assertFalse(self.personal.exists())

    def test_existing_directory_is_preserved(self):
        self.bundles('dev-cli')
        (self.link / 'mine').mkdir(parents=True)
        (self.link / 'mine' / 'SKILL.md').write_text('mine', encoding='utf-8')
        self.assertEqual(self.setup_existing(), 0)
        self.assertFalse(ai._is_link(self.link))
        self.assertEqual((self.link / 'mine' / 'SKILL.md').read_text(), 'mine')
        self.assertIn('Preserving existing', self.dev.emit_warn.call_args.args[0])

    def test_link_to_another_directory_is_preserved(self):
        self.bundles('dev-cli')
        other = self.home / 'other skills'
        other.mkdir()
        self.link.parent.mkdir()
        ai._create_directory_link(self.link, other)
        self.assertEqual(self.setup_existing(), 0)
        self.assertTrue(ai._same_path(self.link, other))
        self.assertIn('links elsewhere', self.dev.emit_warn.call_args.args[0])

    def test_unedited_copies_are_retired_and_edited_copies_kept(self):
        bundled = self.bundles('dev-cli', 'edited')
        self.records.mkdir(parents=True)
        for name in ('dev-cli', 'edited', 'gone'):
            if name != 'gone':
                (self.personal / name).mkdir(parents=True)
                (self.personal / name / 'SKILL.md').write_bytes((bundled / name / 'SKILL.md').read_bytes())
            digest = ai._skill_digest(self.personal / name) if name != 'gone' else 'missing'
            (self.records / f'{name}.json').write_text(
                json.dumps({'owner': 'dev_cli', 'sha256': digest}), encoding='utf-8')
        (self.personal / 'edited' / 'SKILL.md').write_text('user edits', encoding='utf-8')
        self.assertEqual(self.setup_existing(), 0)
        self.assertFalse((self.personal / 'dev-cli').exists())
        self.assertEqual((self.personal / 'edited' / 'SKILL.md').read_text(), 'user edits')
        self.assertEqual(sorted(path.name for path in self.records.iterdir()), ['edited.json'])
        self.assertEqual(self.dev.emit_warn.call_count, 1)
        self.assertIn('takes precedence over the bundled edited skill', self.dev.emit_warn.call_args.args[0])

    def test_personal_skill_with_bundled_name_is_untouched(self):
        self.bundles('dev-cli')
        (self.personal / 'dev-cli').mkdir(parents=True)
        (self.personal / 'dev-cli' / 'SKILL.md').write_text('personal', encoding='utf-8')
        (self.personal / 'private').mkdir()
        self.assertEqual(self.setup_existing(), 0)
        self.assertEqual((self.personal / 'dev-cli' / 'SKILL.md').read_text(), 'personal')
        self.assertTrue((self.personal / 'private').is_dir())
        self.assertIn('takes precedence', self.dev.emit_warn.call_args.args[0])

    def test_link_failure_returns_error(self):
        with patch.object(ai, '_create_directory_link', side_effect=OSError('read-only fixture')):
            self.assertEqual(self.setup_existing(), 1)
        self.assertIn('read-only fixture', self.dev.emit_error.call_args.args[0])

    def test_auth_settings_are_not_modified(self):
        settings = self.home / '.copilot' / 'config.json'
        settings.parent.mkdir()
        settings.write_text('{"user_setting": "keep"}', encoding='utf-8')
        self.assertEqual(self.setup_existing(), 0)
        self.assertEqual(settings.read_text(), '{"user_setting": "keep"}')

    def test_chat_defaults_to_copilot_with_yolo_from_workspace_root(self):
        arguments = ['--resume', '-p', 'space and "quotes"', '', '--', '*']
        workspace = self.home / 'workspace root'
        workspace.mkdir()
        self.which.return_value = 'native-copilot'
        self.run.return_value.returncode = 23
        for config in ({}, self.config):
            with self.subTest(config=config):
                self.run.reset_mock()
                self.assertEqual(ai.run_chat(config, arguments, workspace, self.dev), 23)
                self.run.assert_called_once_with(
                    ['native-copilot', '--allow-all', '--mode', 'autopilot', '--add-dir', str(workspace), *arguments],
                    cwd=workspace, check=False)
        self.download.assert_not_called()

    def test_chat_keeps_an_explicit_mode(self):
        self.which.return_value = 'native-copilot'
        self.run.return_value.returncode = 0
        for arguments in (['--mode', 'plan'], ['--mode=interactive'], ['--autopilot'], ['--plan']):
            with self.subTest(arguments=arguments):
                self.run.reset_mock()
                self.assertEqual(ai.run_chat({}, arguments, self.home, self.dev), 0)
                self.run.assert_called_once_with(
                    ['native-copilot', '--allow-all', '--add-dir', str(self.home), *arguments],
                    cwd=self.home, check=False)
        self.dev.emit_ok.assert_not_called()
        self.assertFalse(self.linked())

    def test_chat_bootstraps_missing_copilot_and_skills_then_launches(self):
        with patch.object(ai, '_install_copilot', return_value='installed-copilot') as install:
            self.assertEqual(ai.run_chat(self.config, [], self.home, self.dev), 0)
        install.assert_called_once_with()
        self.assertTrue((self.link / 'dev-cli' / 'SKILL.md').is_file())
        self.run.assert_called_once_with(
            ['installed-copilot', '--allow-all', '--mode', 'autopilot', '--add-dir', str(self.home)],
            cwd=self.home, check=False)

    def test_chat_does_not_launch_after_failed_bootstrap(self):
        for error in (OSError('offline'), RuntimeError('installer failed'),
                      subprocess.CalledProcessError(7, 'installer')):
            with self.subTest(error=error), patch.object(ai, '_install_copilot', side_effect=error):
                self.assertEqual(ai.run_chat(self.config, [], self.home, self.dev), 1)
        self.run.assert_not_called()
        self.assertEqual(self.dev.emit_error.call_count, 3)

    def test_chat_does_not_launch_after_failed_skill_setup(self):
        with patch.object(ai, '_install_copilot', return_value='installed-copilot'), \
                patch.object(ai, '_link_skills', side_effect=OSError('skill write failed')):
            self.assertEqual(ai.run_chat(self.config, [], self.home, self.dev), 1)
        self.run.assert_not_called()
        self.assertIn('skill write failed', self.dev.emit_error.call_args.args[0])

    def test_chat_disabled_invalid_and_unimplemented_providers_do_not_fall_back(self):
        for config, message in (
                ({'ai': {'provider': 'none'}}, 'AI is disabled'),
                ({'ai': {'provider': 'claude'}}, 'Claude chat is not implemented'),
                ({'ai': {'provider': 'invalid'}}, 'ai.provider must be'),
                ({'ai': None}, 'ai must be')):
            with self.subTest(config=config):
                self.assertEqual(ai.run_chat(config, [], self.home, self.dev), 1)
                self.assertIn(message, self.dev.emit_error.call_args.args[0])
        self.which.assert_not_called()
        self.run.assert_not_called()
        self.download.assert_not_called()

    def test_chat_launch_error_and_interrupt_propagate(self):
        self.which.return_value = 'native-copilot'
        for error, expected in ((OSError('not executable'), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.assertEqual(ai.run_chat(self.config, [], self.home, self.dev), expected)
        self.assertIn('not executable', self.dev.emit_error.call_args.args[0])

    def test_chat_rejects_missing_workspace_before_bootstrap(self):
        missing = self.home / 'missing workspace'
        self.assertEqual(ai.run_chat(self.config, [], missing, self.dev), 1)
        self.assertIn('Workspace root does not exist', self.dev.emit_error.call_args.args[0])
        self.which.assert_not_called()
        self.run.assert_not_called()
        self.download.assert_not_called()


class TestChatCommand(unittest.TestCase):
    def test_dispatch_preserves_provider_arguments_without_claude_trust_changes(self):
        for arguments in ([], ['--', '--help'], ['--', '-p', 'quoted "prompt"', '', '--', '--version']):
            with self.subTest(arguments=arguments), \
                    patch('sys.argv', ['dev', 'ai', *arguments]), \
                    patch.object(dev, 'load_config', return_value={'ai': {'provider': 'ghcopilot'}}), \
                    patch.object(dev, 'get_base_path', return_value='configured workspace'), \
                    patch.object(dev, 'trust_claude_workspace') as trust, \
                    patch.object(ai, 'run_chat', return_value=27) as chat:
                self.assertEqual(dev.main(), 27)
                chat.assert_called_once_with(
                    {'ai': {'provider': 'ghcopilot'}}, arguments[1:] if arguments else [],
                    'configured workspace', dev)
                trust.assert_not_called()

    def test_configuration_error_is_reported_without_launching(self):
        with patch('sys.argv', ['dev', 'ai']), \
                patch.object(dev, 'load_config', side_effect=ValueError('invalid configuration')), \
                patch.object(dev, 'emit_error') as error, patch.object(ai, 'run_chat') as chat:
            self.assertEqual(dev.main(), 1)
        error.assert_called_once_with('AI configuration failed: invalid configuration')
        chat.assert_not_called()


if __name__ == '__main__':
    unittest.main()
