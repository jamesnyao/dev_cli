#!/usr/bin/env python3
"""
Lightweight unit tests for dev.py

Run with: python3 test_dev.py
Or: python3 -m pytest test_dev.py -v
"""

import json
import os
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
            'version': 1,
            'repos': [{'name': 'test-repo', 'remoteUrl': 'https://example.com/test.git'}]
        }
        dev.save_config(config)
        loaded = dev.load_config()
        self.assertEqual(loaded['repos'][0]['name'], 'test-repo')


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
    
    @patch.dict(os.environ, {'DEV': '/workspace'})
    def test_uses_dev_env(self):
        self.assertEqual(dev.get_base_path(), '/workspace')
    
    @patch.dict(os.environ, {'DEV': 'C:\\dev'})
    def test_windows_path(self):
        self.assertEqual(dev.get_base_path(), 'C:\\dev')
    
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_dev_raises(self):
        with self.assertRaises(ValueError):
            dev.get_base_path()


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

        dev.save_config({'version': 1, 'repos': [], 'files': []})

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
            files.append({'path': path, 'addedAt': '2026-01-01T00:00:00+00:00'})
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
            'version': 1, 'repos': [],
            'files': [{'path': 'platform/.gclient', 'addedAt': '2026-01-01T00:00:00+00:00'}]
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
            'version': 1, 'repos': [],
            'files': [{'path': 'nonexistent.txt', 'addedAt': '2026-01-01T00:00:00+00:00'}]
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

        dev.save_config({'version': 1, 'repos': []})

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.CONFIG_FILE = self.orig_config_file
        dev.RCFILES_DIR = self.orig_rcfiles_dir
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.dict(os.environ, {'DEV': ''})
    def test_add_tracked_file(self):
        """Adding a file should store it in config and rcfiles"""
        os.environ['DEV'] = str(self.workspace)

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

    @patch.dict(os.environ, {'DEV': ''})
    def test_add_file_replaces_existing(self):
        """Adding same file again should replace the entry"""
        os.environ['DEV'] = str(self.workspace)

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

    @patch.dict(os.environ, {'DEV': ''})
    def test_add_file_outside_workspace_fails(self):
        """Adding a file outside workspace should fail"""
        os.environ['DEV'] = str(self.workspace)

        outside = Path(self.temp_dir) / 'outside.txt'
        outside.write_text('test')

        result = dev._add_tracked_file(outside)
        self.assertEqual(result, 1)


class TestAdoGit(unittest.TestCase):
    """Test ado git command."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_pat_file = dev.ADO_PAT_FILE
        dev.ADO_PAT_FILE = Path(self.temp_dir) / 'ado_pat.txt'

    def tearDown(self):
        dev.ADO_PAT_FILE = self.orig_pat_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_pat_returns_error(self):
        args = argparse.Namespace(git_args=['pull'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 1)

    def test_no_git_args_returns_error(self):
        dev.ADO_PAT_FILE.write_text('test-pat')
        args = argparse.Namespace(git_args=[])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 1)

    def test_strips_leading_doubledash(self):
        dev.ADO_PAT_FILE.write_text('test-pat')
        args = argparse.Namespace(git_args=['--'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 1)

    @patch('subprocess.run')
    def test_runs_git_with_credential_helper(self, mock_run):
        dev.ADO_PAT_FILE.write_text('test-pat-value')
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        args = argparse.Namespace(git_args=['pull'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 0)
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], 'git')
        self.assertIn('credential.helper=', call_args)
        self.assertIn('credential.helper=store', call_args)
        self.assertIn('pull', call_args)
        call_env = mock_run.call_args[1]['env']
        self.assertEqual(call_env['ADO_PAT'], 'test-pat-value')

    @patch('subprocess.run')
    def test_cleans_up_temp_file(self, mock_run):
        dev.ADO_PAT_FILE.write_text('test-pat-value')
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        args = argparse.Namespace(git_args=['fetch'])
        dev.cmd_ado_git(args)
        call_args = mock_run.call_args[0][0]
        helper_arg = [a for a in call_args
                      if a.startswith('credential.helper=') and a != 'credential.helper='
                      and a != 'credential.helper=store']
        self.assertEqual(len(helper_arg), 1)
        helper_path = helper_arg[0].split('=', 1)[1]
        self.assertFalse(os.path.exists(helper_path))

    @patch('subprocess.run')
    def test_passes_extra_git_args(self, mock_run):
        dev.ADO_PAT_FILE.write_text('test-pat-value')
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        args = argparse.Namespace(git_args=['pull', '--rebase'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 0)
        call_args = mock_run.call_args[0][0]
        self.assertIn('pull', call_args)
        self.assertIn('--rebase', call_args)

    @patch('subprocess.run')
    def test_clone_with_url(self, mock_run):
        dev.ADO_PAT_FILE.write_text('test-pat-value')
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        args = argparse.Namespace(git_args=['clone', 'https://dev.azure.com/org/proj/_git/repo'])
        result = dev.cmd_ado_git(args)
        self.assertEqual(result, 0)
        call_args = mock_run.call_args[0][0]
        self.assertIn('clone', call_args)
        self.assertIn('https://dev.azure.com/org/proj/_git/repo', call_args)


if __name__ == '__main__':
    # Run with verbosity
    unittest.main(verbosity=2)
