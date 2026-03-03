#!/usr/bin/env python3
"""Tests for dev.py"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import dev


class TestGetOsType(unittest.TestCase):
    """Test OS type detection."""
    
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
    """Test config loading and saving."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        self.orig_config_file = dev.CONFIG_FILE
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.CONFIG_FILE = dev.CONFIG_DIR / 'repos.json'
    
    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        dev.CONFIG_FILE = self.orig_config_file
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_load_config_creates_default(self):
        """load_config raises when config file is missing."""
        with self.assertRaises(FileNotFoundError):
            dev.load_config()
    
    def test_save_and_load_config(self):
        """Config round-trips correctly."""
        config = {
            'version': 1,
            'repos': [{'name': 'test-repo', 'remoteUrl': 'https://example.com/test.git'}]
        }
        dev.save_config(config)
        loaded = dev.load_config()
        self.assertEqual(loaded['repos'][0]['name'], 'test-repo')


class TestComputeRepoName(unittest.TestCase):
    """Test repo name computation."""
    
    def test_simple_repo_name(self):
        """Simple repo uses folder name."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'my-repo'
            repo.mkdir()
            name = dev.compute_repo_name(repo)
            self.assertEqual(name, 'my-repo')
    
    def test_nested_gclient_repo(self):
        """Repo under gclient enlistment uses parent/name format."""
        with tempfile.TemporaryDirectory() as tmp:
            platform = Path(tmp) / 'platform'
            platform.mkdir()
            (platform / '.gclient').touch()
            src = platform / 'src'
            src.mkdir()
            
            name = dev.compute_repo_name(src)
            self.assertEqual(name, 'platform/src')


class TestHasRealConflictMarkers(unittest.TestCase):
    """Test conflict marker detection."""
    
    def test_no_conflicts(self):
        content = "# Title\n\n## Section\nSome content\n"
        self.assertFalse(dev.has_real_conflict_markers(content))
    
    def test_real_conflict_markers(self):
        content = "## Section\n<<<<<<< HEAD\nVersion A\n=======\nVersion B\n>>>>>>> branch\n"
        self.assertTrue(dev.has_real_conflict_markers(content))
    
    def test_example_markers_in_backticks(self):
        content = "If you see `<<<<<<<` markers, resolve them.\n"
        self.assertFalse(dev.has_real_conflict_markers(content))
    
    def test_example_markers_in_code_block(self):
        content = "```\n<<<<<<< branch\n=======\n>>>>>>> other\n```\n"
        self.assertFalse(dev.has_real_conflict_markers(content))


class TestGetBasePath(unittest.TestCase):
    """Test base path resolution."""
    
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


class TestCopilotInstructionsSync(unittest.TestCase):
    """Test copilot instructions merge and sync."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.CONFIG_DIR.mkdir(parents=True)
        self.workspace = Path(self.temp_dir) / 'workspace'
        self.workspace.mkdir()
        (self.workspace / '.github').mkdir()
    
    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_workspace_changes_sync_to_repoconfig(self):
        """Workspace edits propagate to repoconfig on merge."""
        initial = "# Copilot\n\n## Rules\nInitial rules\n"
        (dev.CONFIG_DIR / 'copilot-instructions.md').write_text(initial)
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(initial)
        
        updated = "# Copilot\n\n## Rules\nInitial rules\n\n## ADO PAT\nThe PAT is used for PRs and builds.\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(updated)
        
        dev.merge_copilot_instructions_to_repoconfig(self.workspace)
        
        repoconfig_content = (dev.CONFIG_DIR / 'copilot-instructions.md').read_text()
        self.assertIn("ADO PAT", repoconfig_content)
        self.assertIn("PRs and builds", repoconfig_content)
    
    def test_repoconfig_changes_sync_to_workspace(self):
        """Remote repoconfig updates propagate to workspace on apply."""
        initial = "# Copilot\n\n## Rules\nInitial rules\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(initial)
        
        updated_from_remote = "# Copilot\n\n## Rules\nInitial rules\n\n## New Section\nContent from another machine.\n"
        (dev.CONFIG_DIR / 'copilot-instructions.md').write_text(updated_from_remote)
        
        dev.apply_copilot_instructions_to_workspace(self.workspace)
        
        workspace_content = (self.workspace / '.github' / 'copilot-instructions.md').read_text()
        self.assertIn("New Section", workspace_content)
        self.assertIn("Content from another machine", workspace_content)
    
    def test_workspace_is_source_of_truth_during_merge(self):
        """Workspace content replaces repoconfig on merge."""
        repoconfig_content = "## Old Section\nOld content\n"
        workspace_content = "## New Section\nNew content\n"
        
        (dev.CONFIG_DIR / 'copilot-instructions.md').write_text(repoconfig_content)
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(workspace_content)
        
        dev.merge_copilot_instructions_to_repoconfig(self.workspace)
        
        result = (dev.CONFIG_DIR / 'copilot-instructions.md').read_text()
        self.assertEqual(result, workspace_content)
        self.assertNotIn("Old Section", result)
    
    def test_merge_skips_when_workspace_has_conflicts(self):
        """Merge skips if workspace file has conflict markers."""
        conflict_content = "## Section\n<<<<<<< HEAD\nVersion A\n=======\nVersion B\n>>>>>>> branch\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(conflict_content)
        (dev.CONFIG_DIR / 'copilot-instructions.md').write_text("original")
        
        dev.merge_copilot_instructions_to_repoconfig(self.workspace)
        
        result = (dev.CONFIG_DIR / 'copilot-instructions.md').read_text()
        self.assertEqual(result, "original")
    
    def test_apply_skips_when_repoconfig_has_conflicts(self):
        """Apply skips if repoconfig file has conflict markers."""
        conflict_content = "## Section\n<<<<<<< HEAD\nVersion A\n=======\nVersion B\n>>>>>>> branch\n"
        (dev.CONFIG_DIR / 'copilot-instructions.md').write_text(conflict_content)
        (self.workspace / '.github' / 'copilot-instructions.md').write_text("original")
        
        dev.apply_copilot_instructions_to_workspace(self.workspace)
        
        result = (self.workspace / '.github' / 'copilot-instructions.md').read_text()
        self.assertEqual(result, "original")
    
    def test_merge_handles_empty_repoconfig(self):
        """Merge works when repoconfig doesn't exist yet."""
        workspace_content = "# Copilot\n\nNew content\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(workspace_content)
        
        repoconfig_file = dev.CONFIG_DIR / 'copilot-instructions.md'
        if repoconfig_file.exists():
            repoconfig_file.unlink()
        
        dev.merge_copilot_instructions_to_repoconfig(self.workspace)
        
        self.assertTrue(repoconfig_file.exists())
        self.assertEqual(repoconfig_file.read_text(), workspace_content)
    
    def test_apply_creates_workspace_github_dir(self):
        """Apply creates .github dir if missing."""
        shutil.rmtree(self.workspace / '.github')
        
        repoconfig_content = "# Copilot\n\nContent\n"
        (dev.CONFIG_DIR / 'copilot-instructions.md').write_text(repoconfig_content)
        
        dev.apply_copilot_instructions_to_workspace(self.workspace)
        
        workspace_file = self.workspace / '.github' / 'copilot-instructions.md'
        self.assertTrue(workspace_file.exists())
        self.assertEqual(workspace_file.read_text(), repoconfig_content)
    
    def test_no_change_when_content_identical(self):
        content = "# Copilot\n\n## Rules\nSome rules\n"
        (self.workspace / '.github' / 'copilot-instructions.md').write_text(content)
        (dev.CONFIG_DIR / 'copilot-instructions.md').write_text(content)
        
        result = dev.merge_copilot_instructions_to_repoconfig(self.workspace)
        self.assertFalse(result)
        
        result = dev.apply_copilot_instructions_to_workspace(self.workspace)
        self.assertFalse(result)


class TestPythonList(unittest.TestCase):
    """Test python list command."""

    @patch('subprocess.run')
    @patch('dev.get_os_type', return_value='linux')
    @patch.dict(os.environ, {'PATH': '/usr/bin'})
    @patch('os.listdir', return_value=['python3', 'python3.12'])
    @patch('os.path.isfile', return_value=True)
    @patch('os.access', return_value=True)
    @patch('os.path.realpath', side_effect=lambda p: p)
    def test_finds_python_versions(self, mock_real, mock_access, mock_isfile, mock_listdir, mock_os, mock_run):
        from unittest.mock import MagicMock
        mock_run.return_value = MagicMock(returncode=0, stdout='Python 3.12.0')
        args = argparse.Namespace()
        result = dev.cmd_python_list(args)
        self.assertEqual(result, 0)
        self.assertTrue(mock_run.called)

    @patch('dev.get_os_type', return_value='linux')
    @patch.dict(os.environ, {'PATH': ''})
    def test_no_python_found(self, mock_os):
        args = argparse.Namespace()
        result = dev.cmd_python_list(args)
        self.assertEqual(result, 1)


class TestClaudeInstructionsSync(unittest.TestCase):
    """Test Claude instructions merge and sync."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_config_dir = dev.CONFIG_DIR
        dev.CONFIG_DIR = Path(self.temp_dir) / 'repoconfig'
        dev.CONFIG_DIR.mkdir(parents=True)
        self.workspace = Path(self.temp_dir) / 'workspace'
        self.workspace.mkdir()
        (self.workspace / '.claude').mkdir()

    def tearDown(self):
        dev.CONFIG_DIR = self.orig_config_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_workspace_changes_sync_to_repoconfig(self):
        """Workspace edits propagate to repoconfig on merge."""
        initial = "# Dev CLI\n\nInitial content\n"
        (dev.CONFIG_DIR / 'CLAUDE.md').write_text(initial)
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(initial)

        updated = "# Dev CLI\n\nInitial content\n\n## New Section\nNew content\n"
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(updated)

        dev.merge_claude_instructions_to_repoconfig(self.workspace)

        repoconfig_content = (dev.CONFIG_DIR / 'CLAUDE.md').read_text()
        self.assertIn("New Section", repoconfig_content)

    def test_repoconfig_changes_sync_to_workspace(self):
        """Remote repoconfig updates propagate to workspace on apply."""
        initial = "# Dev CLI\n\nInitial content\n"
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(initial)

        updated_from_remote = "# Dev CLI\n\nInitial content\n\n## Remote Section\nFrom another machine.\n"
        (dev.CONFIG_DIR / 'CLAUDE.md').write_text(updated_from_remote)

        dev.apply_claude_instructions_to_workspace(self.workspace)

        workspace_content = (self.workspace / '.claude' / 'CLAUDE.md').read_text()
        self.assertIn("Remote Section", workspace_content)

    def test_workspace_is_source_of_truth_during_merge(self):
        """Workspace content replaces repoconfig on merge."""
        repoconfig_content = "## Old Section\nOld content\n"
        workspace_content = "## New Section\nNew content\n"

        (dev.CONFIG_DIR / 'CLAUDE.md').write_text(repoconfig_content)
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(workspace_content)

        dev.merge_claude_instructions_to_repoconfig(self.workspace)

        result = (dev.CONFIG_DIR / 'CLAUDE.md').read_text()
        self.assertEqual(result, workspace_content)
        self.assertNotIn("Old Section", result)

    def test_merge_skips_when_workspace_has_conflicts(self):
        """Merge skips if workspace file has conflict markers."""
        conflict_content = "## Section\n<<<<<<< HEAD\nVersion A\n=======\nVersion B\n>>>>>>> branch\n"
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(conflict_content)
        (dev.CONFIG_DIR / 'CLAUDE.md').write_text("original")

        dev.merge_claude_instructions_to_repoconfig(self.workspace)

        result = (dev.CONFIG_DIR / 'CLAUDE.md').read_text()
        self.assertEqual(result, "original")

    def test_apply_skips_when_repoconfig_has_conflicts(self):
        """Apply skips if repoconfig file has conflict markers."""
        conflict_content = "## Section\n<<<<<<< HEAD\nVersion A\n=======\nVersion B\n>>>>>>> branch\n"
        (dev.CONFIG_DIR / 'CLAUDE.md').write_text(conflict_content)
        (self.workspace / '.claude' / 'CLAUDE.md').write_text("original")

        dev.apply_claude_instructions_to_workspace(self.workspace)

        result = (self.workspace / '.claude' / 'CLAUDE.md').read_text()
        self.assertEqual(result, "original")

    def test_merge_handles_empty_repoconfig(self):
        """Merge works when repoconfig doesn't exist yet."""
        workspace_content = "# Dev CLI\n\nNew content\n"
        (self.workspace / '.claude' / 'CLAUDE.md').write_text(workspace_content)

        repoconfig_file = dev.CONFIG_DIR / 'CLAUDE.md'
        if repoconfig_file.exists():
            repoconfig_file.unlink()

        dev.merge_claude_instructions_to_repoconfig(self.workspace)

        self.assertTrue(repoconfig_file.exists())
        self.assertEqual(repoconfig_file.read_text(), workspace_content)

    def test_apply_creates_workspace_claude_dir(self):
        """Apply creates .claude dir if missing."""
        shutil.rmtree(self.workspace / '.claude')

        repoconfig_content = "# Dev CLI\n\nContent\n"
        (dev.CONFIG_DIR / 'CLAUDE.md').write_text(repoconfig_content)

        dev.apply_claude_instructions_to_workspace(self.workspace)

        workspace_file = self.workspace / '.claude' / 'CLAUDE.md'
        self.assertTrue(workspace_file.exists())
        self.assertEqual(workspace_file.read_text(), repoconfig_content)

    def test_no_change_when_content_identical(self):
        """No writes when content is identical."""
        content = "# Dev CLI\n\n## Rules\nSome rules\n"
        workspace_file = self.workspace / '.claude' / 'CLAUDE.md'
        repoconfig_file = dev.CONFIG_DIR / 'CLAUDE.md'

        workspace_file.write_text(content)
        repoconfig_file.write_text(content)

        result = dev.merge_claude_instructions_to_repoconfig(self.workspace)
        self.assertFalse(result)

        result = dev.apply_claude_instructions_to_workspace(self.workspace)
        self.assertFalse(result)


if __name__ == '__main__':
    # Run with verbosity
    unittest.main(verbosity=2)
