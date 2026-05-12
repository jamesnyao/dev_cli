#!/usr/bin/env python3
"""
Lightweight unit tests for dev.py

Run with: dev test
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
import argparse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import dev


class TestGetOsType(unittest.TestCase):
    """Test OS type detection"""
    
    @patch('platform.system')
    def test_linux(self, mock_system):
        mock_system.return_value = 'Linux'
        self.assertEqual(dev.get_os_type(), 'linux')
    
    @patch('platform.system')
    def test_darwin(self, mock_system):
        mock_system.return_value = 'Darwin'
        self.assertEqual(dev.get_os_type(), 'darwin')
    
    @patch('platform.system')
    def test_windows(self, mock_system):
        mock_system.return_value = 'Windows'
        self.assertEqual(dev.get_os_type(), 'windows')


class TestConfig(unittest.TestCase):
    """Test config loading and saving"""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_config_file = dev.CONFIG_FILE
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.CONFIG_FILE = dev.CONFIG_DIR / 'repos.json'
    
    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.CONFIG_FILE = self.orig_config_file
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_load_config_creates_default(self):
        """load_config should raise when config file is missing"""
        with self.assertRaises(FileNotFoundError):
            dev.load_config()
    
    def test_save_and_load_config(self):
        """Config should round-trip correctly"""
        config = {
            'repos': [{'path': 'test-repo', 'remoteUrl': 'https://example.com/test.git'}]
        }
        dev.save_config(config)
        loaded = dev.load_config()
        self.assertEqual(loaded['repos'][0]['path'], 'test-repo')


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
            # Create structure: platform/.gclient, platform/src
            platform = Path(tmp) / 'platform'
            platform.mkdir()
            (platform / '.gclient').touch()
            src = platform / 'src'
            src.mkdir()
            
            name = dev.compute_repo_name(src)
            self.assertEqual(name, 'platform/src')


class TestHasRealConflictMarkers(unittest.TestCase):
    """Test conflict marker detection"""
    
    def test_no_conflicts(self):
        """Normal content should not be detected as conflict"""
        content = "# Title\n\n## Section\nSome content\n"
        self.assertFalse(dev.has_real_conflict_markers(content))
    
    def test_real_conflict_markers(self):
        """Real conflict markers should be detected"""
        content = "## Section\n<<<<<<< HEAD\nVersion A\n=======\nVersion B\n>>>>>>> branch\n"
        self.assertTrue(dev.has_real_conflict_markers(content))
    
    def test_example_markers_in_backticks(self):
        """Example markers in backticks should NOT be detected as conflicts"""
        content = "If you see `<<<<<<<` markers, resolve them.\n"
        self.assertFalse(dev.has_real_conflict_markers(content))
    
    def test_example_markers_in_code_block(self):
        """Example markers in code blocks should NOT be detected as conflicts"""
        content = "```\n<<<<<<< branch\n=======\n>>>>>>> other\n```\n"
        self.assertFalse(dev.has_real_conflict_markers(content))


class TestGetBasePath(unittest.TestCase):
    """Test base path resolution"""
    
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_devconfig_raises(self):
        with self.assertRaises(ValueError):
            dev.get_base_path(config={})

    @patch.dict(os.environ, {'DEVCONFIG': 'unknown-machine'}, clear=True)
    def test_unknown_devconfig_raises(self):
        config = {'workspaceRoots': {'mac-devbox': '/Users/test'}}
        with self.assertRaises(ValueError):
            dev.get_base_path(config=config)

    @patch.dict(os.environ, {'DEVCONFIG': 'mac-devbox'}, clear=True)
    def test_uses_workspace_root_from_config(self):
        config = {'workspaceRoots': {'mac-devbox': '/Users/test'}}
        self.assertEqual(dev.get_base_path(config=config), '/Users/test')

    @patch.dict(os.environ, {'DEVCONFIG': 'mac-devbox', 'DEV': '/fallback'}, clear=True)
    def test_config_takes_priority_over_dev_env(self):
        config = {'workspaceRoots': {'mac-devbox': '/from-config'}}
        self.assertEqual(dev.get_base_path(config=config), '/from-config')


class TestNormalizeGithubUrl(unittest.TestCase):
    """Test GitHub URL normalization to SSH with correct host aliases"""
    
    def test_https_personal_account(self):
        """HTTPS URL for personal account should use github.com-personal"""
        url = 'https://github.com/example-user/rcfiles.git'
        self.assertEqual(dev.normalize_github_url(url), 'git@github.com-personal:example-user/rcfiles.git')
    
    def test_https_edge_org(self):
        """HTTPS URL for acme-corp org should use github.com-work"""
        url = 'https://github.com/acme-corp/platform-agents.git'
        self.assertEqual(dev.normalize_github_url(url), 'git@github.com-work:acme-corp/platform-agents.git')
    
    def test_https_without_git_suffix(self):
        """HTTPS URL without .git suffix should still work"""
        url = 'https://github.com/example-user/rcfiles'
        self.assertEqual(dev.normalize_github_url(url), 'git@github.com-personal:example-user/rcfiles.git')
    
    def test_ssh_personal_account(self):
        """SSH URL with github.com should be converted to github.com-personal"""
        url = 'git@github.com:example-user/rcfiles.git'
        self.assertEqual(dev.normalize_github_url(url), 'git@github.com-personal:example-user/rcfiles.git')
    
    def test_ssh_edge_org(self):
        """SSH URL with github.com should be converted to github.com-work"""
        url = 'git@github.com:acme-corp/platform-agents.git'
        self.assertEqual(dev.normalize_github_url(url), 'git@github.com-work:acme-corp/platform-agents.git')
    
    def test_unknown_org_uses_default_host(self):
        """Unknown org should use plain github.com"""
        url = 'https://github.com/unknown-org/some-repo.git'
        self.assertEqual(dev.normalize_github_url(url), 'git@github.com:unknown-org/some-repo.git')
    
    def test_non_github_url_unchanged(self):
        """Non-GitHub URLs should be returned unchanged"""
        url = 'https://dev.azure.com/contoso/platform/_git/internal.service'
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

    def test_no_staged_changes(self):
        """No staged changes should return Auto-sync"""
        msg = dev._build_commit_message()
        self.assertEqual(msg, 'Auto-sync')

    def test_added_files(self):
        """Added files should show A: prefix"""
        Path(self.temp_dir, 'new.txt').write_text('content')
        subprocess.run(['git', 'add', 'new.txt'], cwd=self.temp_dir, capture_output=True)
        msg = dev._build_commit_message()
        self.assertEqual(msg, 'A: new.txt')

    def test_modified_files(self):
        """Modified files should show M: prefix"""
        Path(self.temp_dir, 'file.txt').write_text('v1')
        subprocess.run(['git', 'add', 'file.txt'], cwd=self.temp_dir, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'init'], cwd=self.temp_dir, capture_output=True)
        Path(self.temp_dir, 'file.txt').write_text('v2')
        subprocess.run(['git', 'add', 'file.txt'], cwd=self.temp_dir, capture_output=True)
        msg = dev._build_commit_message()
        self.assertEqual(msg, 'M: file.txt')

    def test_deleted_files(self):
        """Deleted files should show D: prefix"""
        Path(self.temp_dir, 'file.txt').write_text('content')
        subprocess.run(['git', 'add', 'file.txt'], cwd=self.temp_dir, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'init'], cwd=self.temp_dir, capture_output=True)
        os.remove(Path(self.temp_dir, 'file.txt'))
        subprocess.run(['git', 'add', 'file.txt'], cwd=self.temp_dir, capture_output=True)
        msg = dev._build_commit_message()
        self.assertEqual(msg, 'D: file.txt')

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


class TestSyncTrackedFiles(unittest.TestCase):
    """Test timestamp-based bidirectional file sync"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_config_file = dev.CONFIG_FILE
        self.orig_rcfiles_dir = dev.RCFILES_DIR
        self.orig_home_dir = dev.HOME_DIR
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.CONFIG_DIR.mkdir(parents=True)
        dev.CONFIG_FILE = dev.CONFIG_DIR / 'repos.json'
        dev.RCFILES_DIR = dev.CONFIG_DIR / 'rcfiles'

        self.home = Path(self.temp_dir) / 'home'
        self.home.mkdir()
        dev.HOME_DIR = self.home

        dev.save_config({'repos': [], 'files': []})

        self.old_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.new_time = datetime(2026, 3, 1, tzinfo=timezone.utc)

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.CONFIG_FILE = self.orig_config_file
        dev.RCFILES_DIR = self.orig_rcfiles_dir
        dev.HOME_DIR = self.orig_home_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _set_mtime(self, path, dt):
        ts = dt.timestamp()
        os.utime(str(path), (ts, ts))

    def _setup_rcfile(self, rel_path, content):
        rcfile = dev.RCFILES_DIR / rel_path
        rcfile.parent.mkdir(parents=True, exist_ok=True)
        rcfile.write_text(content)
        return rcfile

    def _setup_home_file(self, rel_path, content, mtime=None):
        home_file = self.home / rel_path.replace('/', os.sep)
        home_file.parent.mkdir(parents=True, exist_ok=True)
        home_file.write_text(content)
        if mtime:
            self._set_mtime(home_file, mtime)
        return home_file

    # --- Timestamp-based direction tests ---

    def _track_file(self, path):
        """Add a file to the tracked files config."""
        config = dev.load_config()
        files = config.get('files', [])
        if not any(f['path'] == path for f in files):
            files.append({'path': path})
        config['files'] = files
        dev.save_config(config)

    @patch('dev.get_rcfile_git_timestamp')
    def test_local_newer_overwrites_remote(self, mock_ts):
        """When home file mtime > rcfile git timestamp, local wins."""
        mock_ts.return_value = self.old_time
        self._track_file('myconfig.md')
        self._setup_rcfile('myconfig.md', 'old remote')
        self._setup_home_file('myconfig.md', 'new local', self.new_time)

        result = dev.sync_tracked_files(self.home)

        self.assertTrue(result)
        rcfile = dev.RCFILES_DIR / 'myconfig.md'
        self.assertEqual(rcfile.read_text(), 'new local')

    @patch('dev.get_rcfile_git_timestamp')
    def test_remote_newer_overwrites_local(self, mock_ts):
        """When rcfile git timestamp > home file mtime, remote wins."""
        mock_ts.return_value = self.new_time
        self._track_file('myconfig.md')
        self._setup_rcfile('myconfig.md', 'new remote')
        self._setup_home_file('myconfig.md', 'old local', self.old_time)

        result = dev.sync_tracked_files(self.home)

        self.assertFalse(result)
        home_file = self.home / 'myconfig.md'
        self.assertEqual(home_file.read_text(), 'new remote')

    @patch('dev.get_rcfile_git_timestamp')
    def test_identical_content_skipped(self, mock_ts):
        """Same content should not trigger any copy."""
        mock_ts.return_value = self.old_time
        self._track_file('myconfig.md')
        content = '# Same content'
        self._setup_rcfile('myconfig.md', content)
        self._setup_home_file('myconfig.md', content)

        result = dev.sync_tracked_files(self.home)

        self.assertFalse(result)

    @patch('dev.get_rcfile_git_timestamp')
    def test_only_home_copies_to_rcfiles(self, mock_ts):
        """File only in home should be copied to rcfiles."""
        mock_ts.return_value = None
        self._track_file('myconfig.md')
        self._setup_home_file('myconfig.md', 'local only', self.new_time)

        result = dev.sync_tracked_files(self.home)

        self.assertTrue(result)
        rcfile = dev.RCFILES_DIR / 'myconfig.md'
        self.assertTrue(rcfile.exists())
        self.assertEqual(rcfile.read_text(), 'local only')

    @patch('dev.get_rcfile_git_timestamp')
    def test_only_rcfiles_copies_to_home(self, mock_ts):
        """File only in rcfiles should be copied to home."""
        mock_ts.return_value = self.new_time
        self._track_file('myconfig.md')
        self._setup_rcfile('myconfig.md', 'remote only')

        result = dev.sync_tracked_files(self.home)

        self.assertFalse(result)
        home_file = self.home / 'myconfig.md'
        self.assertTrue(home_file.exists())
        self.assertEqual(home_file.read_text(), 'remote only')

    @patch('dev.get_rcfile_git_timestamp')
    def test_no_git_timestamp_local_wins(self, mock_ts):
        """No git timestamp (never committed) should default to local wins."""
        mock_ts.return_value = None
        self._track_file('myconfig.md')
        self._setup_rcfile('myconfig.md', 'remote')
        self._setup_home_file('myconfig.md', 'local', self.new_time)

        result = dev.sync_tracked_files(self.home)

        self.assertTrue(result)
        rcfile = dev.RCFILES_DIR / 'myconfig.md'
        self.assertEqual(rcfile.read_text(), 'local')

    # --- Home mtime alignment tests ---

    @patch('dev.get_rcfile_git_timestamp')
    def test_home_mtime_aligned_after_remote_wins(self, mock_ts):
        """After remote wins, home file mtime should match remote timestamp."""
        mock_ts.return_value = self.new_time
        self._track_file('myconfig.md')
        self._setup_rcfile('myconfig.md', 'new remote')
        self._setup_home_file('myconfig.md', 'old local', self.old_time)

        dev.sync_tracked_files(self.home)

        home_file = self.home / 'myconfig.md'
        home_mtime = datetime.fromtimestamp(home_file.stat().st_mtime, tz=timezone.utc)
        self.assertAlmostEqual(home_mtime.timestamp(), self.new_time.timestamp(), delta=2)

    @patch('dev.get_rcfile_git_timestamp')
    def test_home_mtime_aligned_when_identical(self, mock_ts):
        """When content is identical, home file mtime should align to remote timestamp."""
        mock_ts.return_value = self.new_time
        self._track_file('myconfig.md')
        content = '# Same content'
        self._setup_rcfile('myconfig.md', content)
        self._setup_home_file('myconfig.md', content, self.old_time)

        dev.sync_tracked_files(self.home)

        home_file = self.home / 'myconfig.md'
        home_mtime = datetime.fromtimestamp(home_file.stat().st_mtime, tz=timezone.utc)
        self.assertAlmostEqual(home_mtime.timestamp(), self.new_time.timestamp(), delta=2)

    # --- Conflict marker tests ---

    @patch('dev.get_rcfile_git_timestamp')
    def test_home_md_conflict_markers_skipped(self, mock_ts):
        """Home .md with conflict markers should be skipped."""
        mock_ts.return_value = self.old_time
        self._track_file('myconfig.md')
        conflict = "## Section\n<<<<<<< HEAD\nA\n=======\nB\n>>>>>>> branch\n"
        self._setup_rcfile('myconfig.md', 'original')
        self._setup_home_file('myconfig.md', conflict, self.new_time)

        result = dev.sync_tracked_files(self.home)

        self.assertFalse(result)
        rcfile = dev.RCFILES_DIR / 'myconfig.md'
        self.assertEqual(rcfile.read_text(), 'original')

    @patch('dev.get_rcfile_git_timestamp')
    def test_rcfile_md_conflict_markers_skipped(self, mock_ts):
        """Rcfile .md with conflict markers should be skipped."""
        mock_ts.return_value = self.new_time
        self._track_file('myconfig.md')
        conflict = "## Section\n<<<<<<< HEAD\nA\n=======\nB\n>>>>>>> branch\n"
        self._setup_rcfile('myconfig.md', conflict)
        self._setup_home_file('myconfig.md', 'original', self.old_time)

        result = dev.sync_tracked_files(self.home)

        self.assertFalse(result)
        home_file = self.home / 'myconfig.md'
        self.assertEqual(home_file.read_text(), 'original')

    @patch('dev.get_rcfile_git_timestamp')
    def test_non_md_files_skip_conflict_check(self, mock_ts):
        """Non-.md files with conflict-like content should still sync."""
        mock_ts.return_value = self.old_time
        self._track_file('platform/.gclient')
        conflict = "<<<<<<< HEAD\nstuff\n=======\nother\n>>>>>>> branch\n"
        self._setup_rcfile('platform/.gclient', 'old')
        platform_dir = self.home / 'platform'
        platform_dir.mkdir(exist_ok=True)
        home_file = platform_dir / '.gclient'
        home_file.write_text(conflict)
        self._set_mtime(home_file, self.new_time)

        result = dev.sync_tracked_files(self.home)

        self.assertTrue(result)
        rcfile = dev.RCFILES_DIR / 'platform' / '.gclient'
        self.assertEqual(rcfile.read_text(), conflict)

    # --- Parent directory creation tests ---

    @patch('dev.get_rcfile_git_timestamp')
    def test_creates_parent_dirs_for_home(self, mock_ts):
        """Remote -> home should create parent directories."""
        mock_ts.return_value = self.new_time
        self._track_file('subdir/config.md')
        self._setup_rcfile('subdir/config.md', 'remote content')

        dev.sync_tracked_files(self.home)

        home_file = self.home / 'subdir' / 'config.md'
        self.assertTrue(home_file.exists())
        self.assertEqual(home_file.read_text(), 'remote content')

    @patch('dev.get_rcfile_git_timestamp')
    def test_creates_parent_dirs_for_rcfiles(self, mock_ts):
        """Home -> rcfiles should create parent directories."""
        mock_ts.return_value = None
        self._track_file('subdir/config.md')
        self._setup_home_file('subdir/config.md', 'local content', self.new_time)

        result = dev.sync_tracked_files(self.home)

        self.assertTrue(result)
        rcfile = dev.RCFILES_DIR / 'subdir' / 'config.md'
        self.assertTrue(rcfile.exists())

    # --- User file tests ---

    def test_no_builtin_files(self):
        all_files = dev._get_all_tracked_files()
        self.assertEqual(all_files, [])

    def test_user_files_tracked(self):
        self._track_file('platform/.gclient')
        all_files = dev._get_all_tracked_files()
        paths = [f['path'] for f in all_files]
        self.assertIn('platform/.gclient', paths)

    @patch('dev.get_rcfile_git_timestamp')
    def test_user_file_local_newer(self, mock_ts):
        """User-added files should follow the same timestamp logic."""
        mock_ts.return_value = self.old_time
        self._track_file('platform/.gclient')
        self._setup_rcfile('platform/.gclient', 'old remote')
        platform_dir = self.home / 'platform'
        platform_dir.mkdir(exist_ok=True)
        home_file = platform_dir / '.gclient'
        home_file.write_text('new local')
        self._set_mtime(home_file, self.new_time)

        result = dev.sync_tracked_files(self.home)

        self.assertTrue(result)
        rcfile = dev.RCFILES_DIR / 'platform' / '.gclient'
        self.assertEqual(rcfile.read_text(), 'new local')

    @patch('dev.get_rcfile_git_timestamp')
    def test_user_file_remote_newer(self, mock_ts):
        """User-added files should follow the same timestamp logic."""
        mock_ts.return_value = self.new_time
        dev.save_config({
            'repos': [],
            'files': [{'path': 'platform/.gclient'}]
        })
        self._setup_rcfile('platform/.gclient', 'new remote')
        platform_dir = self.home / 'platform'
        platform_dir.mkdir(exist_ok=True)
        home_file = platform_dir / '.gclient'
        home_file.write_text('old local')
        self._set_mtime(home_file, self.old_time)

        result = dev.sync_tracked_files(self.home)

        self.assertFalse(result)
        self.assertEqual(home_file.read_text(), 'new remote')

    @patch('dev.get_rcfile_git_timestamp')
    def test_missing_file_skipped(self, mock_ts):
        """Non-existent file with no rcfile should be skipped."""
        mock_ts.return_value = None
        dev.save_config({
            'repos': [],
            'files': [{'path': 'nonexistent.txt'}]
        })
        result = dev.sync_tracked_files(self.home)
        self.assertFalse(result)



class TestAddTrackedFile(unittest.TestCase):
    """Test _add_tracked_file"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_config_file = dev.CONFIG_FILE
        self.orig_rcfiles_dir = dev.RCFILES_DIR
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.CONFIG_DIR.mkdir(parents=True)
        dev.CONFIG_FILE = dev.CONFIG_DIR / 'repos.json'
        dev.RCFILES_DIR = dev.CONFIG_DIR / 'rcfiles'

        self.workspace = Path(self.temp_dir) / 'workspace'
        self.workspace.mkdir()

        dev.save_config({'repos': [],
                         'workspaceRoots': {'test': str(self.workspace)}})

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.CONFIG_FILE = self.orig_config_file
        dev.RCFILES_DIR = self.orig_rcfiles_dir
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.dict(os.environ, {'DEVCONFIG': 'test'})
    def test_add_tracked_file(self):
        """Adding a file should store it in config and rcfiles"""

        platform_dir = self.workspace / 'platform'
        platform_dir.mkdir()
        gclient = platform_dir / '.gclient'
        gclient.write_text('solutions = [{"name": "src"}]')

        result = dev._add_tracked_file(gclient)

        self.assertEqual(result, 0)
        config = dev.load_config()
        self.assertEqual(len(config.get('files', [])), 1)
        self.assertEqual(config['files'][0]['path'], 'platform/.gclient')

        rcfile = dev.RCFILES_DIR / 'platform' / '.gclient'
        self.assertTrue(rcfile.exists())

    @patch.dict(os.environ, {'DEVCONFIG': 'test'})
    def test_add_file_replaces_existing(self):
        """Adding same file again should replace the entry"""

        platform_dir = self.workspace / 'platform'
        platform_dir.mkdir()
        gclient = platform_dir / '.gclient'
        gclient.write_text('v1')

        dev._add_tracked_file(gclient)
        gclient.write_text('v2')
        dev._add_tracked_file(gclient)

        config = dev.load_config()
        self.assertEqual(len(config.get('files', [])), 1)
        rcfile = dev.RCFILES_DIR / 'platform' / '.gclient'
        self.assertEqual(rcfile.read_text(), 'v2')

    @patch.dict(os.environ, {'DEVCONFIG': 'test'})
    def test_add_file_outside_workspace_fails(self):
        """Adding a file outside workspace should fail"""

        outside = Path(self.temp_dir) / 'outside.txt'
        outside.write_text('test')

        result = dev._add_tracked_file(outside)
        self.assertEqual(result, 1)


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
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
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
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        args = argparse.Namespace(git_args=['pull', '--rebase'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 0)
        call_args = mock_run.call_args[0][0]
        self.assertIn('pull', call_args)
        self.assertIn('--rebase', call_args)

    @patch('subprocess.run')
    @patch('dev.get_ado_token', return_value='test-bearer-token')
    def test_clone_with_url(self, mock_token, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        args = argparse.Namespace(git_args=['clone', 'https://dev.azure.com/org/proj/_git/repo'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 0)
        call_args = mock_run.call_args[0][0]
        self.assertIn('clone', call_args)
        self.assertIn('https://dev.azure.com/org/proj/_git/repo', call_args)


class TestParseAdoRemote(unittest.TestCase):

    def test_devazure_with_user_prefix(self):
        result = dev._parse_ado_remote('https://contoso@dev.azure.com/contoso/platform/_git/internal.service')
        self.assertEqual(result, ('contoso', 'platform', 'internal.service'))

    def test_devazure_without_user_prefix(self):
        result = dev._parse_ado_remote('https://dev.azure.com/contoso/platform/_git/internal.service.dashboard')
        self.assertEqual(result, ('contoso', 'platform', 'internal.service.dashboard'))

    def test_visualstudio_format(self):
        result = dev._parse_ado_remote('https://contoso.visualstudio.com/DefaultCollection/platform/_git/bigrepo.toolchain_tools')
        self.assertEqual(result, ('contoso', 'platform', 'bigrepo.toolchain_tools'))

    def test_github_url_returns_none(self):
        result = dev._parse_ado_remote('git@github.com-work:acme-corp/platform-agents.git')
        self.assertIsNone(result)

    def test_with_dot_git_suffix(self):
        result = dev._parse_ado_remote('https://dev.azure.com/org/proj/_git/repo.git')
        self.assertEqual(result, ('org', 'proj', 'repo'))


class TestCmdInit(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.home = self.tmpdir / 'home'
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    @patch.dict(os.environ, {'SHELL': '/bin/zsh'})
    @patch('dev.get_os_type', return_value='linux')
    def test_unix_zsh_already_default(self, mock_os):
        self.assertEqual(dev._init_unix(), 0)

    @patch.dict(os.environ, {'SHELL': '/bin/bash'})
    @patch('dev.get_os_type', return_value='linux')
    @patch('shutil.which', return_value='/usr/bin/zsh')
    def test_unix_creates_bashrc(self, mock_which, mock_os):
        bashrc = self.home / '.bashrc'
        with patch('dev.Path.home', return_value=self.home):
            self.assertEqual(dev._init_unix(), 0)
        self.assertIn('exec zsh', bashrc.read_text())

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
        self.assertIn('exec zsh', content)

    @patch.dict(os.environ, {'SHELL': '/bin/bash'})
    @patch('dev.get_os_type', return_value='linux')
    @patch('shutil.which', return_value='/usr/bin/zsh')
    def test_unix_bashrc_idempotent(self, mock_which, mock_os):
        bashrc = self.home / '.bashrc'
        bashrc.write_text(f'{dev.BASHRC_SOURCE_LINE}\n')
        with patch('dev.Path.home', return_value=self.home):
            self.assertEqual(dev._init_unix(), 0)
        self.assertEqual(bashrc.read_text().count('exec zsh'), 1)

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


class TestSelfUpdate(unittest.TestCase):

    @patch('dev.run_git')
    def test_no_update_when_hash_unchanged(self, mock_git):
        mock_git.return_value = (True, 'abc123')
        dev._self_update()
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
        dev._self_update()

    @patch('subprocess.run')
    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.run_git')
    def test_reexecs_when_hash_changes(self, mock_git, mock_branch, mock_subprocess):
        call_count = [0]
        def side_effect(path, *args):
            if args[0] == 'rev-parse':
                call_count[0] += 1
                return (True, 'old' if call_count[0] == 1 else 'new')
            if args[0] == 'rev-list':
                return (True, '0\t1')
            return (True, '')
        mock_git.side_effect = side_effect
        mock_subprocess.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        with self.assertRaises(SystemExit) as ctx:
            dev._self_update()
        self.assertEqual(ctx.exception.code, 0)
        mock_subprocess.assert_called_once()

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
        with self.assertRaises(SystemExit):
            dev._self_update()
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
            dev._self_update()
        self.assertNotIn('_DEV_PULLED_RCFILES', os.environ)


class TestSyncRcfilesPull(unittest.TestCase):
    """Test that cmd_repo_sync shows pulled commits."""

    @patch('dev.sync_tracked_files')
    @patch('dev.sync_rcfiles_push')
    @patch('dev._self_update')
    @patch('dev.load_config', return_value={'repos': [], 'files': []})
    @patch('dev.get_base_path', return_value='/tmp/dev')
    def test_shows_pulled_commits(self, mock_base, mock_config,
                                   mock_update, mock_push, mock_sync):
        """When _DEV_PULLED_RCFILES is set, show pulled commits."""
        os.environ['_DEV_PULLED_RCFILES'] = 'abc1234 Update dev.py'
        from io import StringIO
        with patch('sys.stdout', new_callable=StringIO) as mock_out:
            dev.cmd_repo_sync(argparse.Namespace())
        output = mock_out.getvalue()
        self.assertIn('rcfiles updated from remote', output)
        self.assertIn('Update dev.py', output)
        mock_push.assert_called_once_with(pulled=True)
        os.environ.pop('_DEV_PULLED_RCFILES', None)

    @patch('dev.sync_tracked_files')
    @patch('dev.sync_rcfiles_push')
    @patch('dev._self_update')
    @patch('dev.load_config', return_value={'repos': [], 'files': []})
    @patch('dev.get_base_path', return_value='/tmp/dev')
    def test_no_env_var_shows_nothing(self, mock_base, mock_config,
                                      mock_update, mock_push, mock_sync):
        """Without _DEV_PULLED_RCFILES, no pull message shown."""
        os.environ.pop('_DEV_PULLED_RCFILES', None)
        from io import StringIO
        with patch('sys.stdout', new_callable=StringIO) as mock_out:
            dev.cmd_repo_sync(argparse.Namespace())
        output = mock_out.getvalue()
        self.assertNotIn('rcfiles updated from remote', output)
        mock_push.assert_called_once_with(pulled=False)


class TestSyncRcfilesPushPulled(unittest.TestCase):
    """Test that sync_rcfiles_push suppresses 'up to date' when pulled."""

    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.run_git')
    def test_up_to_date_shown_when_not_pulled(self, mock_git, mock_branch):
        mock_git.return_value = (True, '0\t0')
        from io import StringIO
        with patch('sys.stdout', new_callable=StringIO) as mock_out:
            dev.sync_rcfiles_push(pulled=False)
        self.assertIn('rcfiles up to date', mock_out.getvalue())

    @patch('dev.get_default_branch', return_value='main')
    @patch('dev.run_git')
    def test_up_to_date_hidden_when_pulled(self, mock_git, mock_branch):
        mock_git.return_value = (True, '0\t0')
        from io import StringIO
        with patch('sys.stdout', new_callable=StringIO) as mock_out:
            dev.sync_rcfiles_push(pulled=True)
        self.assertNotIn('rcfiles up to date', mock_out.getvalue())


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

        self.assertTrue(link_path.is_symlink())
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
        link_path.symlink_to(old_target, target_is_directory=True)

        dev._ensure_link(link_path, new_target)

        self.assertTrue(link_path.is_symlink())
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
        link_path.symlink_to(repo_path, target_is_directory=True)

        dev._ensure_link(link_path, repo_path)

        self.assertTrue(link_path.is_symlink())
        self.assertEqual(link_path.resolve(), repo_path.resolve())


class TestToolsInstalled(unittest.TestCase):
    """Verify all expected tools are installed on the current platform"""

    WINDOWS_TOOLS = ['py', 'git', 'clang', 'choco', 'zoxide', 'fzf']
    WSL_TOOLS = ['zsh', 'python3', 'git', 'zoxide', 'fzf']
    WORK_TOOLS = ['agency']

    @unittest.skipUnless(platform.system() == 'Windows', 'Windows only')
    def test_windows_tools(self):
        tools = self.WINDOWS_TOOLS + (self.WORK_TOOLS if os.environ.get('WORK') == 'MSFT' else [])
        missing = [t for t in tools if shutil.which(t) is None]
        self.assertEqual(missing, [], f'Missing Windows tools: {missing}')

    @unittest.skipUnless(platform.system() == 'Windows', 'Windows only')
    def test_wsl_tools(self):
        if shutil.which('wsl') is None:
            self.skipTest('WSL not available')
        try:
            probe = subprocess.run(
                ['wsl', '-e', 'true'],
                capture_output=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            self.skipTest(f'WSL not usable: {e}')
        if probe.returncode != 0:
            self.skipTest('No WSL distro installed/running')
        check = ' && '.join(f'command -v {t}' for t in self.WSL_TOOLS)
        result = subprocess.run(
            ['wsl', '-e', 'zsh', '-ilc', check],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            missing = []
            for t in self.WSL_TOOLS:
                r = subprocess.run(
                    ['wsl', '-e', 'zsh', '-ilc', f'command -v {t}'],
                    capture_output=True, text=True, timeout=15,
                )
                if r.returncode != 0:
                    missing.append(t)
            self.assertEqual(missing, [], f'Missing WSL tools: {missing}')

    @unittest.skipUnless(platform.system() == 'Linux', 'Linux only')
    def test_linux_tools(self):
        missing = [t for t in self.WSL_TOOLS if shutil.which(t) is None]
        self.assertEqual(missing, [], f'Missing Linux tools: {missing}')


class TestNormalizeUrlForComparison(unittest.TestCase):

    def test_ado_with_credentials_matches_without(self):
        url1 = 'https://AzToken123@dev.azure.com/contoso/platform/_git/bigrepo.infra.build'
        url2 = 'https://contoso@dev.azure.com/contoso/platform/_git/bigrepo.infra.build'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_ado_without_credentials_matches_config(self):
        url1 = 'https://dev.azure.com/contoso/platform/_git/internal.service'
        url2 = 'https://contoso@dev.azure.com/contoso/platform/_git/internal.service'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_github_ssh_alias_matches_plain(self):
        url1 = 'git@github.com-personal:example-user/rcfiles.git'
        url2 = 'git@github.com:example-user/rcfiles.git'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_github_edge_alias_matches_plain(self):
        url1 = 'git@github.com-work:acme-corp/platform-agents.git'
        url2 = 'git@github.com:acme-corp/platform-agents.git'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_trailing_dot_git_ignored(self):
        url1 = 'https://dev.azure.com/contoso/platform/_git/repo.git'
        url2 = 'https://dev.azure.com/contoso/platform/_git/repo'
        self.assertEqual(dev._normalize_url_for_comparison(url1),
                         dev._normalize_url_for_comparison(url2))

    def test_different_repos_do_not_match(self):
        url1 = 'https://dev.azure.com/contoso/platform/_git/repo-a'
        url2 = 'https://dev.azure.com/contoso/platform/_git/repo-b'
        self.assertNotEqual(dev._normalize_url_for_comparison(url1),
                            dev._normalize_url_for_comparison(url2))

    def test_sync_skips_non_ado_repos(self):
        """_parse_ado_remote returns None for non-ADO URLs, so sync won't fix them."""
        github_url = 'git@github.com-personal:example-user/rcfiles.git'
        self.assertIsNone(dev._parse_ado_remote(github_url))


class TestCmdPrDesc(unittest.TestCase):

    def _make_args(self, **kwargs):
        defaults = dict(description=None, repo=None, branch=None, id=None)
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    @patch('dev.get_ado_token', return_value='fake-token')
    @patch('dev._resolve_pr_context')
    def test_no_input_reads_description(self, mock_ctx, mock_token):
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'main', 42, 'az', None)
        pr_json = json.dumps({'description': 'existing desc'}).encode()
        mock_resp = type('R', (), {'read': lambda self: pr_json, '__enter__': lambda self: self, '__exit__': lambda *a: None})()
        with patch('urllib.request.urlopen', return_value=mock_resp):
            rc = dev.cmd_pr_desc(self._make_args())
        self.assertEqual(rc, 0)

    @patch('dev._resolve_pr_context')
    def test_no_pr_found_returns_error(self, mock_ctx):
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'main', None, 'az', None)
        rc = dev.cmd_pr_desc(self._make_args())
        self.assertEqual(rc, 1)

    @patch('dev._resolve_pr_context')
    def test_empty_description_reads_current(self, mock_ctx):
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'main', 42, 'az', None)
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
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'main', 42, 'az', None)
        mock_run.return_value = type('R', (), {'returncode': 0})()
        rc = dev.cmd_pr_desc(self._make_args(description=['new desc']))
        self.assertEqual(rc, 0)
        mock_print.assert_called_once()

    @patch('dev._print_pr_description', return_value=0)
    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_multiline_description_updates(self, mock_ctx, mock_run, mock_print):
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'main', 42, 'az', None)
        mock_run.return_value = type('R', (), {'returncode': 0})()
        rc = dev.cmd_pr_desc(self._make_args(description=['# Title', 'Body', '', '- item']))
        self.assertEqual(rc, 0)
        mock_print.assert_called_once()


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
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'feat', 42, 'az', git_fn)
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
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'feat', None, 'az', git_fn)
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='origin/main\n', stderr='')
        rc = dev.cmd_pr_diff(self._make_args())
        self.assertEqual(rc, 0)

    @patch('subprocess.run')
    @patch('dev._resolve_pr_context')
    def test_id_mode_uses_remote_refs(self, mock_ctx, mock_run):
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'feat', None, 'az', None)
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
        mock_ctx.return_value = ('contoso', 'platform', 'repo', 'feat', None, 'az', git_fn)
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='origin/main\n', stderr='')
        rc = dev.cmd_pr_diff(self._make_args(diff_args=['--', '--stat']))
        self.assertEqual(rc, 0)
        diff_call = [c for c in mock_run.call_args_list if 'diff' in c[0][0]]
        self.assertTrue(len(diff_call) > 0)
        self.assertIn('--stat', diff_call[-1][0][0])


if __name__ == '__main__':
    unittest.main(verbosity=2, buffer=True)
