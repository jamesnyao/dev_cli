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


class TestRunGit(unittest.TestCase):
    
    def test_run_git_on_invalid_path(self):
        success, output = dev.run_git('/nonexistent_path_for_test', 'status')
        self.assertFalse(success)


class TestTrackedFilesSync(unittest.TestCase):
    """Test unified tracked file sync (built-in md files + user files)"""

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
        (self.workspace / '.github').mkdir()
        (self.workspace / '.claude').mkdir()

        dev.save_config({'version': 1, 'repos': [], 'files': []})

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.CONFIG_FILE = self.orig_config_file
        dev.RCFILES_DIR = self.orig_rcfiles_dir
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --- Built-in copilot-instructions.md tests ---

    def test_copilot_workspace_changes_sync_to_rcfiles(self):
        initial = "# Copilot\n\nInitial rules\n"
        rcfile = dev.RCFILES_DIR / '.github' / 'copilot-instructions.md'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text(initial)
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(initial)

        updated = "# Copilot\n\nInitial rules\n\n## ADO PAT\nUsed for PRs.\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(updated)

        dev.merge_tracked_files_to_repoconfig(self.workspace)

        self.assertEqual(rcfile.read_text(), updated)

    def test_copilot_rcfiles_changes_sync_to_workspace(self):
        initial = "# Copilot\n\nInitial rules\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(initial)

        updated = "# Copilot\n\nInitial rules\n\n## New Section\nFrom remote.\n"
        rcfile = dev.RCFILES_DIR / '.github' / 'copilot-instructions.md'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text(updated)

        dev.apply_tracked_files_to_workspace(self.workspace)

        result = (self.workspace / '.github' / 'copilot-instructions.md').read_text()
        self.assertIn("New Section", result)

    def test_copilot_merge_skips_conflicts(self):
        conflict = "## Section\n<<<<<<< HEAD\nA\n=======\nB\n>>>>>>> branch\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(conflict)
        rcfile = dev.RCFILES_DIR / '.github' / 'copilot-instructions.md'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text("original")

        dev.merge_tracked_files_to_repoconfig(self.workspace)

        self.assertEqual(rcfile.read_text(), "original")

    def test_copilot_apply_skips_conflicts(self):
        rcfile = dev.RCFILES_DIR / '.github' / 'copilot-instructions.md'
        rcfile.parent.mkdir(parents=True)
        conflict = "## Section\n<<<<<<< HEAD\nA\n=======\nB\n>>>>>>> branch\n"
        rcfile.write_text(conflict)
        (self.workspace / '.github' / 'copilot-instructions.md').write_text("original")

        dev.apply_tracked_files_to_workspace(self.workspace)

        result = (self.workspace / '.github' / 'copilot-instructions.md').read_text()
        self.assertEqual(result, "original")

    def test_copilot_apply_creates_parent_dir(self):
        import shutil
        shutil.rmtree(self.workspace / '.github')

        rcfile = dev.RCFILES_DIR / '.github' / 'copilot-instructions.md'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text("# Copilot\n\nContent\n")

        dev.apply_tracked_files_to_workspace(self.workspace)

        workspace_file = self.workspace / '.github' / 'copilot-instructions.md'
        self.assertTrue(workspace_file.exists())
        self.assertEqual(workspace_file.read_text(), "# Copilot\n\nContent\n")

    # --- Built-in CLAUDE.md tests ---

    def test_claude_workspace_changes_sync_to_rcfiles(self):
        initial = "# Dev CLI\n\nInitial content\n"
        rcfile = dev.RCFILES_DIR / '.claude' / 'CLAUDE.md'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text(initial)
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(initial)

        updated = "# Dev CLI\n\nInitial content\n\n## New Section\nNew content\n"
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(updated)

        dev.merge_tracked_files_to_repoconfig(self.workspace)

        self.assertIn("New Section", rcfile.read_text())

    def test_claude_rcfiles_changes_sync_to_workspace(self):
        initial = "# Dev CLI\n\nInitial content\n"
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(initial)

        updated = "# Dev CLI\n\nInitial content\n\n## Remote Section\nFrom another machine.\n"
        rcfile = dev.RCFILES_DIR / '.claude' / 'CLAUDE.md'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text(updated)

        dev.apply_tracked_files_to_workspace(self.workspace)

        result = (self.workspace / '.claude' / 'CLAUDE.md').read_text()
        self.assertIn("Remote Section", result)

    def test_claude_merge_skips_conflicts(self):
        conflict = "## Section\n<<<<<<< HEAD\nA\n=======\nB\n>>>>>>> branch\n"
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(conflict)
        rcfile = dev.RCFILES_DIR / '.claude' / 'CLAUDE.md'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text("original")

        dev.merge_tracked_files_to_repoconfig(self.workspace)

        self.assertEqual(rcfile.read_text(), "original")

    # --- User-added file tests ---

    def test_user_file_merge_to_rcfiles(self):
        dev.save_config({
            'version': 1, 'repos': [],
            'files': [{'path': 'platform/.gclient', 'addedAt': '2026-01-01T00:00:00+00:00'}]
        })

        platform_dir = self.workspace / 'platform'
        platform_dir.mkdir()
        (platform_dir / '.gclient').write_text('solutions = []')

        result = dev.merge_tracked_files_to_repoconfig(self.workspace)

        self.assertTrue(result)
        rcfile = dev.RCFILES_DIR / 'platform' / '.gclient'
        self.assertTrue(rcfile.exists())
        self.assertEqual(rcfile.read_text(), 'solutions = []')

    def test_user_file_apply_to_workspace(self):
        dev.save_config({
            'version': 1, 'repos': [],
            'files': [{'path': 'platform/.gclient', 'addedAt': '2026-01-01T00:00:00+00:00'}]
        })

        rcfile = dev.RCFILES_DIR / 'platform' / '.gclient'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text('solutions = []')

        result = dev.apply_tracked_files_to_workspace(self.workspace)

        self.assertTrue(result)
        workspace_file = self.workspace / 'platform' / '.gclient'
        self.assertTrue(workspace_file.exists())
        self.assertEqual(workspace_file.read_text(), 'solutions = []')

    def test_no_change_when_identical(self):
        content = "# Copilot\n\n## Rules\nSome rules\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(content)
        rcfile = dev.RCFILES_DIR / '.github' / 'copilot-instructions.md'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text(content)

        self.assertFalse(dev.merge_tracked_files_to_repoconfig(self.workspace))
        self.assertFalse(dev.apply_tracked_files_to_workspace(self.workspace))

    def test_merge_skips_missing_workspace_file(self):
        dev.save_config({
            'version': 1, 'repos': [],
            'files': [{'path': 'nonexistent.txt', 'addedAt': '2026-01-01T00:00:00+00:00'}]
        })
        result = dev.merge_tracked_files_to_repoconfig(self.workspace)
        self.assertFalse(result)

    def test_apply_skips_missing_rcfile(self):
        dev.save_config({
            'version': 1, 'repos': [],
            'files': [{'path': 'nonexistent.txt', 'addedAt': '2026-01-01T00:00:00+00:00'}]
        })
        result = dev.apply_tracked_files_to_workspace(self.workspace)
        self.assertFalse(result)

    def test_builtin_files_always_included(self):
        all_files = dev._get_all_tracked_files()
        paths = [f['path'] for f in all_files]
        self.assertIn('.github/copilot-instructions.md', paths)
        self.assertIn('.claude/CLAUDE.md', paths)

    def test_user_files_combined_with_builtins(self):
        dev.save_config({
            'version': 1, 'repos': [],
            'files': [{'path': 'platform/.gclient', 'addedAt': '2026-01-01T00:00:00+00:00'}]
        })
        all_files = dev._get_all_tracked_files()
        paths = [f['path'] for f in all_files]
        self.assertIn('.github/copilot-instructions.md', paths)
        self.assertIn('.claude/CLAUDE.md', paths)
        self.assertIn('platform/.gclient', paths)

    def test_no_conflict_check_on_non_md_files(self):
        """Non-md files with conflict-like content should still sync"""
        dev.save_config({
            'version': 1, 'repos': [],
            'files': [{'path': 'platform/.gclient', 'addedAt': '2026-01-01T00:00:00+00:00'}]
        })
        platform_dir = self.workspace / 'platform'
        platform_dir.mkdir()
        content = "<<<<<<< HEAD\nstuff\n=======\nother\n>>>>>>> branch\n"
        (platform_dir / '.gclient').write_text(content)

        rcfile = dev.RCFILES_DIR / 'platform' / '.gclient'
        rcfile.parent.mkdir(parents=True)
        rcfile.write_text("old")

        dev.merge_tracked_files_to_repoconfig(self.workspace)
        self.assertEqual(rcfile.read_text(), content)


class TestMigrateLegacyRcfiles(unittest.TestCase):
    """Test migration of legacy flat repoconfig files to rcfiles/"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_rcfiles_dir = dev.RCFILES_DIR
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.CONFIG_DIR.mkdir(parents=True)
        dev.RCFILES_DIR = dev.CONFIG_DIR / 'rcfiles'

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.RCFILES_DIR = self.orig_rcfiles_dir
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_migrates_copilot_instructions(self):
        old = dev.CONFIG_DIR / 'copilot-instructions.md'
        old.write_text("# Copilot instructions")

        dev._migrate_legacy_rcfiles()

        new = dev.RCFILES_DIR / '.github' / 'copilot-instructions.md'
        self.assertTrue(new.exists())
        self.assertEqual(new.read_text(), "# Copilot instructions")
        self.assertFalse(old.exists())

    def test_migrates_claude_md(self):
        old = dev.CONFIG_DIR / 'CLAUDE.md'
        old.write_text("# Claude rules")

        dev._migrate_legacy_rcfiles()

        new = dev.RCFILES_DIR / '.claude' / 'CLAUDE.md'
        self.assertTrue(new.exists())
        self.assertEqual(new.read_text(), "# Claude rules")
        self.assertFalse(old.exists())

    def test_skips_if_new_already_exists(self):
        old = dev.CONFIG_DIR / 'copilot-instructions.md'
        old.write_text("old content")

        new = dev.RCFILES_DIR / '.github' / 'copilot-instructions.md'
        new.parent.mkdir(parents=True)
        new.write_text("new content")

        dev._migrate_legacy_rcfiles()

        self.assertEqual(new.read_text(), "new content")

    def test_noop_when_no_legacy_files(self):
        dev._migrate_legacy_rcfiles()
        self.assertFalse((dev.RCFILES_DIR / '.github' / 'copilot-instructions.md').exists())
        self.assertFalse((dev.RCFILES_DIR / '.claude' / 'CLAUDE.md').exists())


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
        helper_arg = [a for a in call_args if a.startswith('credential.helper=/')]
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
