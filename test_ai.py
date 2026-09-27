"""Network-free tests for AI provider setup and skill ownership."""

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
        self.target = self.home / '.copilot' / 'skills' / 'dev-cli' / 'SKILL.md'
        self.metadata = self.home / '.dev_temp' / 'dev_cli' / 'copilot-skills' / 'dev-cli.json'
        self.config = {'ai': {'provider': 'ghcopilot'}}

    def setup_existing(self):
        self.which.side_effect = lambda name: 'copilot' if name == 'copilot' else None
        return self.setup_ai(self.config)

    def setup_ai(self, config):
        return ai.setup_ai(config, self.dev)

    def bundles(self, *names):
        bundled = self.home / 'bundled'
        for name in names:
            directory = bundled / name
            directory.mkdir(parents=True)
            (directory / 'SKILL.md').write_text(f'# {name}\n', encoding='utf-8')
        self.enterContext(patch.object(ai, 'BUNDLED_SKILLS', bundled))
        return bundled

    def test_opt_out_does_not_touch_ai_files_or_download(self):
        for config in ({'ai': {'provider': 'none'}}, {'ai': {'provider': 'none', 'skills': []}}):
            with self.subTest(config=config), patch.object(ai.Path, 'home') as home:
                self.assertEqual(self.setup_ai(config), 0)
                home.assert_not_called()
        self.run.assert_not_called()
        self.which.assert_not_called()
        self.download.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_missing_provider_defaults_to_copilot(self):
        for config in ({}, {'ai': {}}, {'ai': {'skills': []}}):
            with self.subTest(config=config), patch.dict(
                    ai.PROVIDERS, {'ghcopilot': Mock()}) as providers:
                self.assertEqual(self.setup_ai(config), 0)
                providers['ghcopilot'].assert_called_once_with(
                    config.get('ai', {}).get('skills', ['dev-cli']), self.dev)

    def test_claude_reaches_unimplemented_handler_without_copilot_side_effects(self):
        with patch.object(ai, 'BUNDLED_SKILLS') as bundled, patch.object(ai.Path, 'home') as home:
            self.assertEqual(self.setup_ai({'ai': {'provider': 'claude'}}), 1)
            bundled.__truediv__.assert_not_called()
            home.assert_not_called()
        self.assertIn('Claude setup is not implemented', self.dev.emit_error.call_args.args[0])
        self.run.assert_not_called()
        self.which.assert_not_called()
        self.download.assert_not_called()

    def test_opt_out_does_not_inspect_selected_skill_directories(self):
        with patch.object(ai, 'BUNDLED_SKILLS') as bundled, patch.object(ai.Path, 'home') as home:
            self.assertEqual(self.setup_ai({'ai': {'provider': 'none', 'skills': ['future-skill']}}), 0)
            bundled.__truediv__.assert_not_called()
            home.assert_not_called()
        self.run.assert_not_called()
        self.download.assert_not_called()

    def test_empty_selection_verifies_copilot_without_touching_skill_files(self):
        self.config['ai']['skills'] = []
        with patch.object(ai, 'BUNDLED_SKILLS') as bundled, patch.object(ai.Path, 'home') as home:
            self.assertEqual(self.setup_existing(), 0)
            bundled.__truediv__.assert_not_called()
            home.assert_not_called()
        self.run.assert_called_once()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_empty_selection_still_installs_selected_provider(self):
        self.config['ai']['skills'] = []
        with patch.object(ai, '_install_copilot', return_value='installed-copilot') as install:
            self.assertEqual(self.setup_ai(self.config), 0)
            install.assert_called_once_with()
        self.assertFalse(self.target.parent.exists())
        self.assertFalse(self.metadata.parent.exists())

    def test_invalid_skill_lists_fail_before_commands_or_file_writes(self):
        selections = [
            None, 'dev-cli', {}, 1, True, ['dev-cli', 'dev-cli'], [None], [{}], [[]],
            [''], ['../private'], ['..\\private'], ['/private'], ['C:\\private'],
            ['dev-cli/SKILL.md'], ['dev-cli\\SKILL.md'], ['dev-cli:stream'],
            ['DEV-CLI'], [' dev-cli'], ['dev_cli'], ['x' * 65],
        ]
        for provider in ('none', 'ghcopilot'):
            for names in selections:
                with self.subTest(provider=provider, names=names):
                    self.assertEqual(self.setup_ai({'ai': {'provider': provider, 'skills': names}}), 1)
        self.run.assert_not_called()
        self.which.assert_not_called()
        self.download.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_unknown_or_missing_skill_is_rejected_before_install(self):
        bundled = self.bundles('known')
        (bundled / 'incomplete').mkdir()
        for name in ('not-bundled', 'incomplete', 'con'):
            with self.subTest(name=name):
                self.config['ai']['skills'] = ['known', name]
                self.assertEqual(self.setup_ai(self.config), 1)
                self.assertIn('Unsupported bundled skill', self.dev.emit_error.call_args.args[0])
        self.which.assert_not_called()
        self.run.assert_not_called()
        self.download.assert_not_called()
        self.assertFalse((self.home / '.copilot').exists())
        self.assertFalse((self.home / '.dev_temp').exists())

    def test_linked_bundled_skill_is_rejected_before_install(self):
        bundled = self.bundles('linked')
        self.config['ai']['skills'] = ['linked']
        with patch.object(ai.Path, 'is_symlink', autospec=True, side_effect=lambda path: path == bundled / 'linked'):
            self.assertEqual(self.setup_ai(self.config), 1)
        self.run.assert_not_called()
        self.download.assert_not_called()

    def test_linked_bundled_resource_is_rejected_before_install(self):
        bundled = self.bundles('resources')
        resource = bundled / 'resources' / 'resource.txt'
        resource.write_text('fixture', encoding='utf-8')
        self.config['ai']['skills'] = ['resources']
        with patch.object(ai.Path, 'is_symlink', autospec=True, side_effect=lambda path: path == resource):
            self.assertEqual(self.setup_ai(self.config), 1)
        self.run.assert_not_called()
        self.download.assert_not_called()

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
        self.assertEqual(self.target.read_bytes(), (ai.BUNDLED_SKILLS / 'dev-cli' / 'SKILL.md').read_bytes())
        self.assertIn('Authentication was not changed', self.dev.emit_info.call_args.args[0])

    def test_non_runnable_existing_command_is_an_error(self):
        self.run.return_value.returncode = 7
        self.assertEqual(self.setup_existing(), 1)
        self.assertFalse(self.target.exists())
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
                self.assertFalse(self.target.exists())

    def test_installer_success_with_broken_executable_fails(self):
        self.which.side_effect = lambda name: 'winget' if name == 'winget' else None
        self.run.side_effect = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 9)]
        with patch.object(ai.platform, 'system', return_value='Windows'), \
                patch.object(ai, '_copilot_command', side_effect=[None, 'broken-copilot']):
            self.assertEqual(self.setup_ai(self.config), 1)
        self.assertFalse(self.target.exists())

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
        self.assertFalse(self.target.exists())

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

    def test_idempotent_install_does_not_rewrite_skill(self):
        self.assertEqual(self.setup_existing(), 0)
        before = (self.target.stat().st_mtime_ns, self.metadata.stat().st_mtime_ns)
        self.assertEqual(self.setup_existing(), 0)
        self.assertEqual(before, (self.target.stat().st_mtime_ns, self.metadata.stat().st_mtime_ns))
        self.dev.emit_warn.assert_not_called()

    def test_owned_skill_updates_with_source(self):
        self.assertEqual(self.setup_existing(), 0)
        bundled = self.home / 'bundled'
        source = bundled / 'dev-cli' / 'SKILL.md'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'updated public skill\n')
        with patch.object(ai, 'BUNDLED_SKILLS', bundled):
            self.assertEqual(self.setup_existing(), 0)
        self.assertEqual(self.target.read_bytes(), source.read_bytes())
        self.assertEqual(json.loads(self.metadata.read_text())['sha256'], ai._skill_digest(source.parent))

    def test_multiple_selected_packages_get_independent_metadata_and_resources(self):
        bundled = self.bundles('build-tools', 'release-notes', 'not-selected')
        resources = bundled / 'build-tools' / 'resources'
        resources.mkdir()
        (resources / 'example.txt').write_text('public resource fixture\n', encoding='utf-8')
        script = resources / 'example.sh'
        script.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        script.chmod(0o755)
        self.config['ai']['skills'] = ['build-tools', 'release-notes']
        self.assertEqual(self.setup_existing(), 0)
        skills = self.home / '.copilot' / 'skills'
        for name in self.config['ai']['skills']:
            metadata = self.metadata.parent / f'{name}.json'
            self.assertEqual(ai._skill_digest(skills / name), ai._skill_digest(bundled / name))
            self.assertEqual(json.loads(metadata.read_text())['sha256'], ai._skill_digest(bundled / name))
        self.assertEqual((skills / 'build-tools' / 'resources' / 'example.txt').read_text(),
                         'public resource fixture\n')
        if os.name != 'nt':
            self.assertTrue((skills / 'build-tools' / 'resources' / 'example.sh').stat().st_mode & 0o111)
        self.assertFalse((skills / 'not-selected').exists())
        self.assertFalse((skills / 'dev-cli').exists())
        self.assertEqual(sorted(path.name for path in skills.iterdir()), ['build-tools', 'release-notes'])

    def test_resource_updates_remove_old_managed_files(self):
        bundled = self.bundles('resources')
        source = bundled / 'resources'
        (source / 'old.txt').write_text('old', encoding='utf-8')
        self.config['ai']['skills'] = ['resources']
        self.assertEqual(self.setup_existing(), 0)
        (source / 'old.txt').unlink()
        (source / 'new.txt').write_text('new', encoding='utf-8')
        self.assertEqual(self.setup_existing(), 0)
        target = self.home / '.copilot' / 'skills' / 'resources'
        self.assertFalse((target / 'old.txt').exists())
        self.assertEqual((target / 'new.txt').read_text(), 'new')
        self.assertEqual(ai._skill_digest(target), ai._skill_digest(source))

    def test_edited_or_added_resources_preserve_entire_managed_package(self):
        bundled = self.bundles('edited', 'added')
        (bundled / 'edited' / 'resource.txt').write_text('original', encoding='utf-8')
        self.config['ai']['skills'] = ['edited', 'added']
        self.assertEqual(self.setup_existing(), 0)
        skills = self.home / '.copilot' / 'skills'
        for name in self.config['ai']['skills']:
            (skills / name / 'resource.txt').write_text('custom resource', encoding='utf-8')
            (bundled / name / 'SKILL.md').write_text('new bundled instructions', encoding='utf-8')
        original = {path: path.read_bytes() for path in skills.rglob('*') if path.is_file()}
        metadata = {path: path.read_bytes() for path in self.metadata.parent.iterdir()}
        self.assertEqual(self.setup_existing(), 0)
        self.assertEqual({path: path.read_bytes() for path in skills.rglob('*') if path.is_file()}, original)
        self.assertEqual({path: path.read_bytes() for path in self.metadata.parent.iterdir()}, metadata)
        self.assertEqual(self.dev.emit_warn.call_count, 2)

    def test_selection_preserves_private_bundle_and_installs_other_bundle(self):
        self.bundles('custom', 'shared')
        self.config['ai']['skills'] = ['custom', 'shared']
        private = self.home / '.copilot' / 'skills' / 'custom'
        private.mkdir(parents=True)
        (private / 'SKILL.md').write_text('private instructions fixture', encoding='utf-8')
        (private / 'resource.txt').write_text('private resource fixture', encoding='utf-8')
        contents = {path: path.read_bytes() for path in private.iterdir()}
        read_bytes = Path.read_bytes

        def read_public_only(path):
            self.assertNotIn(path, contents, 'Unowned private bundle must not be read')
            return read_bytes(path)

        with patch.object(ai.Path, 'read_bytes', autospec=True, side_effect=read_public_only):
            self.assertEqual(self.setup_existing(), 0)
        self.assertEqual({path: path.read_bytes() for path in private.iterdir()}, contents)
        self.assertFalse((self.metadata.parent / 'custom.json').exists())
        self.assertTrue((self.metadata.parent / 'shared.json').is_file())
        self.dev.emit_warn.assert_called_once()

    def test_changing_selection_does_not_uninstall_existing_skills(self):
        self.assertEqual(self.setup_existing(), 0)
        before = self.target.read_bytes()
        self.config['ai']['skills'] = []
        self.assertEqual(self.setup_existing(), 0)
        self.assertEqual(self.target.read_bytes(), before)

    def test_failed_package_swap_restores_owned_directory(self):
        self.assertEqual(self.setup_existing(), 0)
        bundled = self.bundles('dev-cli')
        before = self.target.read_bytes()
        rename = Path.rename

        def fail_new_install(path, destination):
            if destination == self.target.parent and not path.name.endswith('.previous'):
                raise OSError('package swap fixture failure')
            return rename(path, destination)

        with patch.object(ai.Path, 'rename', autospec=True, side_effect=fail_new_install):
            self.assertEqual(self.setup_existing(), 1)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertNotEqual(before, (bundled / 'dev-cli' / 'SKILL.md').read_bytes())
        self.assertEqual([path.name for path in self.target.parent.parent.iterdir()], ['dev-cli'])

    def test_user_edits_are_preserved(self):
        self.assertEqual(self.setup_existing(), 0)
        self.target.write_text('user customized skill\n', encoding='utf-8')
        metadata = self.metadata.read_bytes()
        self.assertEqual(self.setup_existing(), 0)
        self.assertEqual(self.target.read_text(), 'user customized skill\n')
        self.assertEqual(self.metadata.read_bytes(), metadata)
        self.dev.emit_warn.assert_called_once()

    def test_preexisting_skill_is_preserved_without_adoption(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes((ai.BUNDLED_SKILLS / 'dev-cli' / 'SKILL.md').read_bytes())
        self.assertEqual(self.setup_existing(), 0)
        self.assertFalse(self.metadata.exists())
        self.dev.emit_warn.assert_called_once()

    def test_personal_skill_is_not_read_copied_or_replaced(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'personal skill fixture\n')
        companion = self.target.parent / 'custom.txt'
        companion.write_bytes(b'personal resource fixture\n')
        original = {path: path.read_bytes() for path in self.home.rglob('*') if path.is_file()}
        read_bytes = Path.read_bytes

        def read_public_only(path):
            self.assertNotIn(path, original, 'Preexisting personal skill content must not be read')
            return read_bytes(path)

        with patch.object(ai.Path, 'read_bytes', autospec=True, side_effect=read_public_only):
            self.assertEqual(self.setup_existing(), 0)
        self.assertEqual({path: path.read_bytes() for path in self.home.rglob('*') if path.is_file()}, original)
        self.assertFalse(self.metadata.exists())
        self.assertIn('bundled skill was not installed', self.dev.emit_warn.call_args.args[0])

    def test_preexisting_empty_directory_is_preserved(self):
        self.target.parent.mkdir(parents=True)
        self.assertEqual(self.setup_existing(), 0)
        self.assertFalse(self.target.exists())
        self.dev.emit_warn.assert_called_once()

    def test_linked_skill_is_preserved(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text('linked custom skill', encoding='utf-8')
        with patch.object(ai.Path, 'is_symlink', autospec=True, side_effect=lambda path: path == self.target):
            self.assertEqual(self.setup_existing(), 0)
        self.assertEqual(self.target.read_text(), 'linked custom skill')
        self.assertFalse(self.metadata.exists())
        self.dev.emit_warn.assert_called_once()

    def test_invalid_ownership_metadata_preserves_skill(self):
        self.assertEqual(self.setup_existing(), 0)
        self.metadata.write_text('{invalid', encoding='utf-8')
        before = self.target.read_bytes()
        self.assertEqual(self.setup_existing(), 0)
        self.assertEqual(self.target.read_bytes(), before)
        self.dev.emit_warn.assert_called_once()

    def test_auth_settings_are_not_modified(self):
        settings = self.home / '.copilot' / 'config.json'
        settings.parent.mkdir()
        settings.write_text('{"user_setting": "keep"}', encoding='utf-8')
        self.assertEqual(self.setup_existing(), 0)
        self.assertEqual(settings.read_text(), '{"user_setting": "keep"}')

    def test_skill_write_failure_returns_error(self):
        with patch.object(ai, '_write_atomic', side_effect=OSError('read-only fixture')):
            self.assertEqual(self.setup_existing(), 1)
        self.assertIn('read-only fixture', self.dev.emit_error.call_args.args[0])

    def test_atomic_write_does_not_delete_a_preexisting_staging_file(self):
        target = self.home / 'fixture'
        staging = self.home / '.fixture.collision'
        staging.write_bytes(b'keep')
        with patch.object(ai.uuid, 'uuid4', return_value=SimpleNamespace(hex='collision')):
            with self.assertRaises(FileExistsError):
                ai._write_atomic(target, b'replacement')
        self.assertEqual(staging.read_bytes(), b'keep')
        self.assertFalse(target.exists())

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
                    ['native-copilot', '--allow-all', '--add-dir', str(workspace), *arguments],
                    cwd=workspace, check=False)
        self.download.assert_not_called()
        self.dev.emit_ok.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_chat_bootstraps_missing_copilot_and_skills_then_launches(self):
        with patch.object(ai, '_install_copilot', return_value='installed-copilot') as install:
            self.assertEqual(ai.run_chat(self.config, [], self.home, self.dev), 0)
        install.assert_called_once_with()
        self.assertTrue(self.target.is_file())
        self.run.assert_called_once_with(
            ['installed-copilot', '--allow-all', '--add-dir', str(self.home)],
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
                patch.object(ai, '_install_skill', side_effect=OSError('skill write failed')):
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
