#!/usr/bin/env python3
"""
Lightweight unit tests for dev.py

Run with: dev test
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import argparse
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import dev


def _sanitize_environment():
    """Drop env vars that cannot survive a patch.dict round-trip on Windows.

    unittest.mock.patch.dict always restores by clearing the dict and re-adding
    the saved items, even without clear=True. On Windows an empty-valued var is
    written back into os.environ but removed from the native environment block,
    so every subprocess started after the first patch.dict loses it. Some git
    wrappers export GIT_CONFIG_VALUE_2='' (core.fsmonitor), which leaves
    GIT_CONFIG_COUNT pointing at a missing value and makes every later git
    call fail with "missing config value GIT_CONFIG_VALUE_2".

    The GIT_CONFIG_* injection is dropped as a set so the count stays consistent.
    """
    if any(os.environ.get(f'GIT_CONFIG_VALUE_{i}', None) == ''
           for i in range(int(os.environ.get('GIT_CONFIG_COUNT') or 0))):
        for key in [k for k in os.environ if k.startswith('GIT_CONFIG_')]:
            del os.environ[key]

    for key in [k for k, v in os.environ.items() if v == '']:
        del os.environ[key]


_sanitize_environment()


class TestConfig(unittest.TestCase):
    """Test config loading and saving"""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_override_config_file = dev.OVERRIDE_CONFIG_FILE
        self.orig_sample_config_file = dev.SAMPLE_CONFIG_FILE
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.OVERRIDE_CONFIG_FILE = dev.CONFIG_DIR / 'dev_config.json'
        dev.SAMPLE_CONFIG_FILE = Path(self.temp_dir) / 'no-sample-here.json'

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.OVERRIDE_CONFIG_FILE = self.orig_override_config_file
        dev.SAMPLE_CONFIG_FILE = self.orig_sample_config_file
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_config_defaults_to_empty(self):
        """load_config should return {} when neither sample nor override exists"""
        self.assertEqual(dev.load_config(), {})
    
    def test_save_and_load_config(self):
        """Config should round-trip correctly"""
        config = {
            'repos': [{'path': 'test-repo', 'remoteUrl': 'https://example.com/test.git'}]
        }
        dev.save_config(config)
        loaded = dev.load_config()
        self.assertEqual(loaded['repos'][0]['path'], 'test-repo')

    def test_save_never_writes_sample_file(self):
        """save_config only ever writes OVERRIDE_CONFIG_FILE, never the sample."""
        dev.SAMPLE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        dev.SAMPLE_CONFIG_FILE.write_text(json.dumps({'identity': {'gitEmail': 'sample@example.com'}}))
        dev.save_config({'repos': []})
        sample = json.loads(dev.SAMPLE_CONFIG_FILE.read_text())
        self.assertEqual(sample, {'identity': {'gitEmail': 'sample@example.com'}})

    def test_override_wins_on_shared_dict_key(self):
        """Override values win per-key within a shared dict (e.g. identity)."""
        dev.SAMPLE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        dev.SAMPLE_CONFIG_FILE.write_text(json.dumps({
            'identity': {'gitEmail': 'sample@example.com', 'branchPrefix': 'user/sample/'},
            'repos': [{'path': 'sample-repo'}],
        }))
        dev.OVERRIDE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        dev.OVERRIDE_CONFIG_FILE.write_text(json.dumps({
            'identity': {'gitEmail': 'real@example.com'},
            'repos': [{'path': 'real-repo'}],
        }))
        config = dev.load_config()
        self.assertEqual(config['identity']['gitEmail'], 'real@example.com')
        self.assertEqual(config['identity']['branchPrefix'], 'user/sample/')
        self.assertEqual(config['repos'], [{'path': 'real-repo'}])

    def test_override_absent_falls_back_to_sample(self):
        dev.SAMPLE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        dev.SAMPLE_CONFIG_FILE.write_text(json.dumps({'identity': {'gitEmail': 'sample@example.com'}}))
        config = dev.load_config()
        self.assertEqual(config['identity']['gitEmail'], 'sample@example.com')

    def test_load_jsonc_preserves_comment_like_strings(self):
        dev.SAMPLE_CONFIG_FILE.write_text(
            '// Leading comment\n'
            '{"remoteUrl": "https://example.com//repo", '
            '"note": "/* literal */", /* block\ncomment */ "value": true}')
        self.assertEqual(dev.load_config(), {
            'remoteUrl': 'https://example.com//repo',
            'note': '/* literal */',
            'value': True,
        })

    def test_load_jsonc_rejects_unterminated_comment(self):
        dev.SAMPLE_CONFIG_FILE.write_text('{"value": /* unfinished')
        with self.assertRaises(json.JSONDecodeError):
            dev.load_config()

    @patch('dev._sync_repo_dir')
    @patch('dev.get_base_path')
    def test_bootstrap_path_uses_linked_sync_repository(self, mock_base, mock_sync_root):
        tool = Path(self.temp_dir) / 'dev_cli'
        tool.mkdir()
        mock_base.return_value = self.temp_dir
        mock_sync_root.return_value = tool
        config = {'repos': [{
            'path': 'dev_cli',
            'pathLinksTo': str(tool),
            'bootstrap': True,
        }]}
        self.assertEqual(dev._bootstrap_repo_path(config), tool)

    @patch('dev._sync_repo_dir')
    @patch('dev.get_base_path')
    def test_duplicate_bootstrap_entries_fail_before_sync(self, mock_base, mock_sync_root):
        mock_base.return_value = self.temp_dir
        mock_sync_root.return_value = Path(self.temp_dir)
        config = {'repos': [
            {'path': 'one', 'bootstrap': True},
            {'path': 'two', 'bootstrap': True},
        ]}
        with patch('sys.stderr', new_callable=StringIO) as errors:
            self.assertFalse(dev._bootstrap_repo_path(config))
        mock_sync_root.assert_not_called()
        self.assertIn('Only one repository', errors.getvalue())

    @patch.dict(os.environ, {'DEVCONFIG': 'skip-machine'})
    def test_skipped_bootstrap_is_not_updated(self):
        config = {'repos': [{
            'path': 'dev_cli',
            'bootstrap': True,
            'skipOn': ['skip-machine'],
        }]}
        self.assertIsNone(dev._bootstrap_repo_path(config))

    def test_repo_add_rejects_files_and_non_git_directories(self):
        file = Path(self.temp_dir) / 'note.txt'
        file.write_text('keep this file')
        directory = Path(self.temp_dir) / 'not-a-repo'
        directory.mkdir()
        for target in (file, directory):
            with self.subTest(target=target), patch('sys.stderr', new_callable=StringIO) as errors:
                self.assertEqual(dev.cmd_repo_add(argparse.Namespace(path=str(target))), 1)
                self.assertIn('Not a Git repository', errors.getvalue())
        self.assertFalse(dev.OVERRIDE_CONFIG_FILE.exists())
        self.assertEqual(file.read_text(), 'keep this file')

    def test_repo_tracking_preserves_checkout(self):
        repo = Path(self.temp_dir) / 'project'
        subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
        url = 'https://example.com/project.git'
        subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', url],
                       check=True, capture_output=True)
        marker = repo / 'note.txt'
        marker.write_text('keep this file')
        dev.save_config({'repos': []})
        with patch('dev.get_base_path', return_value=self.temp_dir):
            self.assertEqual(dev.cmd_repo_add(argparse.Namespace(path=str(repo))), 0)
        self.assertEqual(dev.load_config(), {
            'repos': [{'path': 'project', 'remoteUrl': url}]})
        self.assertEqual(dev.cmd_repo_remove(argparse.Namespace(name='project')), 0)
        self.assertEqual(dev.load_config(), {'repos': []})
        self.assertEqual(marker.read_text(), 'keep this file')


class TestComputeRepoName(unittest.TestCase):
    """Test repo name computation"""
    
    def test_simple_repo_name(self):
        """Simple repo should use folder name"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'my-repo'
            repo.mkdir()
            name = dev.compute_repo_name(repo)
            self.assertEqual(name, 'my-repo')
    
    def test_nested_gclient_repo(self):
        """Repo under gclient enlistment should use parent/name format"""
        with tempfile.TemporaryDirectory() as tmp:
            # Create structure: team/.gclient, team/src
            team = Path(tmp) / 'team'
            team.mkdir()
            (team / '.gclient').touch()
            src = team / 'src'
            src.mkdir()
            
            name = dev.compute_repo_name(src)
            self.assertEqual(name, 'team/src')


class TestGetBasePath(unittest.TestCase):
    """Test base path resolution"""

    # Patch only the keys get_base_path reads. Clearing the whole environment
    # drops empty-valued vars from the native process env on Windows (they are
    # restored into os.environ but not into the real environment block), which
    # breaks every later subprocess -- notably git, whose wrapper scripts
    # set GIT_CONFIG_VALUE_2 to an empty string.
    @patch.dict(os.environ, {'DEVCONFIG': ''})
    def test_missing_devconfig_raises(self):
        with self.assertRaises(ValueError):
            dev.get_base_path(config={})

    @patch.dict(os.environ, {'DEVCONFIG': 'unknown-machine'})
    def test_unknown_devconfig_raises(self):
        config = {'workspaceRoots': {'mac-devbox': '/Users/test'}}
        with self.assertRaises(ValueError):
            dev.get_base_path(config=config)

    @patch.dict(os.environ, {'DEVCONFIG': 'mac-devbox'})
    def test_uses_workspace_root_from_config(self):
        config = {'workspaceRoots': {'mac-devbox': '/Users/test'}}
        self.assertEqual(dev.get_base_path(config=config), '/Users/test')

    @patch.dict(os.environ, {'DEVCONFIG': 'mac-devbox', 'DEV': '/fallback'})
    def test_config_takes_priority_over_dev_env(self):
        config = {'workspaceRoots': {'mac-devbox': '/from-config'}}
        self.assertEqual(dev.get_base_path(config=config), '/from-config')


class TestResolveOverrideConfigFile(unittest.TestCase):
    """Test override config file resolution: DEV_CONFIG_OVERRIDE env var,
    else one level up from dev_cli (regardless of whether it exists yet --
    it's the private write target, not just a read lookup)."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.orig_script_dir = dev.SCRIPT_DIR
        dev.SCRIPT_DIR = self.temp_dir / 'dev_cli'
        dev.SCRIPT_DIR.mkdir()

    def tearDown(self):
        dev.SCRIPT_DIR = self.orig_script_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.dict(os.environ, {'DEV_CONFIG_OVERRIDE': '/explicit/override.json'})
    def test_env_override_wins(self):
        self.assertEqual(dev._resolve_override_config_file(), Path('/explicit/override.json'))

    @patch.dict(os.environ, {}, clear=False)
    def test_defaults_to_parent_of_dev_cli(self):
        os.environ.pop('DEV_CONFIG_OVERRIDE', None)
        self.assertEqual(dev._resolve_override_config_file(), self.temp_dir / 'dev_config.json')


class TestExpandConfigPath(unittest.TestCase):

    @patch('dev.Path.home', return_value=Path('/home/tester'))
    def test_home_without_home_environment_variable(self, mock_home):
        with patch.dict(os.environ):
            os.environ.pop('HOME', None)
            expected = str(Path('/home/tester')) + '/projects'
            self.assertEqual(dev.expand_config_path('$HOME/projects'), expected)
            self.assertEqual(dev.expand_config_path('${HOME}/projects'), expected)

    @patch('dev.Path.home', return_value=Path('/home/tester'))
    @patch.dict(os.environ, {'HOME_ARCHIVE': '/archive'})
    def test_does_not_replace_other_variable_names(self, mock_home):
        self.assertEqual(dev.expand_config_path('$HOME_ARCHIVE/projects'), '/archive/projects')

    @patch('dev.Path.home', return_value=Path('/home/tester'))
    @patch.dict(os.environ, {'DEVCONFIG': 'example-machine'})
    def test_sample_workspace_uses_real_home(self, mock_home):
        with patch.dict(os.environ):
            os.environ.pop('HOME', None)
            self.assertEqual(dev.get_base_path({
                'workspaceRoots': {'example-machine': '$HOME'}}), str(Path('/home/tester')))

    @patch('dev.Path.home', return_value=Path('/home/tester'))
    @patch.dict(os.environ, {'DEV_CONFIG_OVERRIDE': '$HOME/config/settings.json'})
    def test_override_path_expands_home(self, mock_home):
        self.assertEqual(dev._resolve_override_config_file(), Path('/home/tester/config/settings.json'))


class TestConfigGet(unittest.TestCase):
    """Test `dev config get <dotted.key>` reading from the singleton config file."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_override_config_file = dev.OVERRIDE_CONFIG_FILE
        self.orig_sample_config_file = dev.SAMPLE_CONFIG_FILE
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.CONFIG_DIR.mkdir(parents=True)
        dev.OVERRIDE_CONFIG_FILE = dev.CONFIG_DIR / 'dev_config.json'
        dev.SAMPLE_CONFIG_FILE = Path(self.temp_dir) / 'no-sample-here.json'

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.OVERRIDE_CONFIG_FILE = self.orig_override_config_file
        dev.SAMPLE_CONFIG_FILE = self.orig_sample_config_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _args(self, key):
        return argparse.Namespace(key=key)

    def test_missing_config_file_returns_1(self):
        self.assertEqual(dev.cmd_config_get(self._args('identity.gitEmail')), 1)

    def test_top_level_string_value(self):
        dev.OVERRIDE_CONFIG_FILE.write_text(json.dumps({'description': 'hello'}))
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_config_get(self._args('description'))
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), 'hello')

    def test_nested_dotted_key(self):
        dev.OVERRIDE_CONFIG_FILE.write_text(json.dumps({'identity': {'gitEmail': 'you@example.com'}}))
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_config_get(self._args('identity.gitEmail'))
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), 'you@example.com')

    def test_missing_key_returns_1(self):
        dev.OVERRIDE_CONFIG_FILE.write_text(json.dumps({'identity': {}}))
        self.assertEqual(dev.cmd_config_get(self._args('identity.gitEmail')), 1)

    def test_malformed_json_returns_1(self):
        dev.OVERRIDE_CONFIG_FILE.write_text('{not valid')
        self.assertEqual(dev.cmd_config_get(self._args('identity.gitEmail')), 1)


class TestCmdRepoOldIdentity(unittest.TestCase):
    """Test cmd_repo_old's config/git-derived identity resolution (no hardcoded user)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / 'repo'
        (self.repo_path / '.git').mkdir(parents=True)
        env = patch.dict(os.environ, {'USERNAME': 'windows-user', 'USER': 'unix-user'})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _args(self, prefix=None, days=30, delete=False):
        return argparse.Namespace(path=str(self.repo_path), prefix=prefix, days=days, delete=delete)

    @patch('dev.get_remote_url', return_value='')
    @patch('dev._scan_old_branches_git', return_value=[])
    @patch('dev._git_config_value', return_value='')
    @patch('dev.load_config', return_value={})
    def test_no_email_anywhere_errors(self, mock_load, mock_git_cfg, mock_scan, mock_remote):
        rc = dev.cmd_repo_old(self._args())
        self.assertEqual(rc, 1)
        mock_scan.assert_not_called()

    @patch('dev.get_remote_url', return_value='')
    @patch('dev._scan_old_branches_git', return_value=[])
    @patch('dev._git_config_value', return_value='someone@example.com')
    @patch('dev.load_config', return_value={})
    def test_falls_back_to_environment_and_git_email(self, mock_load, mock_git_cfg, mock_scan, mock_remote):
        rc = dev.cmd_repo_old(self._args())
        self.assertEqual(rc, 0)
        _, prefix_arg, email_arg, _cutoff = mock_scan.call_args[0]
        self.assertEqual(email_arg, 'someone@example.com')
        self.assertEqual(prefix_arg, 'user/windows-user/')

    @patch('dev.get_remote_url', return_value='')
    @patch('dev._scan_old_branches_git', return_value=[])
    @patch('dev._git_config_value', return_value='someone@example.com')
    @patch('dev.load_config', return_value={})
    def test_unix_username_fallback(self, mock_load, mock_git_cfg, mock_scan, mock_remote):
        os.environ.pop('USERNAME', None)
        self.assertEqual(dev.cmd_repo_old(self._args()), 0)
        self.assertEqual(mock_scan.call_args.args[1], 'user/unix-user/')

    @patch('dev.get_remote_url', return_value='')
    @patch('dev._scan_old_branches_git', return_value=[])
    @patch('dev._git_config_value', return_value='someone@example.com')
    @patch('dev.load_config', return_value={'identity': {'username': 'configured-user'}})
    def test_configured_username_overrides_environment(self, mock_load, mock_git_cfg, mock_scan, mock_remote):
        self.assertEqual(dev.cmd_repo_old(self._args()), 0)
        self.assertEqual(mock_scan.call_args.args[1], 'user/configured-user/')

    @patch('dev.getpass.getuser', return_value='login-user')
    @patch('dev.get_remote_url', return_value='')
    @patch('dev._scan_old_branches_git', return_value=[])
    @patch('dev._git_config_value', return_value='someone@example.com')
    @patch('dev.load_config', return_value={})
    def test_login_fallback_without_environment(self, mock_load, mock_git_cfg, mock_scan, mock_remote, mock_user):
        os.environ.pop('USERNAME', None)
        os.environ.pop('USER', None)
        self.assertEqual(dev.cmd_repo_old(self._args()), 0)
        self.assertEqual(mock_scan.call_args.args[1], 'user/login-user/')

    @patch('dev.get_remote_url', return_value='')
    @patch('dev._scan_old_branches_git', return_value=[])
    @patch('dev._git_config_value', return_value='')
    @patch('dev.load_config', return_value={'identity': {'branchPrefix': 'user/alice/',
                                                          'creatorEmail': 'alice@example.com'}})
    def test_identity_config_used_when_present(self, mock_load, mock_git_cfg, mock_scan, mock_remote):
        rc = dev.cmd_repo_old(self._args())
        self.assertEqual(rc, 0)
        _, prefix_arg, email_arg, _cutoff = mock_scan.call_args[0]
        self.assertEqual(prefix_arg, 'user/alice/')
        self.assertEqual(email_arg, 'alice@example.com')

    @patch('dev.get_remote_url', return_value='')
    @patch('dev._scan_old_branches_git', return_value=[])
    @patch('dev._git_config_value', return_value='')
    @patch('dev.load_config', return_value={'identity': {'creatorEmail': 'alice@example.com'}})
    def test_explicit_prefix_flag_overrides_identity(self, mock_load, mock_git_cfg, mock_scan, mock_remote):
        rc = dev.cmd_repo_old(self._args(prefix='user/explicit/'))
        self.assertEqual(rc, 0)
        _, prefix_arg, _email_arg, _cutoff = mock_scan.call_args[0]
        self.assertEqual(prefix_arg, 'user/explicit/')


class TestTrustClaudeWorkspace(unittest.TestCase):
    """Test auto-trusting the workspace root in ~/.claude.json"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_claude_config_file = dev.CLAUDE_CONFIG_FILE
        dev.CLAUDE_CONFIG_FILE = Path(self.temp_dir) / '.claude.json'

    def tearDown(self):
        dev.CLAUDE_CONFIG_FILE = self.orig_claude_config_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_noop_when_claude_config_missing(self):
        dev.trust_claude_workspace('/workspace')
        self.assertFalse(dev.CLAUDE_CONFIG_FILE.exists())

    def test_marks_new_project_trusted(self):
        dev.CLAUDE_CONFIG_FILE.write_text(json.dumps({'projects': {}}))
        dev.trust_claude_workspace('/workspace')
        config = json.loads(dev.CLAUDE_CONFIG_FILE.read_text())
        self.assertTrue(config['projects']['/workspace']['hasTrustDialogAccepted'])

    def test_preserves_other_project_fields(self):
        dev.CLAUDE_CONFIG_FILE.write_text(json.dumps({
            'projects': {'/workspace': {'hasTrustDialogAccepted': False, 'allowedTools': ['Bash']}}
        }))
        dev.trust_claude_workspace('/workspace')
        config = json.loads(dev.CLAUDE_CONFIG_FILE.read_text())
        self.assertTrue(config['projects']['/workspace']['hasTrustDialogAccepted'])
        self.assertEqual(config['projects']['/workspace']['allowedTools'], ['Bash'])

    def test_preserves_unrelated_top_level_keys(self):
        dev.CLAUDE_CONFIG_FILE.write_text(json.dumps({'oauthAccount': {'id': 'abc'}, 'projects': {}}))
        dev.trust_claude_workspace('/workspace')
        config = json.loads(dev.CLAUDE_CONFIG_FILE.read_text())
        self.assertEqual(config['oauthAccount'], {'id': 'abc'})

    def test_already_trusted_is_noop(self):
        dev.CLAUDE_CONFIG_FILE.write_text(json.dumps({
            'projects': {'/workspace': {'hasTrustDialogAccepted': True}}
        }))
        mtime_before = dev.CLAUDE_CONFIG_FILE.stat().st_mtime_ns
        dev.trust_claude_workspace('/workspace')
        self.assertEqual(dev.CLAUDE_CONFIG_FILE.stat().st_mtime_ns, mtime_before)

    def test_malformed_json_is_ignored(self):
        dev.CLAUDE_CONFIG_FILE.write_text('{not valid json')
        dev.trust_claude_workspace('/workspace')
        self.assertEqual(dev.CLAUDE_CONFIG_FILE.read_text(), '{not valid json')


class TestNormalizeGithubUrl(unittest.TestCase):
    """Test GitHub URL normalization to SSH with correct host aliases"""

    ALIASES = {'exampleuser': 'github.com-personal', 'acme-corp': 'github.com-work'}

    def test_https_personal_account(self):
        """HTTPS URL for an aliased personal account should use its alias"""
        url = 'https://github.com/exampleuser/dotfiles.git'
        self.assertEqual(dev.normalize_github_url(url, self.ALIASES),
                          'git@github.com-personal:exampleuser/dotfiles.git')

    def test_https_second_org(self):
        """HTTPS URL for a second aliased org should use its own alias"""
        url = 'https://github.com/acme-corp/acme-tools.git'
        self.assertEqual(dev.normalize_github_url(url, self.ALIASES),
                          'git@github.com-work:acme-corp/acme-tools.git')

    def test_https_without_git_suffix(self):
        """HTTPS URL without .git suffix should still work"""
        url = 'https://github.com/exampleuser/dotfiles'
        self.assertEqual(dev.normalize_github_url(url, self.ALIASES),
                          'git@github.com-personal:exampleuser/dotfiles.git')

    def test_ssh_personal_account(self):
        """SSH URL with github.com should be converted to its aliased host"""
        url = 'git@github.com:exampleuser/dotfiles.git'
        self.assertEqual(dev.normalize_github_url(url, self.ALIASES),
                          'git@github.com-personal:exampleuser/dotfiles.git')

    def test_ssh_second_org(self):
        """SSH URL with github.com should be converted to the second org's alias"""
        url = 'git@github.com:acme-corp/acme-tools.git'
        self.assertEqual(dev.normalize_github_url(url, self.ALIASES),
                          'git@github.com-work:acme-corp/acme-tools.git')

    def test_unknown_org_uses_default_host(self):
        """Org with no configured alias should use plain github.com"""
        url = 'https://github.com/unknown-org/some-repo.git'
        self.assertEqual(dev.normalize_github_url(url, self.ALIASES),
                          'git@github.com:unknown-org/some-repo.git')

    def test_no_aliases_configured_uses_default_host(self):
        """With no alias map at all, every org falls back to plain github.com"""
        url = 'https://github.com/exampleuser/dotfiles.git'
        self.assertEqual(dev.normalize_github_url(url), 'git@github.com:exampleuser/dotfiles.git')

    def test_non_github_url_unchanged(self):
        """Non-GitHub URLs should be returned unchanged"""
        url = 'https://dev.azure.com/contoso/Platform/_git/internal.service'
        self.assertEqual(dev.normalize_github_url(url), url)


class TestRunGit(unittest.TestCase):
    
    def test_run_git_on_invalid_path(self):
        success, output = dev.run_git('/nonexistent_path_for_test', 'status')
        self.assertFalse(success)


class TestBuildCommitMessage(unittest.TestCase):
    """Test commit message building from staged changes"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_script_dir = dev.SCRIPT_DIR
        dev.SCRIPT_DIR = Path(self.temp_dir)
        subprocess.run(['git', 'init'], cwd=self.temp_dir, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=self.temp_dir, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=self.temp_dir, capture_output=True)

    def tearDown(self):
        dev.SCRIPT_DIR = self.orig_script_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mixed_changes(self):
        """Mixed changes should show all types"""
        Path(self.temp_dir, 'existing.txt').write_text('v1')
        Path(self.temp_dir, 'to_delete.txt').write_text('bye')
        subprocess.run(['git', 'add', '.'], cwd=self.temp_dir, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'init'], cwd=self.temp_dir, capture_output=True)
        Path(self.temp_dir, 'new.txt').write_text('new')
        Path(self.temp_dir, 'existing.txt').write_text('v2')
        os.remove(Path(self.temp_dir, 'to_delete.txt'))
        subprocess.run(['git', 'add', '.'], cwd=self.temp_dir, capture_output=True)
        msg = dev._build_commit_message()
        self.assertIn('A: new.txt', msg)
        self.assertIn('M: existing.txt', msg)
        self.assertIn('D: to_delete.txt', msg)

    def test_long_message_truncated(self):
        """Long messages should fall back to summary"""
        for i in range(30):
            Path(self.temp_dir, f'file_{i:02d}_with_long_name.txt').write_text(f'content{i}')
        subprocess.run(['git', 'add', '.'], cwd=self.temp_dir, capture_output=True)
        msg = dev._build_commit_message()
        self.assertIn('30 files', msg)
        self.assertIn('30 added', msg)
        self.assertLessEqual(len(msg), 200)


class TestAdoTokenCache(unittest.TestCase):
    """Tests for the ADO token cache helpers — especially the JWT-aware expiry."""

    @staticmethod
    def _make_jwt(exp):
        """Build a minimal JWT (header.payload.sig) with the given exp claim."""
        import base64 as _b64

        def b64url(d):
            return _b64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()

        header = b64url({'alg': 'none', 'typ': 'JWT'})
        payload = b64url({'exp': exp, 'aud': 'test'})
        return f'{header}.{payload}.sig'

    def test_decode_jwt_exp_returns_claim(self):
        token = self._make_jwt(1234567890)
        self.assertEqual(dev._decode_jwt_exp(token), 1234567890.0)

    def test_decode_jwt_exp_handles_garbage(self):
        self.assertIsNone(dev._decode_jwt_exp(''))
        self.assertIsNone(dev._decode_jwt_exp(None))
        self.assertIsNone(dev._decode_jwt_exp('not-a-jwt'))
        self.assertIsNone(dev._decode_jwt_exp('only.two'))
        self.assertIsNone(dev._decode_jwt_exp('a.@@notbase64@@.c'))

    def test_cache_uses_jwt_exp_when_available(self):
        now = datetime.now(timezone.utc).timestamp()
        token_exp = now + 300  # token expires in 5 minutes
        token = self._make_jwt(token_exp)
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / 'ado_token_cache.json'
            with patch('dev.ADO_TOKEN_CACHE_FILE', cache_file), \
                 patch('dev.CONFIG_DIR', Path(tmp)):
                dev._cache_ado_token(token)
                cache = json.loads(cache_file.read_text())
            self.assertAlmostEqual(cache['expires'], token_exp - dev.ADO_TOKEN_EXPIRY_BUFFER, places=2)

    def test_cache_falls_back_to_heuristic_for_opaque_token(self):
        now = datetime.now(timezone.utc).timestamp()
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / 'ado_token_cache.json'
            with patch('dev.ADO_TOKEN_CACHE_FILE', cache_file), \
                 patch('dev.CONFIG_DIR', Path(tmp)):
                dev._cache_ado_token('not-a-jwt')
                cache = json.loads(cache_file.read_text())
            # Should be ~now + ADO_TOKEN_CACHE_SECONDS, allow some scheduling slack.
            self.assertGreaterEqual(cache['expires'], now + dev.ADO_TOKEN_CACHE_SECONDS - 5)
            self.assertLessEqual(cache['expires'], now + dev.ADO_TOKEN_CACHE_SECONDS + 5)

    def test_get_cached_rejects_expired_jwt_even_when_cache_says_valid(self):
        """Regression: az can hand us a token already past its exp; trust the JWT."""
        now = datetime.now(timezone.utc).timestamp()
        token = self._make_jwt(now - 60)  # already expired
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / 'ado_token_cache.json'
            cache_file.write_text(json.dumps({
                'token': token,
                'expires': now + 600,  # cache thinks it's still good
            }))
            with patch('dev.ADO_TOKEN_CACHE_FILE', cache_file):
                self.assertIsNone(dev._get_cached_ado_token())

    def test_get_cached_returns_valid_token(self):
        now = datetime.now(timezone.utc).timestamp()
        token = self._make_jwt(now + 600)
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / 'ado_token_cache.json'
            cache_file.write_text(json.dumps({
                'token': token,
                'expires': now + 500,
            }))
            with patch('dev.ADO_TOKEN_CACHE_FILE', cache_file):
                self.assertEqual(dev._get_cached_ado_token(), token)

    def test_get_cached_returns_none_when_cache_expiry_passed(self):
        now = datetime.now(timezone.utc).timestamp()
        token = self._make_jwt(now + 600)
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / 'ado_token_cache.json'
            cache_file.write_text(json.dumps({
                'token': token,
                'expires': now - 1,
            }))
            with patch('dev.ADO_TOKEN_CACHE_FILE', cache_file):
                self.assertIsNone(dev._get_cached_ado_token())


class TestAdoGit(unittest.TestCase):

    @patch('dev.get_ado_token', return_value=None)
    def test_no_token_returns_error(self, mock_token):
        args = argparse.Namespace(git_args=['pull'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 1)

    @patch('dev.get_ado_token', return_value='test-token')
    def test_no_git_args_returns_error(self, mock_token):
        args = argparse.Namespace(git_args=[])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 1)

    @patch('dev.get_ado_token', return_value='test-token')
    def test_strips_leading_doubledash(self, mock_token):
        args = argparse.Namespace(git_args=['--'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 1)

    @patch('subprocess.run')
    @patch('dev.get_ado_token', return_value='test-bearer-token')
    def test_runs_git_with_bearer_token(self, mock_token, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
        args = argparse.Namespace(git_args=['pull'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 0)
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], 'git')
        header_arg = [a for a in call_args if 'Authorization: Bearer' in a]
        self.assertEqual(len(header_arg), 1)
        self.assertIn('test-bearer-token', header_arg[0])
        self.assertIn('pull', call_args)

    @patch('subprocess.run')
    @patch('dev.get_ado_token', return_value='test-bearer-token')
    def test_passes_extra_git_args(self, mock_token, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
        args = argparse.Namespace(git_args=['pull', '--rebase'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 0)
        call_args = mock_run.call_args[0][0]
        self.assertIn('pull', call_args)
        self.assertIn('--rebase', call_args)

    @patch('subprocess.run')
    @patch('dev.get_ado_token', return_value='test-bearer-token')
    def test_clone_with_url(self, mock_token, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
        args = argparse.Namespace(git_args=['clone', 'https://dev.azure.com/org/proj/_git/repo'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 0)
        call_args = mock_run.call_args[0][0]
        self.assertIn('clone', call_args)
        self.assertIn('https://dev.azure.com/org/proj/_git/repo', call_args)


class TestParseAdoRemote(unittest.TestCase):

    def test_devazure_with_user_prefix(self):
        result = dev._parse_ado_remote('https://contoso@dev.azure.com/contoso/Platform/_git/internal.service')
        self.assertEqual(result, ('contoso', 'Platform', 'internal.service'))

    def test_devazure_without_user_prefix(self):
        result = dev._parse_ado_remote('https://dev.azure.com/contoso/Platform/_git/internal.service.ui')
        self.assertEqual(result, ('contoso', 'Platform', 'internal.service.ui'))

    def test_visualstudio_format(self):
        result = dev._parse_ado_remote('https://contoso.visualstudio.com/DefaultCollection/Platform/_git/bigrepo.tools')
        self.assertEqual(result, ('contoso', 'Platform', 'bigrepo.tools'))

    def test_github_url_returns_none(self):
        result = dev._parse_ado_remote('git@github.com-work:acme-corp/acme-tools.git')
        self.assertIsNone(result)

    def test_with_dot_git_suffix(self):
        result = dev._parse_ado_remote('https://dev.azure.com/org/proj/_git/repo.git')
        self.assertEqual(result, ('org', 'proj', 'repo'))


@unittest.skipUnless(shutil.which('zsh'), 'zsh is not installed')
class TestShellWorkspace(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        tool = self.home / 'dev_cli'
        (tool / 'shell').mkdir(parents=True)
        for name in ('dev', 'dev.py', 'dev_config.json', 'shell/zsh.sh'):
            shutil.copy2(Path(__file__).parent / name, tool / name)
        tools = self.home / 'bin'
        tools.mkdir()
        uname = tools / 'uname'
        uname.write_text('#!/bin/sh\nprintf "Darwin\\n"\n')
        uname.chmod(0o755)
        (tools / 'python3').symlink_to(sys.executable)
        self.env = dict(os.environ, HOME=str(self.home), DEVCONFIG='',
                        DEV_PROMPT_USER='',
                        DEV_CONFIG_OVERRIDE=str(self.home / 'dev_config.json'),
                        PATH=f'{tools}:/usr/bin:/bin:/usr/sbin:/sbin')

    def shell(self):
        return subprocess.run(
            [shutil.which('zsh'), '-f', '-c',
             'source "$HOME/dev_cli/shell/zsh.sh" || exit $?; '
             'printf "%s\\n" "$DEVCONFIG" "$DEV" "$PWD" "${PRIVATE_HOOK:-no}"'],
            env=self.env, text=True, capture_output=True)

    def test_explicit_machine_uses_private_override(self):
        workspace = self.home / 'custom workspace'
        workspace.mkdir()
        self.env['DEVCONFIG'] = 'my-machine'
        (self.home / 'dev_config.json').write_text(json.dumps({
            'workspaceRoots': {'my-machine': str(workspace)}, 'repos': []}))
        result = self.shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ['my-machine', str(workspace), str(workspace), 'no'])

    def test_private_hook_runs_after_generic_setup(self):
        hooks = self.home / 'dev_env'
        hooks.mkdir()
        (hooks / 'hooks.sh').write_text(
            '[[ "$DEVCONFIG" == "example-machine" ]] || return 1\n'
            'export DEVCONFIG=private-machine\nexport PRIVATE_HOOK=yes\n')
        result = self.shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ['private-machine', str(self.home), str(self.home), 'yes'])

    def test_unknown_machine_reports_configuration_error(self):
        self.env['DEVCONFIG'] = 'missing'
        result = self.shell()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No workspaceRoot found', result.stderr)

    def test_configured_username_reaches_shell(self):
        (self.home / 'dev_config.json').write_text(json.dumps({
            'identity': {'username': 'configured-user'}}))
        hooks = self.home / 'dev_env'
        hooks.mkdir()
        (hooks / 'hooks.sh').write_text(
            '[[ "$DEV_PROMPT_USER" == "configured-user" ]] || return 1\n')
        result = self.shell()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_copy_config_and_initialize_profile(self):
        tool = self.home / 'dev_cli'
        subprocess.run(['git', 'init', str(tool)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(tool), 'remote', 'add', 'origin',
                        'https://example.com/dev_cli.git'], check=True, capture_output=True)
        sample_bytes = (tool / 'dev_config.json').read_bytes()
        shutil.copy2(tool / 'dev_config.json', self.home / 'dev_config.json')
        config = dev.load_jsonc(tool / 'dev_config.json')
        self.assertEqual(config['workspaceRoots']['example-machine'], '$HOME')
        self.assertEqual(config['repos'], [
            {'path': 'dev_cli', 'pathLinksTo': '$HOME/dev_cli', 'bootstrap': True}])
        self.assertEqual(config['identity']['username'], '')
        result = subprocess.run(
            [sys.executable, str(tool / 'dev.py'), 'init'],
            env=dict(self.env, SHELL='/bin/zsh'), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(
            [shutil.which('zsh'), '-f', '-c',
             'source "$HOME/.zshrc" || exit $?; dev repo root; dev repo list'],
            env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.home), result.stdout)
        self.assertIn('dev_cli', result.stdout)
        self.assertIn('https://example.com/dev_cli.git', result.stdout)
        self.assertEqual((tool / 'dev_config.json').read_bytes(), sample_bytes)


class TestCmdInit(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.home = self.tmpdir / 'home'
        self.home.mkdir()
        home_patch = patch('dev.Path.home', return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    @patch.dict(os.environ, {'SHELL': '/bin/zsh'})
    @patch('dev.get_os_type', return_value='linux')
    def test_unix_zsh_already_default(self, mock_os):
        self.assertEqual(dev._init_unix(), 0)
        self.assertIn(dev.ZSHRC_SOURCE_LINE, (self.home / '.zshrc').read_text())

    @patch.dict(os.environ, {'SHELL': '/bin/zsh'})
    def test_unix_preserves_existing_zshrc(self):
        profile = self.home / '.zshrc'
        profile.write_text('export MY_SETTING=1\n')
        self.assertEqual(dev._init_unix(), 0)
        self.assertEqual(dev._init_unix(), 0)
        self.assertTrue(profile.read_text().startswith('export MY_SETTING=1\n'))
        self.assertEqual(profile.read_text().count(dev.ZSHRC_SOURCE_LINE), 1)

    @patch.dict(os.environ, {'SHELL': '/bin/bash'})
    @patch('dev.get_os_type', return_value='linux')
    @patch('shutil.which', return_value='/usr/bin/zsh')
    def test_unix_creates_bashrc(self, mock_which, mock_os):
        bashrc = self.home / '.bashrc'
        with patch('dev.Path.home', return_value=self.home):
            self.assertEqual(dev._init_unix(), 0)
        self.assertIn('dev_cli/shell/bash.sh', bashrc.read_text())

    @patch.dict(os.environ, {'SHELL': '/bin/bash'})
    @patch('dev.get_os_type', return_value='linux')
    @patch('shutil.which', return_value='/usr/bin/zsh')
    def test_unix_appends_to_bashrc(self, mock_which, mock_os):
        bashrc = self.home / '.bashrc'
        bashrc.write_text('# existing\n')
        with patch('dev.Path.home', return_value=self.home):
            self.assertEqual(dev._init_unix(), 0)
        content = bashrc.read_text()
        self.assertIn('# existing', content)
        self.assertIn('dev_cli/shell/bash.sh', content)

    @patch.dict(os.environ, {'SHELL': '/bin/bash'})
    @patch('dev.get_os_type', return_value='linux')
    @patch('shutil.which', return_value='/usr/bin/zsh')
    def test_unix_bashrc_idempotent(self, mock_which, mock_os):
        bashrc = self.home / '.bashrc'
        bashrc.write_text(f'{dev.BASHRC_SOURCE_LINE}\n')
        with patch('dev.Path.home', return_value=self.home):
            self.assertEqual(dev._init_unix(), 0)
        self.assertEqual(bashrc.read_text().count('dev_cli/shell/bash.sh'), 2)

    @patch.dict(os.environ, {'SHELL': '/bin/bash'})
    @patch('subprocess.run')
    @patch('dev.get_os_type', return_value='linux')
    @patch('shutil.which', return_value=None)
    def test_unix_installs_zsh(self, mock_which, mock_os, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
        with patch('dev.Path.home', return_value=self.home):
            self.assertEqual(dev._init_unix(), 0)
        self.assertIn('zsh', mock_run.call_args[0][0])

    @patch('subprocess.run')
    def test_windows_profile_already_set(self, mock_run):
        profile = self.tmpdir / 'profile.ps1'
        profile.write_text('$profile = "$HOME\\.psrc.ps1"\n. $profile\n')
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(profile) + '\n', stderr='')
        self.assertEqual(dev._init_windows(), 0)

    @patch('subprocess.run')
    def test_windows_creates_profile(self, mock_run):
        profile = self.tmpdir / 'Documents' / 'PowerShell' / 'Microsoft.PowerShell_profile.ps1'
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(profile) + '\n', stderr='')
        self.assertEqual(dev._init_windows(), 0)
        self.assertTrue(profile.exists())
        self.assertIn('.psrc.ps1', profile.read_text())
        self.assertIn(dev.PSRC_SOURCE_LINE, (self.home / '.psrc.ps1').read_text())

    @patch('subprocess.run')
    def test_windows_preserves_existing_profile(self, mock_run):
        profile = self.tmpdir / 'profile.ps1'
        profile.write_text('$env:MY_SETTING = "value"\n')
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=str(profile) + '\n', stderr='')
        self.assertEqual(dev._init_windows(), 0)
        self.assertTrue(profile.read_text().startswith('$env:MY_SETTING = "value"\n'))
        self.assertIn('.psrc.ps1', profile.read_text())


class TestInitConfig(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_override_config_file = dev.OVERRIDE_CONFIG_FILE
        self.orig_sample_config_file = dev.SAMPLE_CONFIG_FILE
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.OVERRIDE_CONFIG_FILE = dev.CONFIG_DIR / 'dev_config.json'
        dev.SAMPLE_CONFIG_FILE = Path(self.temp_dir) / 'dev_config.json'

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.OVERRIDE_CONFIG_FILE = self.orig_override_config_file
        dev.SAMPLE_CONFIG_FILE = self.orig_sample_config_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_errors_when_sample_missing(self):
        self.assertEqual(dev._init_config(), 1)
        self.assertFalse(dev.OVERRIDE_CONFIG_FILE.exists())

    def test_creates_override_from_sample(self):
        dev.SAMPLE_CONFIG_FILE.write_text(json.dumps({
            'workspaceRoots': {'example-machine': '$HOME'},
            'identity': {'username': ''}}))
        with patch('sys.stdin.isatty', return_value=False):
            self.assertEqual(dev._init_config(), 0)
        saved = json.loads(dev.OVERRIDE_CONFIG_FILE.read_text())
        self.assertEqual(saved['workspaceRoots']['example-machine'], '$HOME')

    def test_skips_when_override_already_exists(self):
        dev.SAMPLE_CONFIG_FILE.write_text(json.dumps({'identity': {'username': ''}}))
        dev.OVERRIDE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        dev.OVERRIDE_CONFIG_FILE.write_text(json.dumps({'identity': {'username': 'kept'}}))
        with patch('sys.stdin.isatty', return_value=True):
            self.assertEqual(dev._init_config(), 0)
        saved = json.loads(dev.OVERRIDE_CONFIG_FILE.read_text())
        self.assertEqual(saved['identity']['username'], 'kept')

    def test_prompts_for_blank_username_when_interactive(self):
        dev.SAMPLE_CONFIG_FILE.write_text(json.dumps({'identity': {'username': ''}}))
        with patch('sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value='alice'):
            self.assertEqual(dev._init_config(), 0)
        saved = json.loads(dev.OVERRIDE_CONFIG_FILE.read_text())
        self.assertEqual(saved['identity']['username'], 'alice')

    def test_does_not_prompt_when_username_already_set(self):
        dev.SAMPLE_CONFIG_FILE.write_text(json.dumps({'identity': {'username': 'bob'}}))
        with patch('sys.stdin.isatty', return_value=True), \
             patch('builtins.input', side_effect=AssertionError('should not prompt')):
            self.assertEqual(dev._init_config(), 0)
        saved = json.loads(dev.OVERRIDE_CONFIG_FILE.read_text())
        self.assertEqual(saved['identity']['username'], 'bob')


class TestCheckPython3Shim(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_script_dir = dev.SCRIPT_DIR
        dev.SCRIPT_DIR = Path(self.temp_dir)

    def tearDown(self):
        dev.SCRIPT_DIR = self.orig_script_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_noop_without_shim_file(self):
        # No exception, no output, when the shim file isn't present.
        dev._check_python3_shim()

    @patch('dev.get_os_type', return_value='darwin')
    def test_warns_when_shim_finds_no_working_interpreter(self, _mock_os):
        shim = dev.SCRIPT_DIR / 'python3'
        shim.write_text('#!/bin/sh\nexit 1\n')
        shim.chmod(0o755)
        with patch('dev.emit_warn') as mock_warn:
            dev._check_python3_shim()
        mock_warn.assert_called_once()

    @patch('dev.get_os_type', return_value='darwin')
    def test_silent_when_shim_finds_working_interpreter(self, _mock_os):
        shim = dev.SCRIPT_DIR / 'python3'
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" --version\n')
        shim.chmod(0o755)
        with patch('dev.emit_warn') as mock_warn:
            dev._check_python3_shim()
        mock_warn.assert_not_called()


class TestCmdInitSelfHealing(unittest.TestCase):
    """dev init is idempotent: re-running it restores anything deleted,
    without touching content the user has since customized."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.home = self.tmpdir / 'home'
        self.home.mkdir()
        home_patch = patch('dev.Path.home', return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)

        self.orig_script_dir = dev.SCRIPT_DIR
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_override_config_file = dev.OVERRIDE_CONFIG_FILE
        self.orig_sample_config_file = dev.SAMPLE_CONFIG_FILE
        dev.SCRIPT_DIR = self.tmpdir / 'dev_cli'  # no real python3 shim here
        dev.CONFIG_DIR = self.home
        dev.OVERRIDE_CONFIG_FILE = self.home / 'dev_config.json'
        dev.SAMPLE_CONFIG_FILE = self.tmpdir / 'dev_cli' / 'dev_config.json'
        dev.SAMPLE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        dev.SAMPLE_CONFIG_FILE.write_text(json.dumps({
            'workspaceRoots': {'example-machine': '$HOME'}, 'identity': {'username': ''}}))

    def tearDown(self):
        dev.SCRIPT_DIR = self.orig_script_dir
        dev.CONFIG_DIR = self.orig_config_dir
        dev.OVERRIDE_CONFIG_FILE = self.orig_override_config_file
        dev.SAMPLE_CONFIG_FILE = self.orig_sample_config_file
        shutil.rmtree(self.tmpdir)

    @patch.dict(os.environ, {'SHELL': '/bin/zsh'})
    @patch('dev.get_os_type', return_value='linux')
    @patch('sys.stdin')
    def test_restores_deleted_artifacts(self, mock_stdin, _mock_os):
        mock_stdin.isatty.return_value = False
        self.assertEqual(dev.cmd_init(None), 0)
        zshrc = self.home / '.zshrc'
        hook_path = dev._hook_path()
        self.assertTrue(zshrc.is_file())
        self.assertTrue(dev.OVERRIDE_CONFIG_FILE.is_file())
        self.assertTrue(hook_path.is_file())

        # Simulate loss of each artifact independently of the others.
        zshrc.unlink()
        dev.OVERRIDE_CONFIG_FILE.unlink()
        hook_path.unlink()

        self.assertEqual(dev.cmd_init(None), 0)
        self.assertIn(dev.ZSHRC_SOURCE_LINE, zshrc.read_text())
        self.assertTrue(dev.OVERRIDE_CONFIG_FILE.is_file())
        self.assertTrue(hook_path.is_file())

    @patch.dict(os.environ, {'SHELL': '/bin/zsh'})
    @patch('dev.get_os_type', return_value='linux')
    @patch('sys.stdin')
    def test_does_not_clobber_customizations(self, mock_stdin, _mock_os):
        mock_stdin.isatty.return_value = False
        self.assertEqual(dev.cmd_init(None), 0)
        dev.OVERRIDE_CONFIG_FILE.write_text(json.dumps({'identity': {'username': 'kept'}}))
        hook_path = dev._hook_path()
        hook_path.write_text('export CUSTOM=1\n')
        (self.home / '.zshrc').write_text(
            f'export CUSTOM_RC=1\n{dev.ZSHRC_SOURCE_LINE}\n')

        self.assertEqual(dev.cmd_init(None), 0)
        self.assertEqual(json.loads(dev.OVERRIDE_CONFIG_FILE.read_text()),
                         {'identity': {'username': 'kept'}})
        self.assertEqual(hook_path.read_text(), 'export CUSTOM=1\n')
        self.assertIn('export CUSTOM_RC=1', (self.home / '.zshrc').read_text())


class TestSelfUpdate(unittest.TestCase):

    def setUp(self):
        self.repo_path = dev.SCRIPT_DIR
        for target, value in (('_sync_submodules', True),
                              ('_sync_upstream', 'origin/main')):
            patcher = patch(f'dev.{target}', return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch('dev.run_git')
    def test_no_update_when_hash_unchanged(self, mock_git):
        def side_effect(path, *args):
            if args[0] == 'rev-parse':
                return (True, 'abc123')
            if args[0] == 'rev-list':
                return (True, '0\t0')
            return (True, '')
        mock_git.side_effect = side_effect
        self.assertTrue(dev._self_update(self.repo_path))
        calls = [c[0][1:] for c in mock_git.call_args_list]
        self.assertIn(('rev-parse', 'HEAD'), calls)
        self.assertNotIn('rebase', str(calls))

    @patch('dev.run_git')
    def test_skips_when_fetch_fails(self, mock_git):
        def side_effect(path, *args):
            if args[0] == 'fetch':
                return (False, '')
            if args[0] == 'rev-parse':
                return (True, 'abc123')
            return (True, '')
        mock_git.side_effect = side_effect
        self.assertFalse(dev._self_update(self.repo_path))

    @patch('subprocess.run')
    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.run_git')
    def test_sets_pulled_rcfiles_on_remote_rebase(self, mock_git, mock_branch, mock_subprocess):
        """_self_update sets _DEV_PULLED_RCFILES only when remote commits are pulled."""
        call_count = [0]
        def side_effect(path, *args):
            if args[0] == 'rev-parse':
                call_count[0] += 1
                if call_count[0] == 1:
                    return (True, 'oldhash')
                elif call_count[0] == 2:
                    return (True, 'oldhash')
                else:
                    return (True, 'newhash')
            if args[0] == 'rev-list':
                return (True, '0\t1')
            if args[0] == 'log':
                return (True, 'abc123 some commit')
            return (True, '')
        mock_git.side_effect = side_effect
        mock_subprocess.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        os.environ.pop('_DEV_PULLED_RCFILES', None)
        with self.assertRaises(SystemExit) as ctx:
            dev._self_update(self.repo_path)
        self.assertEqual(ctx.exception.code, 0)
        mock_subprocess.assert_called_once()
        self.assertEqual(os.environ.pop('_DEV_PULLED_RCFILES'), 'abc123 some commit')

    @patch('subprocess.run')
    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.run_git')
    def test_no_pulled_rcfiles_on_local_commit_only(self, mock_git, mock_branch, mock_subprocess):
        """_self_update does NOT set _DEV_PULLED_RCFILES for local-only commits."""
        call_count = [0]
        def side_effect(path, *args):
            if args[0] == 'rev-parse':
                call_count[0] += 1
                if call_count[0] == 1:
                    return (True, 'oldhash')
                else:
                    return (True, 'newhash')
            if args[0] == 'rev-list':
                return (True, '0\t0')
            if args[0] == 'status':
                return (True, 'M dev.py')
            return (True, '')
        mock_git.side_effect = side_effect
        mock_subprocess.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        os.environ.pop('_DEV_PULLED_RCFILES', None)
        with self.assertRaises(SystemExit):
            dev._self_update(self.repo_path)
        self.assertNotIn('_DEV_PULLED_RCFILES', os.environ)


class TestSyncSubmodules(unittest.TestCase):
    """Submodule updates must not commit unrelated parent edits."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.base_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('dev.run_git')
    def test_noop_without_gitmodules(self, mock_git):
        self.assertTrue(dev._sync_submodules(self.base_path))
        mock_git.assert_not_called()

    @patch('dev.run_git')
    def test_update_failure_skips_commit(self, mock_git):
        (self.base_path / '.gitmodules').write_text('[submodule "dev_cli"]\n')

        def side_effect(repo, *args):
            if args[0] == 'config':
                return (True, 'submodule.dev_cli.path dev_cli')
            if args[:2] == ('submodule', 'update'):
                return (False, 'network error')
            self.fail(f"unexpected git call: {args}")
        mock_git.side_effect = side_effect

        self.assertFalse(dev._sync_submodules(self.base_path))
        calls = [c.args[1] for c in mock_git.call_args_list]
        self.assertNotIn('commit', calls)


class TestSplitRepositorySync(unittest.TestCase):
    """Exercise the split using real local parent and submodule remotes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.parent = self.base / 'home'
        self.tool = self.base / 'tool'
        self.remote = self.base / 'tool.git'
        self.parent_remote = self.base / 'home.git'
        for repo in (self.parent, self.tool):
            self.git(self.base, 'init', '--initial-branch=main', str(repo))
            self.git(repo, 'config', 'user.name', 'Test')
            self.git(repo, 'config', 'user.email', 'test@example.com')
        (self.tool / 'dev.py').write_text('print("tool")\n')
        self.git(self.tool, 'add', 'dev.py')
        self.git(self.tool, 'commit', '-m', 'add tool')
        self.git(self.base, 'clone', '--bare', str(self.tool), str(self.remote))
        self.git(self.tool, 'remote', 'add', 'origin', str(self.remote))
        self.git(self.parent, '-c', 'protocol.file.allow=always', 'submodule', 'add',
                 '-b', 'main', str(self.remote), 'dev_cli')
        self.child = self.parent / 'dev_cli'
        self.git(self.child, 'config', 'user.name', 'Test')
        self.git(self.child, 'config', 'user.email', 'test@example.com')
        self.git(self.parent, 'commit', '-am', 'add submodule')
        self.git(self.base, 'clone', '--bare', str(self.parent), str(self.parent_remote))
        self.git(self.parent, 'remote', 'add', 'origin', str(self.parent_remote))
        self.git(self.parent, 'fetch', 'origin')
        self.git(self.parent, 'branch', '--set-upstream-to=origin/main')
        for patcher in (patch('dev.SCRIPT_DIR', self.child),
                        patch.dict(os.environ, {'GIT_ALLOW_PROTOCOL': 'file'})):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def git(path, *args):
        return subprocess.run(['git', '-C', str(path), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def test_sync_root_is_parent_not_workspace(self):
        self.assertEqual(dev._sync_repo_dir().resolve(), self.parent.resolve())
        with patch('dev.SCRIPT_DIR', self.tool):
            self.assertEqual(dev._sync_repo_dir().resolve(), self.tool.resolve())

    def test_updates_detached_submodule_without_committing_parent_edits(self):
        self.git(self.child, 'checkout', '--detach')
        parent_head = self.git(self.parent, 'rev-parse', 'HEAD')
        (self.parent / 'private.txt').write_text('keep this edit\n')
        (self.tool / 'dev.py').write_text('print("updated tool")\n')
        self.git(self.tool, 'commit', '-am', 'update tool')
        self.git(self.tool, 'push', 'origin', 'main')
        self.assertTrue(dev._sync_submodules(self.parent))
        self.assertEqual(self.git(self.child, 'rev-parse', 'HEAD'),
                         self.git(self.tool, 'rev-parse', 'HEAD'))
        self.assertEqual(self.git(self.parent, 'rev-parse', 'HEAD'), parent_head)
        self.assertEqual(self.git(self.parent, 'diff', '--cached'), '')
        self.assertEqual((self.parent / 'private.txt').read_text(), 'keep this edit\n')

    def test_dirty_child_blocks_parent_sync(self):
        (self.child / 'dev.py').write_text('uncommitted work\n')
        self.assertFalse(dev._self_update(self.parent))
        self.assertEqual((self.child / 'dev.py').read_text(), 'uncommitted work\n')
        self.assertEqual(self.git(self.parent, 'diff', '--cached'), '')

    def test_unpublished_child_commit_is_not_discarded(self):
        self.git(self.child, 'checkout', '--detach')
        (self.child / 'dev.py').write_text('local work\n')
        self.git(self.child, 'commit', '-am', 'change tool locally')
        before = self.git(self.child, 'rev-parse', 'HEAD')
        self.assertFalse(dev._sync_submodules(self.parent))
        self.assertEqual(self.git(self.child, 'rev-parse', 'HEAD'), before)

    def test_initializes_submodule_in_fresh_clone(self):
        clone = self.base / 'fresh-home'
        self.git(self.base, 'clone', str(self.parent_remote), str(clone))
        self.assertTrue(dev._sync_submodules(clone))
        self.assertTrue((clone / 'dev_cli' / 'dev.py').is_file())

    def test_push_updates_parent_feature_branch_only(self):
        branch = 'user/test/split'
        self.git(self.parent, 'switch', '-c', branch)
        self.git(self.parent, 'push', '-u', 'origin', branch)
        main_before = self.git(self.parent_remote, 'rev-parse', 'main')
        tool_before = self.git(self.remote, 'rev-parse', 'main')
        (self.parent / 'private.txt').write_text('private configuration\n')
        self.assertTrue(dev.sync_rcfiles_push())
        self.assertEqual(self.git(self.parent_remote, 'rev-parse', branch),
                         self.git(self.parent, 'rev-parse', 'HEAD'))
        self.assertEqual(self.git(self.parent_remote, 'rev-parse', 'main'), main_before)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), tool_before)
        self.assertEqual(dev._sync_upstream(self.parent), f'origin/{branch}')

    def test_bootstrap_entry_needs_no_url_or_second_sync(self):
        config = {'repos': [{
            'path': 'home',
            'pathLinksTo': str(self.parent),
            'bootstrap': True,
        }]}
        with patch('dev._self_update', return_value=True), \
             patch('dev.sync_rcfiles_push', return_value=True), \
             patch('dev.load_config', return_value=config), \
             patch('dev.get_base_path', return_value=str(self.parent)), \
             patch('dev._sync_repo_latest') as repo_sync, \
             patch('dev._ensure_link'), \
             patch('dev.emit_ok') as success:
            self.assertEqual(dev.cmd_repo_sync(argparse.Namespace()), 0)
        success.assert_called_once_with('home (updated during bootstrap)')
        repo_sync.assert_not_called()


class TestEnsureLink(unittest.TestCase):
    """Test _ensure_link symlink creation"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.base = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_symlink(self):
        """pathLinksTo repo should get a symlink from workspace path"""
        repo_path = self.base / 'actual_repo'
        repo_path.mkdir()
        (repo_path / '.git').mkdir()

        link_path = self.base / 'workspace' / 'test'

        dev._ensure_link(link_path, repo_path)

        self.assertTrue(dev._is_dir_link(link_path))
        self.assertEqual(link_path.resolve(), repo_path.resolve())

    def test_no_symlink_when_same_path(self):
        """No symlink when link_path == repo_path"""
        repo_path = self.base / 'repo'
        repo_path.mkdir()

        dev._ensure_link(repo_path, repo_path)

        self.assertFalse(repo_path.is_symlink())

    def test_replaces_stale_symlink(self):
        """Stale symlink should be replaced"""
        old_target = self.base / 'old'
        old_target.mkdir()
        new_target = self.base / 'new'
        new_target.mkdir()

        link_path = self.base / 'link'
        dev._ensure_link(link_path, old_target)
        self.assertEqual(link_path.resolve(), old_target.resolve())

        dev._ensure_link(link_path, new_target)

        self.assertTrue(dev._is_dir_link(link_path))
        self.assertEqual(link_path.resolve(), new_target.resolve())

    def test_skips_when_real_dir_exists(self):
        """Should not replace a real directory with a symlink"""
        repo_path = self.base / 'actual'
        repo_path.mkdir()
        link_path = self.base / 'existing_dir'
        link_path.mkdir()
        (link_path / 'file.txt').write_text('data')

        dev._ensure_link(link_path, repo_path)

        self.assertFalse(link_path.is_symlink())
        self.assertTrue((link_path / 'file.txt').exists())

    def test_idempotent_when_correct(self):
        """Calling again with correct symlink should be a no-op"""
        repo_path = self.base / 'repo'
        repo_path.mkdir()
        link_path = self.base / 'link'
        dev._ensure_link(link_path, repo_path)

        dev._ensure_link(link_path, repo_path)

        self.assertTrue(dev._is_dir_link(link_path))
        self.assertEqual(link_path.resolve(), repo_path.resolve())


class TestNormalizeUrlForComparison(unittest.TestCase):

    def test_ado_with_credentials_matches_without(self):
        url1 = 'https://AzToken123@dev.azure.com/contoso/Platform/_git/bigrepo.infra'
        url2 = 'https://contoso@dev.azure.com/contoso/Platform/_git/bigrepo.infra'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_ado_without_credentials_matches_config(self):
        url1 = 'https://dev.azure.com/contoso/Platform/_git/internal.service'
        url2 = 'https://contoso@dev.azure.com/contoso/Platform/_git/internal.service'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_github_ssh_alias_matches_plain(self):
        url1 = 'git@github.com-personal:exampleuser/dotfiles.git'
        url2 = 'git@github.com:exampleuser/dotfiles.git'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_github_work_alias_matches_plain(self):
        url1 = 'git@github.com-work:acme-corp/acme-tools.git'
        url2 = 'git@github.com:acme-corp/acme-tools.git'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_trailing_dot_git_ignored(self):
        url1 = 'https://dev.azure.com/contoso/Platform/_git/repo.git'
        url2 = 'https://dev.azure.com/contoso/Platform/_git/repo'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_different_repos_do_not_match(self):
        url1 = 'https://dev.azure.com/contoso/Platform/_git/repo-a'
        url2 = 'https://dev.azure.com/contoso/Platform/_git/repo-b'
        self.assertNotEqual(dev._normalize_url_for_comparison(url1),
                            dev._normalize_url_for_comparison(url2))

    def test_sync_skips_non_ado_repos(self):
        """_parse_ado_remote returns None for non-ADO URLs, so sync won't fix them."""
        github_url = 'git@github.com-personal:exampleuser/dotfiles.git'
        self.assertIsNone(dev._parse_ado_remote(github_url))


class TestCmdPrDesc(unittest.TestCase):

    def _make_args(self, **kwargs):
        defaults = dict(description=None, repo=None, branch=None, id=None)
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    @patch('dev.get_ado_token', return_value='fake-token')
    @patch('dev._resolve_pr_context')
    def test_no_input_reads_description(self, mock_ctx, mock_token):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        pr_json = json.dumps({'description': 'existing desc'}).encode()
        mock_resp = type('R', (), {'read': lambda self: pr_json, '__enter__': lambda self: self, '__exit__': lambda *a: None})()
        with patch('urllib.request.urlopen', return_value=mock_resp):
            rc = dev.cmd_pr_desc(self._make_args())
        self.assertEqual(rc, 0)

    @patch('dev._resolve_pr_context')
    def test_no_pr_found_returns_error(self, mock_ctx):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', None, 'az', None)
        rc = dev.cmd_pr_desc(self._make_args())
        self.assertEqual(rc, 1)

    @patch('dev._resolve_pr_context')
    def test_empty_description_reads_current(self, mock_ctx):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        pr_json = json.dumps({'description': 'existing desc'}).encode()
        mock_resp = type('R', (), {
            'read': lambda self: pr_json,
            '__enter__': lambda self: self,
            '__exit__': lambda *a: None,
        })()
        with patch('dev.get_ado_token', return_value='fake-token'), \
             patch('urllib.request.urlopen', return_value=mock_resp):
            rc = dev.cmd_pr_desc(self._make_args(description=[]))
        self.assertEqual(rc, 0)

    @patch('dev._print_pr_description', return_value=0)
    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_description_updates(self, mock_ctx, mock_run, mock_print):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        mock_run.return_value = type('R', (), {'returncode': 0})()
        rc = dev.cmd_pr_desc(self._make_args(description=['new desc']))
        self.assertEqual(rc, 0)
        mock_print.assert_called_once()

    @patch('dev._print_pr_description', return_value=0)
    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_multiline_description_updates(self, mock_ctx, mock_run, mock_print):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        mock_run.return_value = type('R', (), {'returncode': 0})()
        rc = dev.cmd_pr_desc(self._make_args(description=['# Title', 'Body', '', '- item']))
        self.assertEqual(rc, 0)
        mock_print.assert_called_once()


class TestParseBoolArg(unittest.TestCase):

    def test_true_values(self):
        for v in ('true', 'True', 'TRUE', 't', 'yes', 'y', '1'):
            self.assertTrue(dev._parse_bool_arg(v), f"expected True for {v!r}")

    def test_false_values(self):
        for v in ('false', 'False', 'FALSE', 'f', 'no', 'n', '0'):
            self.assertFalse(dev._parse_bool_arg(v), f"expected False for {v!r}")

    def test_none_is_true(self):
        self.assertTrue(dev._parse_bool_arg(None))

    def test_passthrough_bool(self):
        self.assertTrue(dev._parse_bool_arg(True))
        self.assertFalse(dev._parse_bool_arg(False))

    def test_invalid_raises(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            dev._parse_bool_arg('maybe')


class TestCmdPrCreate(unittest.TestCase):

    def _make_args(self, **kwargs):
        defaults = dict(title=None, description_file=None, repo=None,
                        branch=None, id=None, draft=True)
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def _ctx(self):
        return ('contoso', 'Platform', 'repo', 'feature-branch', None,
                'az', lambda *a: (0, 'main'))

    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_default_is_draft_true(self, mock_ctx, mock_run):
        mock_ctx.return_value = self._ctx()
        mock_run.return_value = type('R', (), {'returncode': 0})()
        rc = dev.cmd_pr_create(self._make_args(title='T'))
        self.assertEqual(rc, 0)
        cmd = mock_run.call_args[0][0]
        self.assertIn('--draft', cmd)
        idx = cmd.index('--draft')
        self.assertEqual(cmd[idx + 1], 'true')

    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_draft_false_is_active(self, mock_ctx, mock_run):
        mock_ctx.return_value = self._ctx()
        mock_run.return_value = type('R', (), {'returncode': 0})()
        rc = dev.cmd_pr_create(self._make_args(title='T', draft=False))
        self.assertEqual(rc, 0)
        cmd = mock_run.call_args[0][0]
        self.assertIn('--draft', cmd)
        idx = cmd.index('--draft')
        self.assertEqual(cmd[idx + 1], 'false')

    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_existing_pr_short_circuits(self, mock_ctx, mock_run):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feature-branch', 99,
                                 'az', lambda *a: (0, 'main'))
        rc = dev.cmd_pr_create(self._make_args(title='T'))
        self.assertEqual(rc, 0)
        mock_run.assert_not_called()


class TestSyncStateHelpers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix='dev_sync_state_'))
        self.patcher = patch.object(dev, 'SYNC_STATE_DIR', self.tmpdir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_state_paths_safe_for_slashes(self):
        state_path, log_path = dev._sync_state_paths('team/src')
        self.assertEqual(state_path.name, 'team__src.json')
        self.assertEqual(log_path.name, 'team__src.log')

    def test_save_load_clear_roundtrip(self):
        dev._save_sync_state('team/src', {'pid': 1234, 'status': 'running'})
        loaded = dev._load_sync_state('team/src')
        self.assertEqual(loaded['pid'], 1234)
        self.assertEqual(loaded['status'], 'running')
        dev._clear_sync_state('team/src')
        self.assertIsNone(dev._load_sync_state('team/src'))

    def test_load_returns_none_when_missing(self):
        self.assertIsNone(dev._load_sync_state('nope'))

    def test_format_duration(self):
        self.assertEqual(dev._format_duration(5), '5s')
        self.assertEqual(dev._format_duration(125), '2m 5s')
        self.assertEqual(dev._format_duration(3725), '1h 2m')


class TestReportBackgroundSyncStatus(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix='dev_sync_state_'))
        self.patcher = patch.object(dev, 'SYNC_STATE_DIR', self.tmpdir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_state_returns_false(self):
        self.assertFalse(dev._report_background_sync_status('team/src'))

    @patch('dev._is_pid_alive', return_value=True)
    def test_running_alive_returns_true(self, _mock_alive):
        dev._save_sync_state('team/src', {
            'pid': 1234, 'status': 'running', 'label': 'switching',
            'started_at': datetime.now(timezone.utc).isoformat(),
            'log_path': 'some/path.log',
        })
        self.assertTrue(dev._report_background_sync_status('team/src'))
        self.assertIsNotNone(dev._load_sync_state('team/src'))  # not cleared

    @patch('dev._is_pid_alive', return_value=False)
    def test_running_dead_clears_state(self, _mock_alive):
        dev._save_sync_state('team/src', {
            'pid': 1234, 'status': 'running', 'label': 'switching',
            'started_at': datetime.now(timezone.utc).isoformat(),
            'log_path': 'some/path.log',
        })
        self.assertFalse(dev._report_background_sync_status('team/src'))
        self.assertIsNone(dev._load_sync_state('team/src'))

    def test_succeeded_clears_state(self):
        dev._save_sync_state('team/src', {
            'pid': 1234, 'status': 'succeeded', 'label': 'switching',
            'log_path': 'some/path.log',
        })
        self.assertFalse(dev._report_background_sync_status('team/src'))
        self.assertIsNone(dev._load_sync_state('team/src'))

    def test_failed_clears_state(self):
        dev._save_sync_state('team/src', {
            'pid': 1234, 'status': 'failed', 'exit_code': 1,
            'label': 'switching', 'log_path': 'some/path.log',
        })
        self.assertFalse(dev._report_background_sync_status('team/src'))
        self.assertIsNone(dev._load_sync_state('team/src'))


class TestCmdBgSync(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix='dev_bg_sync_'))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _payload(self, ops):
        return argparse.Namespace(payload=json.dumps({
            'name': 'test-repo',
            'ops': ops,
            'state_path': str(self.tmpdir / 'state.json'),
            'log_path': str(self.tmpdir / 'op.log'),
        }))

    @patch('subprocess.run')
    def test_success_writes_succeeded_state(self, mock_run):
        mock_run.return_value = type('R', (), {'returncode': 0})()
        rc = dev.cmd_bg_sync(self._payload([
            {'argv': ['git', 'fetch', 'origin'], 'cwd': str(self.tmpdir)},
        ]))
        self.assertEqual(rc, 0)
        state = json.loads((self.tmpdir / 'state.json').read_text())
        self.assertEqual(state['status'], 'succeeded')
        self.assertEqual(state['exit_code'], 0)
        self.assertIn('finished_at', state)

    @patch('subprocess.run')
    def test_failure_stops_and_writes_failed_state(self, mock_run):
        mock_run.side_effect = [
            type('R', (), {'returncode': 0})(),
            type('R', (), {'returncode': 1})(),
        ]
        rc = dev.cmd_bg_sync(self._payload([
            {'argv': ['git', 'fetch', 'origin'], 'cwd': str(self.tmpdir)},
            {'argv': ['git', 'checkout', '-f', 'main'], 'cwd': str(self.tmpdir)},
            {'argv': ['git', 'reset', '--hard', 'origin/main'], 'cwd': str(self.tmpdir)},
        ]))
        self.assertEqual(rc, 0)
        state = json.loads((self.tmpdir / 'state.json').read_text())
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['exit_code'], 1)
        self.assertEqual(mock_run.call_count, 2)

    @patch('subprocess.run')
    def test_mixed_ops_uses_per_op_cwd(self, mock_run):
        mock_run.return_value = type('R', (), {'returncode': 0})()
        gclient_cwd = str(self.tmpdir / 'parent')
        rc = dev.cmd_bg_sync(self._payload([
            {'argv': ['git', 'fetch', 'origin'], 'cwd': str(self.tmpdir)},
            {'argv': ['gclient', 'sync', '-Df'], 'cwd': gclient_cwd},
        ]))
        self.assertEqual(rc, 0)
        # Verify second call used gclient cwd
        second_call = mock_run.call_args_list[1]
        self.assertEqual(second_call.kwargs.get('cwd'), gclient_cwd)
        self.assertEqual(second_call.args[0][0], 'gclient')


class TestCheckStaleBranchSlowSync(unittest.TestCase):
    """Op composition; the unblock helpers are covered by their own tests."""

    def setUp(self):
        for target, value in (('dev._clear_pinned_index_bits', []),
                              ('dev._stash_before_switch', None)):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch('dev._spawn_background_sync', return_value=99999)
    @patch('dev._sync_state_paths', return_value=(Path('s.json'), Path('s.log')))
    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.get_branch_age_days', return_value=30)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('builtins.input', return_value='y')
    def test_slow_sync_spawns_bg_and_returns(self, _i, _c, _a, _d, _sp, mock_spawn):
        with patch('subprocess.run') as mock_run:
            dev.check_stale_branch(Path('/tmp/repo'), 'team/src', slow_sync=True)
            mock_run.assert_not_called()
            mock_spawn.assert_called_once()
            ops = mock_spawn.call_args[0][1]
            self.assertEqual(len(ops), 3)
            # No sync hook when none configured
            self.assertTrue(all(op['argv'][0] == 'git' for op in ops))

    @patch('dev._spawn_background_sync', return_value=99999)
    @patch('dev._sync_state_paths', return_value=(Path('s.json'), Path('s.log')))
    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.get_branch_age_days', return_value=30)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('builtins.input', return_value='y')
    def test_slow_sync_with_sync_command_appends_op(self, _i, _c, _a, _d, _sp, mock_spawn):
        dev.check_stale_branch(Path('/tmp/repo'), 'team/src',
                               slow_sync=True, sync_command='gclient sync -Df')
        ops = mock_spawn.call_args[0][1]
        self.assertEqual(len(ops), 4)
        self.assertEqual(ops[-1]['argv'], ['gclient', 'sync', '-Df'])
        # cwd should be parent of repo
        self.assertEqual(ops[-1]['cwd'], str(Path('/tmp/repo').parent))

    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.get_branch_age_days', return_value=30)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('builtins.input', return_value='y')
    @patch('subprocess.run')
    def test_no_slow_sync_runs_synchronously(self, mock_run, _i, _c, _a, _d):
        mock_run.return_value = type('R', (), {'returncode': 0})()
        dev.check_stale_branch(Path('/tmp/repo'), 'team/src', slow_sync=False)
        self.assertEqual(mock_run.call_count, 3)

    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.get_branch_age_days', return_value=30)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('builtins.input', return_value='y')
    @patch('subprocess.run')
    def test_no_slow_sync_with_sync_command_runs_four(self, mock_run, _i, _c, _a, _d):
        mock_run.return_value = type('R', (), {'returncode': 0})()
        dev.check_stale_branch(Path('/tmp/repo'), 'team/src',
                               slow_sync=False, sync_command='gclient sync -Df')
        self.assertEqual(mock_run.call_count, 4)
        # Last call should be the sync hook
        self.assertEqual(mock_run.call_args_list[-1].args[0][0], 'gclient')


class TestConfirm(unittest.TestCase):
    """Test confirm(): prompt answers and --force auto-yes."""

    @patch('builtins.input', return_value='')
    def test_empty_takes_default(self, _i):
        self.assertTrue(dev.confirm('q? ', default_yes=True))
        self.assertFalse(dev.confirm('q? ', default_yes=False))

    @patch('builtins.input', return_value='N')
    def test_explicit_no(self, _i):
        self.assertFalse(dev.confirm('q? ', default_yes=True))

    @patch('builtins.input', return_value='Y')
    def test_explicit_yes(self, _i):
        self.assertTrue(dev.confirm('q? ', default_yes=False))

    @patch('builtins.input', side_effect=EOFError)
    def test_eof_is_no(self, _i):
        self.assertFalse(dev.confirm('q? ', default_yes=True))

    @patch('builtins.input', side_effect=AssertionError('should not prompt'))
    def test_assume_yes_skips_prompt(self, _i):
        self.assertTrue(dev.confirm('q? ', default_yes=False, assume_yes=True))


class TestCheckStaleBranchForce(unittest.TestCase):
    """Test check_stale_branch honors assume_yes (dev repo sync -f)."""

    def setUp(self):
        for target, value in (('dev._clear_pinned_index_bits', []),
                              ('dev._stash_before_switch', None)):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.get_branch_age_days', return_value=30)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('builtins.input', side_effect=AssertionError('should not prompt'))
    @patch('subprocess.run')
    def test_assume_yes_switches_without_prompt(self, mock_run, _i, _c, _a, _d):
        mock_run.return_value = type('R', (), {'returncode': 0})()
        dev.check_stale_branch(Path('/tmp/repo'), 'team/src', assume_yes=True)
        self.assertEqual(mock_run.call_count, 3)

    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.get_branch_age_days', return_value=30)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('builtins.input', side_effect=EOFError)
    @patch('subprocess.run')
    def test_without_force_eof_does_not_switch(self, mock_run, _i, _c, _a, _d):
        dev.check_stale_branch(Path('/tmp/repo'), 'team/src')
        mock_run.assert_not_called()

    @patch('dev.emit_ok')
    @patch('dev.emit_error')
    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.get_branch_age_days', return_value=30)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('subprocess.run')
    def test_failed_op_reports_error_and_stops(self, mock_run, _c, _a, _d, mock_err, mock_ok):
        mock_run.return_value = type('R', (), {'returncode': 1})()
        dev.check_stale_branch(Path('/tmp/repo'), 'team/src', assume_yes=True)
        self.assertEqual(mock_run.call_count, 1)
        mock_ok.assert_not_called()
        mock_err.assert_called_once()
        self.assertIn('user/x/old', mock_err.call_args[0][0])

    @patch('dev._stash_before_switch', return_value='dev-sync user/x/old 2026-01-01 00:00:00')
    @patch('dev._clear_pinned_index_bits', return_value=['pinned/skip.ts'])
    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.get_branch_age_days', return_value=30)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('subprocess.run')
    def test_unblocks_before_switching(self, mock_run, _c, _a, _d, mock_clear, mock_stash):
        mock_run.return_value = type('R', (), {'returncode': 0})()
        dev.check_stale_branch(Path('/tmp/repo'), 'pump.ui', assume_yes=True)
        mock_clear.assert_called_once()
        mock_stash.assert_called_once()
        # Unpin + stash happen before the checkout, never after it.
        self.assertEqual(mock_run.call_count, 3)
        self.assertIn('checkout', mock_run.call_args_list[1].args[0])


class TestClearPinnedIndexBits(unittest.TestCase):
    """skip-worktree / assume-unchanged bits are what block a branch switch."""

    def test_parses_skip_worktree_and_assume_unchanged(self):
        listing = ('H normal.txt\n'
                   'S pinned/skip.ts\n'
                   'h assumed.props\n'
                   'H other.txt\n')
        with patch('dev.run_git', return_value=(True, listing)), \
             patch('subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {'returncode': 0})()
            cleared = dev._clear_pinned_index_bits(Path('/tmp/repo'))
        self.assertEqual(sorted(cleared), ['assumed.props', 'pinned/skip.ts'])
        # Each bit needs its own invocation; git honors only the last flag when both
        # are passed together, silently leaving the other bit set.
        self.assertEqual(mock_run.call_count, 2)
        first, second = (call.args[0] for call in mock_run.call_args_list)
        self.assertEqual(first[-3:], ['--no-skip-worktree', '--', 'pinned/skip.ts'])
        self.assertEqual(second[-3:], ['--no-assume-unchanged', '--', 'assumed.props'])

    def test_only_skip_worktree_runs_one_command(self):
        with patch('dev.run_git', return_value=(True, 'H a.txt\nS b.ts\n')), \
             patch('subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {'returncode': 0})()
            cleared = dev._clear_pinned_index_bits(Path('/tmp/repo'))
        self.assertEqual(cleared, ['b.ts'])
        self.assertEqual(mock_run.call_count, 1)
        self.assertIn('--no-skip-worktree', mock_run.call_args[0][0])

    def test_no_pinned_files_runs_nothing(self):
        with patch('dev.run_git', return_value=(True, 'H a.txt\nH b.txt\n')), \
             patch('subprocess.run') as mock_run:
            self.assertEqual(dev._clear_pinned_index_bits(Path('/tmp/repo')), [])
            mock_run.assert_not_called()

    def test_failed_update_index_reports_nothing_cleared(self):
        with patch('dev.run_git', return_value=(True, 'S a.txt\n')), \
             patch('subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {'returncode': 1})()
            self.assertEqual(dev._clear_pinned_index_bits(Path('/tmp/repo')), [])


class TestStashBeforeSwitch(unittest.TestCase):
    """A forced checkout must park uncommitted work first."""

    def test_clean_tree_stashes_nothing(self):
        with patch('dev.run_git', return_value=(True, '')), \
             patch('subprocess.run') as mock_run:
            self.assertIsNone(dev._stash_before_switch(Path('/tmp/repo'), 'user/x/old'))
            mock_run.assert_not_called()

    def test_dirty_tree_is_stashed_with_label(self):
        with patch('dev.run_git', return_value=(True, ' M a.txt')), \
             patch('subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {'returncode': 0})()
            label = dev._stash_before_switch(Path('/tmp/repo'), 'user/x/old')
        self.assertIsNotNone(label)
        self.assertTrue(label.startswith('dev-sync user/x/old '))
        argv = mock_run.call_args[0][0]
        self.assertIn('stash', argv)
        self.assertIn('--include-untracked', argv)

    def test_failed_stash_returns_none(self):
        with patch('dev.run_git', return_value=(True, ' M a.txt')), \
             patch('subprocess.run') as mock_run:
            mock_run.return_value = type('R', (), {'returncode': 1})()
            self.assertIsNone(dev._stash_before_switch(Path('/tmp/repo'), 'user/x/old'))


class TestSyncRepoLatest(unittest.TestCase):
    """Test _sync_repo_latest: safe fetch + default-branch update."""

    def setUp(self):
        changes = patch('dev.get_dirty_paths', return_value=[])
        self.changes = changes.start()
        self.addCleanup(changes.stop)

    @staticmethod
    def _subproc(fetch=0, reset=0, ff=0):
        def se(cmd, *a, **k):
            argv = cmd
            if 'reset' in argv:
                rc = reset
            elif '--prune' in argv:
                rc = fetch
            else:  # refspec fetch (default:default)
                rc = ff
            return type('R', (), {'returncode': rc, 'stdout': '', 'stderr': ''})()
        return se

    @staticmethod
    def _rungit(origin_sha='aaa', local_sha='aaa', local_ok=True):
        def se(repo, *args):
            if args and args[0] == 'rev-parse':
                ref = args[1]
                if ref.startswith('origin/'):
                    return (True, origin_sha)
                return (local_ok, local_sha)
            return (True, '')
        return se

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_on_default_clean_behind_resets(self, _d, _c):
        with patch('subprocess.run', side_effect=self._subproc()), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('updated', 'main'))

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_on_default_up_to_date_is_current(self, _d, _c):
        with patch('subprocess.run', side_effect=self._subproc()), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='aaa')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('current', 'main'))

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_on_default_dirty_is_not_reset(self, _d, _c):
        self.changes.return_value = ['f.py']
        reset_called = {'n': 0}

        def se(cmd, *a, **k):
            if 'reset' in cmd:
                reset_called['n'] += 1
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch('subprocess.run', side_effect=se), \
             patch('dev.get_dirty_age_days', return_value=1), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('dirty', 'main'))
        self.assertEqual(reset_called['n'], 0)

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_on_default_stale_dirty_is_stashed_then_reset(self, _d, _c):
        self.changes.side_effect = [['f.py'], []]
        calls = []

        def se(cmd, *a, **k):
            calls.append(cmd)
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch('subprocess.run', side_effect=se), \
             patch('dev.get_dirty_age_days', return_value=dev.STALE_DAYS), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('reset', 'main'))
        stash = [c for c in calls if 'stash' in c]
        reset = [c for c in calls if 'reset' in c]
        self.assertEqual(len(stash), 1)
        self.assertIn('--include-untracked', stash[0])
        self.assertEqual(len(reset), 1)
        # The stash must happen before the reset, or the changes are gone.
        self.assertLess(calls.index(stash[0]), calls.index(reset[0]))

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_stale_dirty_at_origin_sha_is_stashed_without_reset(self, _d, _c):
        self.changes.side_effect = [['f.py'], []]
        calls = []

        def se(cmd, *a, **k):
            calls.append(cmd)
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch('subprocess.run', side_effect=se), \
             patch('dev.get_dirty_age_days', return_value=99), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='aaa')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('reset', 'main'))
        self.assertEqual(len([c for c in calls if 'stash' in c]), 1)
        self.assertEqual(len([c for c in calls if 'reset' in c]), 0)

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_stash_that_does_not_clean_tree_is_preserved(self, _d, _c):
        self.changes.side_effect = [['f.py'], ['still-dirty.py']]
        calls = []

        def se(cmd, *a, **k):
            calls.append(cmd)
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch('subprocess.run', side_effect=se), \
             patch('dev.get_dirty_age_days', return_value=99), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))

        self.assertEqual((status, default), ('stash-incomplete', 'main'))
        self.assertEqual(len([c for c in calls if 'stash' in c]), 1)
        self.assertEqual(len([c for c in calls if 'drop' in c]), 0)
        self.assertEqual(len([c for c in calls if 'reset' in c]), 0)

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_failed_inspection_never_resets_or_drops_stash(self, _d, _c):
        for changes in ([None], [['f.py'], None]):
            with self.subTest(changes=changes), \
                 patch('subprocess.run', side_effect=self._subproc()) as mock_run, \
                 patch('dev.get_dirty_age_days', return_value=99), \
                 patch('dev.run_git', side_effect=self._rungit()):
                self.changes.side_effect = changes
                self.assertEqual(dev._sync_repo_latest(Path('/tmp/repo')), ('failed', 'main'))
                self.assertFalse(any('reset' in c.args[0] or 'drop' in c.args[0]
                                     for c in mock_run.call_args_list))

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_failed_stash_does_not_reset(self, _d, _c):
        self.changes.return_value = ['f.py']
        calls = []

        def se(cmd, *a, **k):
            calls.append(cmd)
            rc = 1 if 'stash' in cmd else 0
            return type('R', (), {'returncode': rc, 'stdout': '', 'stderr': ''})()

        with patch('subprocess.run', side_effect=se), \
             patch('dev.get_dirty_age_days', return_value=99), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('stash-failed', 'main'))
        self.assertEqual(len([c for c in calls if 'reset' in c]), 0)

    @patch('dev.get_current_branch', return_value='main')
    @patch('dev.get_default_branch', return_value='main')
    def test_undatable_dirty_tree_is_not_reset(self, _d, _c):
        self.changes.return_value = ['gone.py']
        calls = []

        def se(cmd, *a, **k):
            calls.append(cmd)
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch('subprocess.run', side_effect=se), \
             patch('dev.get_dirty_age_days', return_value=None), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('dirty-unknown', 'main'))
        self.assertEqual(len([c for c in calls if 'reset' in c or 'stash' in c]), 0)

    @patch('dev.get_current_branch', return_value='user/x/feat')
    @patch('dev.get_default_branch', return_value='main')
    def test_on_feature_fastforwards_default(self, _d, _c):
        with patch('subprocess.run', side_effect=self._subproc(ff=0)), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('updated', 'main'))

    @patch('dev.get_current_branch', return_value='user/x/feat')
    @patch('dev.get_default_branch', return_value='main')
    def test_on_feature_nonff_is_diverged(self, _d, _c):
        with patch('subprocess.run', side_effect=self._subproc(ff=1)), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual((status, default), ('diverged', 'main'))

    @patch('dev.get_default_branch', return_value='main')
    def test_fetch_failure_is_failed(self, _d):
        with patch('subprocess.run', side_effect=self._subproc(fetch=1)):
            status, default = dev._sync_repo_latest(Path('/tmp/repo'))
        self.assertEqual(status, 'failed')

    @patch('dev.get_current_branch', return_value='mirror/main')
    def test_configured_default_is_passed_through(self, _c):
        with patch('subprocess.run', side_effect=self._subproc()), \
             patch('dev.run_git', side_effect=self._rungit(origin_sha='aaa', local_sha='bbb')), \
             patch('dev.get_default_branch', return_value='mirror/main') as mock_default:
            status, default = dev._sync_repo_latest(Path('/tmp/repo'), 'mirror/main')
        self.assertEqual((status, default), ('updated', 'mirror/main'))
        self.assertEqual(mock_default.call_args[0][1], 'mirror/main')


class TestGetDirtyPaths(unittest.TestCase):
    def _inspect(self, status, raw_sha='abc'):
        result = subprocess.CompletedProcess([], 0, os.fsencode(status), b'')
        with patch('subprocess.run', return_value=result), \
             patch('dev.run_git', side_effect=lambda repo, *args:
                   (True, '' if args[0] == 'rev-parse' else raw_sha)):
            return dev.get_dirty_paths(Path('/tmp/repo'))

    def test_clean_and_byte_identical_files_are_excluded(self):
        self.assertEqual(self._inspect(''), [])
        self.assertEqual(self._inspect(
            '1 .M N... 100644 100644 100644 abc abc file.cs\0'), [])

    def test_real_changes_are_not_excluded(self):
        records = [
            '1 .M N... 100644 100644 100644 abc abc file.cs',
            '1 M. N... 100644 100644 100644 old abc file.cs',
            '1 MM N... 100644 100644 100644 old abc file.cs',
            '1 .M N... 100644 100644 100755 abc abc file.cs',
            '1 .M N... 120000 120000 120000 abc abc file.cs',
            '1 .M S.M. 160000 160000 160000 abc abc file.cs',
            '1 .D N... 100644 100644 000000 abc abc file.cs',
            'u UU N... 100644 100644 100644 100644 abc def ghi file.cs',
            '? file.cs',
        ]
        for record in records:
            with self.subTest(record=record):
                self.assertEqual(self._inspect(record + '\0', raw_sha='changed'), ['file.cs'])
        for record in records[1:]:
            with self.subTest(same_bytes=record):
                self.assertEqual(self._inspect(record + '\0'), ['file.cs'])

    def test_rename_consumes_source_and_preserves_unusual_paths(self):
        path = ' leading -> path"\n.cs '
        status = (f'2 R. N... 100644 100644 100644 abc abc R100 {path}\0old.cs\0'
                  '? untracked/file.cs\0')
        self.assertEqual(self._inspect(status), [path, 'untracked/file.cs'])

    def test_raw_hash_receives_exact_path(self):
        path = ' leading -> path"\n.cs '
        result = subprocess.CompletedProcess(
            [], 0, os.fsencode(f'1 .M N... 100644 100644 100644 abc abc {path}\0'), b'')
        with patch('subprocess.run', return_value=result), \
             patch('dev.run_git', side_effect=[(True, ''), (True, 'abc')]) as mock_git:
            self.assertEqual(dev.get_dirty_paths(Path('/tmp/repo')), [])
        mock_git.assert_any_call(
            Path('/tmp/repo'), 'hash-object', '--no-filters', '--', path)

    def test_inspection_errors_fail_closed(self):
        failures = [
            subprocess.CompletedProcess([], 1, b'', b'git failed'),
            subprocess.CompletedProcess([], 0, b'1 incomplete\0', b''),
            subprocess.CompletedProcess([], 0, b'unexpected\0', b''),
        ]
        for result in failures:
            with self.subTest(result=result), patch('subprocess.run', return_value=result), \
                 patch('dev.run_git', return_value=(True, '')), \
                 patch('sys.stderr', new_callable=StringIO) as output:
                self.assertIsNone(dev.get_dirty_paths(Path('/tmp/repo')))
                self.assertIn('[X]', output.getvalue())
        for error in (OSError('cannot run git'), subprocess.TimeoutExpired('git', 30)):
            with self.subTest(error=error), patch('subprocess.run', side_effect=error), \
                 patch('sys.stderr', new_callable=StringIO):
                self.assertIsNone(dev.get_dirty_paths(Path('/tmp/repo')))

    def test_hash_failure_is_not_treated_as_clean(self):
        result = subprocess.CompletedProcess(
            [], 0, b'1 .M N... 100644 100644 100644 abc abc file.cs\0', b'')
        with patch('subprocess.run', return_value=result), \
             patch('dev.run_git', side_effect=[(True, ''), (False, '')]), \
             patch('sys.stderr', new_callable=StringIO):
            self.assertIsNone(dev.get_dirty_paths(Path('/tmp/repo')))


class TestNormalizationSync(unittest.TestCase):
    """Exercise normalization artifacts against real, isolated Git repositories."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='dev-normalization-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.upstream = self.root / 'upstream'
        self.repo = self.root / 'repo'
        environment = patch.dict(os.environ, {
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_COUNT': '0',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.upstream.mkdir()
        self._git(self.upstream, 'init', '--template=', '-b', 'main')
        self._git(self.upstream, 'config', 'user.email', 'test@example.invalid')
        self._git(self.upstream, 'config', 'user.name', 'Test')
        self._git(self.upstream, 'config', 'core.autocrlf', 'false')
        (self.upstream / '.gitattributes').write_bytes(b'* -text\n')
        (self.upstream / 'sample.cs').write_bytes(b'class Sample {}\r\n')
        (self.upstream / 'notes.txt').write_bytes(b'original\n')
        self._git(self.upstream, 'add', '.')
        self._git(self.upstream, 'commit', '-qm', 'Initial files')
        (self.upstream / '.gitattributes').write_bytes(b'* -text\n*.cs text eol=crlf\n')
        self._git(self.upstream, 'add', '.gitattributes')
        self._git(self.upstream, 'commit', '-qm', 'Require normalization')
        self._git(self.root, 'clone', '--quiet', '--no-hardlinks',
                  str(self.upstream), str(self.repo))
        self._git(self.repo, 'config', 'user.email', 'test@example.invalid')
        self._git(self.repo, 'config', 'user.name', 'Test')
        self.initial = self._git(self.repo, 'rev-parse', 'HEAD')
        self.assertIn(b'sample.cs', self._git(self.repo, 'status', '--porcelain'))
        (self.upstream / 'notes.txt').write_bytes(b'upstream\n')
        self._git(self.upstream, 'add', 'notes.txt')
        self._git(self.upstream, 'commit', '-qm', 'Upstream update')

    @staticmethod
    def _git(repo, *args):
        result = subprocess.run(
            ['git', '-C', str(repo), *args], capture_output=True, check=True, timeout=30)
        return result.stdout

    def test_phantom_changes_sync_without_stashing_or_rewriting_on_repeat(self):
        self.assertEqual(dev.get_dirty_paths(self.repo), [])
        self.assertEqual(dev._sync_repo_latest(self.repo), ('updated', 'main'))
        self.assertEqual(self._git(self.repo, 'rev-parse', 'HEAD'),
                         self._git(self.upstream, 'rev-parse', 'HEAD'))
        mtime = (self.repo / 'sample.cs').stat().st_mtime_ns
        self.assertEqual(dev._sync_repo_latest(self.repo), ('current', 'main'))
        self.assertEqual((self.repo / 'sample.cs').stat().st_mtime_ns, mtime)
        self.assertEqual(self._git(self.repo, 'stash', 'list'), b'')

    def test_real_edits_including_line_endings_are_protected(self):
        for contents in (b'class Changed {}\r\n', b'class Sample {}\n', b'class Sample {} \r\n'):
            with self.subTest(contents=contents):
                (self.repo / 'sample.cs').write_bytes(contents)
                self.assertEqual(dev._sync_repo_latest(self.repo), ('dirty', 'main'))
                self.assertEqual((self.repo / 'sample.cs').read_bytes(), contents)
                self.assertEqual(self._git(self.repo, 'rev-parse', 'HEAD'), self.initial)
                self.assertEqual(self._git(self.repo, 'stash', 'list'), b'')

    def test_staged_and_untracked_work_is_protected(self):
        self._git(self.repo, 'config', 'status.showUntrackedFiles', 'no')
        self._git(self.repo, 'add', '--renormalize', 'sample.cs')
        (self.repo / 'new.txt').write_bytes(b'untracked\n')
        self.assertEqual(set(dev.get_dirty_paths(self.repo)), {'sample.cs', 'new.txt'})
        self.assertEqual(dev._sync_repo_latest(self.repo), ('dirty', 'main'))
        self.assertEqual(self._git(self.repo, 'rev-parse', 'HEAD'), self.initial)
        self.assertEqual((self.repo / 'new.txt').read_bytes(), b'untracked\n')
        self.assertIn(b'sample.cs', self._git(self.repo, 'diff', '--cached', '--name-only'))

    def test_stale_real_work_ignores_recent_phantom_timestamp_and_keeps_stash(self):
        old_time = time.time() - 40 * 86400
        for name in ('notes.txt', 'new.txt'):
            (self.repo / name).write_bytes(b'local work\n')
            os.utime(self.repo / name, (old_time, old_time))
        self.assertEqual(dev.get_dirty_age_days(self.repo), 40)
        self.assertEqual(dev._sync_repo_latest(self.repo), ('reset', 'main'))
        self.assertEqual((self.repo / 'notes.txt').read_bytes(), b'upstream\n')
        self.assertFalse((self.repo / 'new.txt').exists())
        self.assertEqual(self._git(self.repo, 'show', 'stash@{0}:notes.txt'), b'local work\n')
        self.assertEqual(self._git(self.repo, 'show', 'stash@{0}^3:new.txt'), b'local work\n')
        stash = self._git(self.repo, 'rev-parse', 'refs/stash')
        self.assertEqual(dev._sync_repo_latest(self.repo), ('current', 'main'))
        self.assertEqual(self._git(self.repo, 'rev-parse', 'refs/stash'), stash)

    def test_sync_from_subdirectory_uses_relative_paths_regardless_of_config(self):
        nested = self.repo / 'nested'
        nested.mkdir()
        self._git(self.repo, 'config', 'status.relativePaths', 'false')
        self.assertEqual(dev.get_dirty_paths(nested), [])
        (self.repo / 'notes.txt').write_bytes(b'local work\n')
        self.assertEqual(dev.get_dirty_paths(nested), ['../notes.txt'])
        self.assertEqual(dev._sync_repo_latest(nested), ('dirty', 'main'))
        self.assertEqual(self._git(self.repo, 'rev-parse', 'HEAD'), self.initial)
        self.assertEqual((self.repo / 'notes.txt').read_bytes(), b'local work\n')

    def test_repo_sync_command_reports_success_and_specific_skip_reasons(self):
        config = {'repos': [{'path': 'repo', 'remoteUrl': str(self.upstream)}]}
        with patch('dev._self_update'), patch('dev.sync_rcfiles_push'), \
             patch('dev.load_config', return_value=config), \
             patch('dev.get_base_path', return_value=str(self.root)), \
             patch('dev._report_background_sync_status', return_value=False), \
             patch('sys.argv', ['dev', 'repo', 'sync']), \
             patch('sys.stdout', new_callable=StringIO) as output:
            self.assertEqual(dev.main(), 0)
            self.assertIn('[OK] repo (synced to origin/main)', output.getvalue())
            self.assertIn('Synced: 1 | Skipped: 0 | Failed: 0', output.getvalue())
            for status, reason in (
                ('dirty', 'uncommitted changes newer than 14d'),
                ('dirty-unknown', 'uncommitted change age unknown'),
                ('stash-failed', 'could not stash local changes'),
                ('stash-incomplete', 'local changes remain after stashing; stash preserved'),
            ):
                with self.subTest(status=status), \
                     patch('dev._sync_repo_latest', return_value=(status, 'main')):
                    output.seek(0)
                    output.truncate(0)
                    self.assertEqual(dev.main(), 0)
                    self.assertIn(f'[WARN] repo ({reason}; default not reset)', output.getvalue())
                    self.assertIn('Synced: 0 | Skipped: 1 | Failed: 0', output.getvalue())
                    if status != 'dirty':
                        self.assertNotIn('newer than', output.getvalue())


class TestGetDirtyAgeDays(unittest.TestCase):
    """Working-tree change age is measured from file mtimes, not commit dates."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _touch(self, rel, age_days):
        path = Path(self.tmp) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x')
        when = time.time() - age_days * 86400
        os.utime(path, (when, when))
        return path

    def test_clean_tree_is_none(self):
        with patch('dev.get_dirty_paths', return_value=[]):
            self.assertIsNone(dev.get_dirty_age_days(Path(self.tmp)))

    def test_uses_newest_change(self):
        self._touch('old.py', 90)
        self._touch('new.py', 2)
        with patch('dev.get_dirty_paths', return_value=['old.py', 'new.py']):
            self.assertEqual(dev.get_dirty_age_days(Path(self.tmp)), 2)

    def test_supplied_paths_do_not_repeat_inspection(self):
        self._touch('old.py', 40)
        self._touch('other.py', 80)
        with patch('dev.get_dirty_paths') as mock_paths:
            self.assertEqual(dev.get_dirty_age_days(Path(self.tmp), ['old.py', 'other.py']), 40)
        mock_paths.assert_not_called()

    def test_stale_change_reports_its_age(self):
        self._touch('old.py', 40)
        with patch('dev.get_dirty_paths', return_value=['old.py']):
            self.assertEqual(dev.get_dirty_age_days(Path(self.tmp)), 40)

    def test_rename_uses_destination_path(self):
        self._touch('new.py', 30)
        with patch('dev.get_dirty_paths', return_value=['new.py']):
            self.assertEqual(dev.get_dirty_age_days(Path(self.tmp)), 30)

    def test_deletions_only_is_none(self):
        with patch('dev.get_dirty_paths', return_value=['gone.py']):
            self.assertIsNone(dev.get_dirty_age_days(Path(self.tmp)))

    def test_spaced_path(self):
        self._touch('spaced name.py', 25)
        with patch('dev.get_dirty_paths', return_value=['spaced name.py']):
            self.assertEqual(dev.get_dirty_age_days(Path(self.tmp)), 25)


class TestGetDefaultBranch(unittest.TestCase):
    """A configured defaultBranch wins over origin/HEAD when it exists on origin."""

    @staticmethod
    def _rungit(existing_remote_branches, origin_head='refs/remotes/origin/master'):
        def se(repo, *args):
            if args and args[0] == 'show-ref':
                ref = args[-1]
                return (ref.split('refs/remotes/origin/')[-1] in existing_remote_branches, '')
            if args and args[0] == 'symbolic-ref':
                return (bool(origin_head), origin_head)
            return (True, '')
        return se

    def test_configured_branch_wins_over_origin_head(self):
        with patch('dev.run_git', side_effect=self._rungit({'master', 'main', 'mirror/main'})):
            self.assertEqual(dev.get_default_branch(Path('/tmp/repo'), 'mirror/main'), 'mirror/main')

    def test_missing_configured_branch_falls_back_to_origin_head(self):
        with patch('dev.run_git', side_effect=self._rungit({'master'})):
            self.assertEqual(dev.get_default_branch(Path('/tmp/repo'), 'mirror/main'), 'master')

    def test_no_configured_branch_uses_origin_head(self):
        with patch('dev.run_git', side_effect=self._rungit({'master'})):
            self.assertEqual(dev.get_default_branch(Path('/tmp/repo')), 'master')

    def test_falls_back_to_main_when_no_origin_head(self):
        with patch('dev.run_git', side_effect=self._rungit({'main'}, origin_head='')):
            self.assertEqual(dev.get_default_branch(Path('/tmp/repo')), 'main')


class TestCheckStaleBranchConfiguredDefault(unittest.TestCase):
    """A mirror/* branch is only exempt from the stale prompt when it is not the default."""

    def setUp(self):
        for target, value in (('dev._clear_pinned_index_bits', []),
                              ('dev._stash_before_switch', None)):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch('dev.get_branch_age_days', return_value=365)
    @patch('dev.get_current_branch', return_value='mirror/main')
    @patch('builtins.input', return_value='y')
    @patch('subprocess.run')
    def test_configured_mirror_default_is_not_prompted(self, mock_run, _i, _c, _a):
        with patch('dev.get_default_branch', return_value='mirror/main'):
            dev.check_stale_branch(Path('/tmp/repo'), 'bigrepo.infra',
                                   default_branch='mirror/main')
        mock_run.assert_not_called()

    @patch('dev.get_branch_age_days', return_value=365)
    @patch('dev.get_current_branch', return_value='mirror/old')
    @patch('builtins.input', return_value='y')
    @patch('subprocess.run')
    def test_other_mirror_branch_still_exempt(self, mock_run, _i, _c, _a):
        with patch('dev.get_default_branch', return_value='mirror/main'):
            dev.check_stale_branch(Path('/tmp/repo'), 'bigrepo.infra',
                                   default_branch='mirror/main')
        mock_run.assert_not_called()

    @patch('dev.get_branch_age_days', return_value=365)
    @patch('dev.get_current_branch', return_value='user/x/old')
    @patch('builtins.input', return_value='y')
    @patch('subprocess.run')
    def test_feature_branch_switches_to_configured_default(self, mock_run, _i, _c, _a):
        mock_run.return_value = type('R', (), {'returncode': 0})()
        with patch('dev.get_default_branch', return_value='mirror/main'):
            dev.check_stale_branch(Path('/tmp/repo'), 'bigrepo.infra',
                                   default_branch='mirror/main')
        self.assertEqual(mock_run.call_count, 3)
        self.assertIn('mirror/main', mock_run.call_args_list[1].args[0])


class TestCmdPrDiff(unittest.TestCase):

    def _make_args(self, **kwargs):
        defaults = dict(repo=None, branch=None, id=None, diff_args=[])
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    @patch('dev._resolve_pr_context')
    def test_returns_error_when_no_context(self, mock_ctx):
        mock_ctx.return_value = None
        rc = dev.cmd_pr_diff(self._make_args())
        self.assertEqual(rc, 1)

    @patch('subprocess.run')
    @patch('dev.get_ado_token', return_value='fake-token')
    @patch('dev._resolve_pr_context')
    def test_fetches_target_from_pr_api(self, mock_ctx, mock_token, mock_run):
        git_fn = lambda *a: (0, '')
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', git_fn)
        pr_json = json.dumps({'targetRefName': 'refs/heads/main'}).encode()
        mock_resp = type('R', (), {
            'read': lambda self: pr_json,
            '__enter__': lambda self: self,
            '__exit__': lambda *a: None,
        })()
        mock_run.return_value = type('R', (), {'returncode': 0})()
        with patch('urllib.request.urlopen', return_value=mock_resp):
            rc = dev.cmd_pr_diff(self._make_args())
        self.assertEqual(rc, 0)
        diff_call = [c for c in mock_run.call_args_list if 'diff' in c[0][0]]
        self.assertTrue(len(diff_call) > 0)
        self.assertIn('origin/main...HEAD', ' '.join(diff_call[-1][0][0]))

    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_falls_back_to_default_branch(self, mock_ctx, mock_run):
        def git_fn(*cmd_args):
            if 'rev-parse' in cmd_args:
                return 0, 'origin/main'
            return 0, ''
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', None, 'az', git_fn)
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='origin/main\n', stderr='')
        rc = dev.cmd_pr_diff(self._make_args())
        self.assertEqual(rc, 0)

    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_id_mode_uses_remote_refs(self, mock_ctx, mock_run):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', None, 'az', None)
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='origin/master\n', stderr='')
        rc = dev.cmd_pr_diff(self._make_args())
        self.assertEqual(rc, 0)
        diff_call = [c for c in mock_run.call_args_list if 'diff' in c[0][0]]
        self.assertTrue(len(diff_call) > 0)
        self.assertIn('origin/feat', ' '.join(diff_call[-1][0][0]))

    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_extra_args_passed_to_git_diff(self, mock_ctx, mock_run):
        git_fn = lambda *a: (0, 'origin/main')
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', None, 'az', git_fn)
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='origin/main\n', stderr='')
        rc = dev.cmd_pr_diff(self._make_args(diff_args=['--', '--stat']))
        self.assertEqual(rc, 0)
        diff_call = [c for c in mock_run.call_args_list if 'diff' in c[0][0]]
        self.assertTrue(len(diff_call) > 0)
        self.assertIn('--stat', diff_call[-1][0][0])


    @patch('dev._fetch_pr')
    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_reuses_resolved_pr_without_refetch(self, mock_ctx, mock_run, mock_fetch):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='origin/master\n', stderr='')
        args = self._make_args(resolved_pr={'targetRefName': 'refs/heads/main'})
        rc = dev.cmd_pr_diff(args)
        self.assertEqual(rc, 0)
        mock_fetch.assert_not_called()
        diff_call = [c for c in mock_run.call_args_list if 'diff' in c[0][0]]
        self.assertTrue(len(diff_call) > 0)
        self.assertIn('origin/main...origin/feat', ' '.join(diff_call[-1][0][0]))


class TestCmdPrComments(unittest.TestCase):

    def _make_args(self, **kwargs):
        defaults = dict(repo=None, branch=None, id=None)
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    @patch('dev._resolve_pr_context')
    def test_returns_error_when_no_context(self, mock_ctx):
        mock_ctx.return_value = None
        rc = dev.cmd_pr_comments(self._make_args())
        self.assertEqual(rc, 1)

    @patch('dev._resolve_pr_context')
    def test_no_pr_id_returns_error(self, mock_ctx):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', None, 'az', None)
        rc = dev.cmd_pr_comments(self._make_args())
        self.assertEqual(rc, 1)

    @patch('dev._fetch_pr_threads', return_value=None)
    @patch('dev._resolve_pr_context')
    def test_fetch_failure_returns_error(self, mock_ctx, mock_threads):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        rc = dev.cmd_pr_comments(self._make_args())
        self.assertEqual(rc, 1)

    @patch('dev._fetch_pr_threads', return_value=[])
    @patch('dev._resolve_pr_context')
    def test_no_active_threads_returns_ok(self, mock_ctx, mock_threads):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        from io import StringIO
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args())
        self.assertEqual(rc, 0)
        self.assertIn('no human or bot comment threads', buf.getvalue())

    @patch('dev._fetch_pr_threads')
    @patch('dev._resolve_pr_context')
    def test_active_threads_include_ids(self, mock_ctx, mock_threads):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        mock_threads.return_value = [
            {
                'id': 100,
                'status': 'active',
                'threadContext': {
                    'filePath': '/src/foo.py',
                    'rightFileStart': {'line': 42, 'offset': 1},
                },
                'comments': [
                    {
                        'id': 1,
                        'commentType': 'text',
                        'author': {'displayName': 'Alice'},
                        'publishedDate': '2026-06-04T12:34:56.789Z',
                        'content': 'Nit: rename this var',
                    },
                    {
                        'id': 2,
                        'parentCommentId': 1,
                        'commentType': 'text',
                        'author': {'displayName': 'Bob'},
                        'publishedDate': '2026-06-04T13:00:00.000Z',
                        'content': 'Agreed',
                    },
                ],
            },
            {
                'id': 200,
                'status': 'fixed',
                'comments': [{'id': 9, 'commentType': 'text', 'content': 'old'}],
            },
            {
                'id': 300,
                'status': 'active',
                'properties': {'CodeReviewThreadType': {'$value': 'VoteUpdate'}},
                'comments': [{
                    'id': 10,
                    'commentType': 'system',
                    'author': {'displayName': 'System'},
                    'publishedDate': '2026-06-04T14:00:00Z',
                    'content': 'voted',
                }],
            },
            {
                'id': 350,
                'status': 'fixed',
                'comments': [{
                    'id': 11,
                    'commentType': 'system',
                    'author': {'displayName': 'GitOps (Git LowPriv)'},
                    'publishedDate': '2026-06-04T14:30:00Z',
                    'content': 'PR Assistant bot finding',
                }],
            },
            {
                'id': 400,
                'comments': [{
                    'id': 1,
                    'commentType': 'text',
                    'author': {'displayName': 'Carol'},
                    'publishedDate': '2026-06-04T15:00:00Z',
                    'content': 'No status thread',
                }],
            },
            {
                'id': 500,
                'status': 'byDesign',
                'comments': [{'id': 1, 'commentType': 'text', 'content': 'wontfix'}],
            },
            {
                'id': 600,
                'status': 'active',
                'isDeleted': True,
                'comments': [{'id': 1, 'commentType': 'text', 'content': 'deleted'}],
            },
        ]
        from io import StringIO
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args())
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('PR !42', out)
        self.assertIn('thread 100', out)
        self.assertIn('#1', out)
        self.assertIn('#2', out)
        self.assertIn('reply to #1', out)
        self.assertIn('/src/foo.py:42', out)
        self.assertIn('Alice', out)
        self.assertIn('Bob', out)
        self.assertIn('Nit: rename this var', out)
        # Resolved threads (human/bot) are now shown and labelled resolved
        self.assertIn('thread 200', out)
        self.assertIn('old', out)
        self.assertIn('thread 500', out)
        self.assertIn('wontfix', out)
        self.assertIn('resolved', out)
        # Threads with null/missing status are treated as open
        self.assertIn('thread 400', out)
        self.assertIn('No status thread', out)
        # ADO system-activity threads (CodeReviewThreadType) are hidden as noise
        self.assertNotIn('thread 300', out)
        self.assertNotIn('voted', out)
        # Bot review comments are shown even when commentType is 'system'
        self.assertIn('thread 350', out)
        self.assertIn('PR Assistant bot finding', out)
        # Deleted threads are hidden
        self.assertNotIn('thread 600', out)
        self.assertNotIn('deleted', out)

    @patch('dev._fetch_pr_threads')
    @patch('dev._resolve_pr_context')
    def test_active_flag_hides_resolved_threads(self, mock_ctx, mock_threads):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        mock_threads.return_value = [
            {
                'id': 100,
                'status': 'active',
                'comments': [{
                    'id': 1, 'commentType': 'text',
                    'author': {'displayName': 'Alice'},
                    'publishedDate': '2026-06-04T12:00:00Z',
                    'content': 'still open',
                }],
            },
            {
                'id': 200,
                'status': 'fixed',
                'comments': [{
                    'id': 2, 'commentType': 'text',
                    'author': {'displayName': 'Bob'},
                    'publishedDate': '2026-06-04T13:00:00Z',
                    'content': 'already fixed',
                }],
            },
            {
                'id': 500,
                'status': 'byDesign',
                'comments': [{
                    'id': 3, 'commentType': 'text',
                    'author': {'displayName': 'Carol'},
                    'publishedDate': '2026-06-04T14:00:00Z',
                    'content': 'by design',
                }],
            },
        ]
        from io import StringIO
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args(active=True))
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        # Active thread is shown
        self.assertIn('thread 100', out)
        self.assertIn('still open', out)
        # Resolved threads are hidden with --active
        self.assertNotIn('thread 200', out)
        self.assertNotIn('already fixed', out)
        self.assertNotIn('thread 500', out)
        self.assertNotIn('by design', out)

    @patch('dev._fetch_pr_threads')
    @patch('dev._resolve_pr_context')
    def test_active_flag_no_active_threads_message(self, mock_ctx, mock_threads):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        mock_threads.return_value = [{
            'id': 200,
            'status': 'fixed',
            'comments': [{
                'id': 2, 'commentType': 'text',
                'author': {'displayName': 'Bob'},
                'publishedDate': '2026-06-04T13:00:00Z',
                'content': 'already fixed',
            }],
        }]
        from io import StringIO
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args(active=True))
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('no active (unresolved) comment threads', out)

    @patch('dev._fetch_pr_threads')
    @patch('dev._resolve_pr_context')
    def test_pr_level_thread_no_file(self, mock_ctx, mock_threads):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        mock_threads.return_value = [{
            'id': 500,
            'status': 'active',
            'comments': [{
                'id': 7,
                'commentType': 'text',
                'author': {'displayName': 'BuildBot'},
                'publishedDate': '2026-01-01T00:00:00Z',
                'content': 'Build failed.',
            }],
        }]
        from io import StringIO
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args())
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('PR-level', out)
        self.assertIn('thread 500', out)
        self.assertIn('#7', out)
        self.assertIn('BuildBot', out)
        self.assertIn('Build failed.', out)

    @patch('dev._fetch_pr_threads')
    @patch('dev._resolve_pr_context')
    def test_mixed_thread_keeps_system_entries(self, mock_ctx, mock_threads):
        # A thread with at least one human/bot comment is kept, and its
        # interleaved system entries are shown for context.
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'main', 42, 'az', None)
        mock_threads.return_value = [{
            'id': 700,
            'status': 'active',
            'comments': [
                {
                    'id': 1,
                    'commentType': 'text',
                    'author': {'displayName': 'Dave'},
                    'publishedDate': '2026-01-01T00:00:00Z',
                    'content': 'Please fix',
                },
                {
                    'id': 2,
                    'commentType': 'system',
                    'author': {'displayName': 'System'},
                    'publishedDate': '2026-01-01T01:00:00Z',
                    'content': 'resolved the thread',
                },
            ],
        }]
        from io import StringIO
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args())
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('thread 700', out)
        self.assertIn('Please fix', out)
        self.assertIn('resolved the thread', out)


class TestPrCommentPosting(unittest.TestCase):

    def _make_args(self, **kwargs):
        defaults = dict(repo=None, branch=None, id=None, message=[],
                        reply=None, parent=1, file=None, line=None, resolve=False)
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_apply_bot_prefix_adds_marker(self):
        out = dev._apply_bot_prefix('hello world')
        self.assertTrue(out.startswith('Code-review-bot:'))
        self.assertIn('hello world', out)

    def test_apply_bot_prefix_not_duplicated(self):
        already = 'Code-review-bot:\nalready stamped'
        self.assertEqual(dev._apply_bot_prefix(already), already)

    def test_apply_bot_prefix_not_duplicated_with_leading_space(self):
        # A message that already carries the marker (even indented) is not re-stamped.
        out = dev._apply_bot_prefix('  Code-review-bot: inline note')
        self.assertEqual(out.count('Code-review-bot:'), 1)

    @patch('dev._post_pr_thread_new', return_value={'id': 1})
    @patch('dev._resolve_pr_context')
    def test_posting_pre_prefixed_message_not_doubled(self, mock_ctx, mock_new):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args(message=['Code-review-bot:', 'noted']))
        self.assertEqual(rc, 0)
        content = mock_new.call_args[0][4]
        self.assertEqual(content.count('Code-review-bot:'), 1)

    @patch('dev._post_pr_thread_new', return_value={'id': 555})
    @patch('dev._resolve_pr_context')
    def test_posting_new_pr_level_thread_stamps_prefix(self, mock_ctx, mock_new):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args(message=['some', 'feedback']))
        self.assertEqual(rc, 0)
        content = mock_new.call_args[0][4]
        self.assertTrue(content.startswith('Code-review-bot:'))
        self.assertIn('some\nfeedback', content)

    @patch('dev._post_pr_thread_reply', return_value={'id': 9})
    @patch('dev._resolve_pr_context')
    def test_posting_reply_uses_thread_and_parent(self, mock_ctx, mock_reply):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args(message=['ack'], reply=700, parent=2))
        self.assertEqual(rc, 0)
        pos = mock_reply.call_args[0]
        self.assertEqual(pos[4], 700)  # thread_id
        self.assertEqual(pos[5], 2)    # parent_id
        self.assertTrue(pos[6].startswith('Code-review-bot:'))

    @patch('dev._post_pr_thread_new', return_value={'id': 1})
    @patch('dev._resolve_pr_context')
    def test_posting_file_anchored_thread(self, mock_ctx, mock_new):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args(message=['bug here'], file='src/a.cs', line=10))
        self.assertEqual(rc, 0)
        self.assertEqual(mock_new.call_args[0][5], 'src/a.cs')
        self.assertEqual(mock_new.call_args[0][6], 10)

    @patch('dev._resolve_pr_context')
    def test_line_without_file_errors(self, mock_ctx):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        rc = dev.cmd_pr_comments(self._make_args(message=['x'], line=5))
        self.assertEqual(rc, 1)

    @patch('dev._resolve_pr_context')
    def test_reply_with_file_errors(self, mock_ctx):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        rc = dev.cmd_pr_comments(self._make_args(message=['x'], reply=1, file='src/a.cs'))
        self.assertEqual(rc, 1)

    @patch('dev._set_pr_thread_status', return_value={'id': 700})
    @patch('dev._post_pr_thread_reply', return_value={'id': 9})
    @patch('dev._resolve_pr_context')
    def test_reply_with_resolve_marks_thread_fixed(self, mock_ctx, mock_reply, mock_status):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args(message=['done'], reply=700, resolve=True))
        self.assertEqual(rc, 0)
        mock_reply.assert_called_once()
        self.assertEqual(mock_status.call_args[0][4], 700)   # thread_id
        self.assertEqual(mock_status.call_args[0][5], 'fixed')

    @patch('dev._set_pr_thread_status', return_value={'id': 700})
    @patch('dev._post_pr_thread_reply')
    @patch('dev._resolve_pr_context')
    def test_resolve_without_message_skips_reply(self, mock_ctx, mock_reply, mock_status):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        buf = StringIO()
        with patch('sys.stdout', buf):
            rc = dev.cmd_pr_comments(self._make_args(reply=700, resolve=True))
        self.assertEqual(rc, 0)
        mock_reply.assert_not_called()
        self.assertEqual(mock_status.call_args[0][4], 700)

    @patch('dev._resolve_pr_context')
    def test_resolve_without_reply_errors(self, mock_ctx):
        mock_ctx.return_value = ('contoso', 'Platform', 'repo', 'feat', 42, 'az', None)
        rc = dev.cmd_pr_comments(self._make_args(message=['x'], resolve=True))
        self.assertEqual(rc, 1)


class TestAdoGetJson(unittest.TestCase):

    @patch('dev.get_ado_token', return_value='tok')
    def test_returns_parsed_json(self, _tok):
        payload = json.dumps({'a': 1}).encode()
        resp = type('R', (), {
            'read': lambda self: payload,
            '__enter__': lambda self: self,
            '__exit__': lambda *a: None,
        })()
        with patch('urllib.request.urlopen', return_value=resp):
            self.assertEqual(dev._ado_get_json('http://x'), {'a': 1})

    @patch('dev.get_ado_token', return_value='tok')
    def test_returns_none_on_httperror(self, _tok):
        import urllib.error
        err = urllib.error.HTTPError('http://x', 404, 'nf', {}, None)
        with patch('urllib.request.urlopen', side_effect=err):
            self.assertIsNone(dev._ado_get_json('http://x'))

    @patch('dev.get_ado_token', return_value=None)
    def test_returns_none_without_token(self, _tok):
        self.assertIsNone(dev._ado_get_json('http://x'))


class TestColorsAndEmit(unittest.TestCase):

    def tearDown(self):
        dev.Colors.configure(dev._color_default_enabled())

    def test_configure_toggles_codes(self):
        dev.Colors.configure(True)
        self.assertTrue(dev.Colors.RED)
        dev.Colors.configure(False)
        self.assertEqual(dev.Colors.RED, '')
        self.assertEqual(dev.Colors.NC, '')

    def test_emit_helpers_route_to_expected_streams(self):
        dev.Colors.configure(False)
        out, err = StringIO(), StringIO()
        with patch('sys.stdout', out), patch('sys.stderr', err):
            dev.emit_ok('good')
            dev.emit_error('bad')
            dev.emit_warn('careful')
        self.assertIn('[OK] good', out.getvalue())
        self.assertIn('[X] bad', err.getvalue())
        self.assertIn('[WARN] careful', err.getvalue())
        self.assertNotIn('bad', out.getvalue())

    def test_version_is_semver(self):
        self.assertRegex(dev.__version__, r'^\d+\.\d+\.\d+$')


if __name__ == '__main__':
    unittest.main(verbosity=2, buffer=True)
