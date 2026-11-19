"""Native, disposable Git fixtures for managed checkout selection."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import checkouts
import configuration


ROOT = Path(__file__).resolve().parent


class CheckoutFixture:
    """An absorbed tool submodule, paired worktrees, and a foreign repository."""

    def __init__(self, root):
        self.root = root / 'native checkouts with spaces'
        self.root.mkdir()
        self.home = self.root / 'actual home'
        self.primary = self.home / 'dev_cli'
        self.linked = self.root / 'standalone tool worktree'
        self.private = self.root / 'paired private worktree'
        self.paired = self.private / 'dev_cli'
        self.uninitialized = self.root / 'uninitialized private worktree'
        self.foreign = self.root / 'unrelated repository'
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_') and key != 'DEV_CONFIG_OVERRIDE' and value}
        self.env.update({
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_AUTHOR_NAME': 'Checkout Fixture',
            'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
            'GIT_COMMITTER_NAME': 'Checkout Fixture',
            'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
            'HOME': str(self.home), 'USERPROFILE': str(self.home),
        })
        seed = self.root / 'tool seed'
        self.init_repo(seed, {'dev.py': '# tool fixture\n', 'runtime.py': '# runtime fixture\n'})
        self.init_repo(self.home, {'dev_config.json': '{"fixture":"primary"}\n'})
        self.git(self.home, 'submodule', 'add', str(seed), 'dev_cli')
        self.git(self.home, 'commit', '-am', 'Register absorbed tool submodule')
        self.git(self.primary, 'worktree', 'add', '--detach', str(self.linked), 'HEAD')
        self.git(self.home, 'worktree', 'add', '--detach', str(self.private), 'HEAD')
        self.git(self.primary, 'worktree', 'add', '--detach', str(self.paired), 'HEAD')
        self.git(self.home, 'worktree', 'add', '--detach', str(self.uninitialized), 'HEAD')
        self.init_repo(self.foreign, {'dev.py': '# foreign tool\n', 'runtime.py': '# foreign runtime\n'})
        for directory in (self.home, self.private, self.uninitialized):
            (directory / 'dev_ai' / 'default' / 'skills').mkdir(parents=True)
        (self.private / 'dev_config.json').write_text('{"fixture":"paired"}\n', encoding='utf-8')
        (self.uninitialized / 'dev_config.json').write_text(
            '{"fixture":"uninitialized"}\n', encoding='utf-8')
        (self.linked.parent / 'dev_config.json').write_text(
            '{"fixture":"wrong sibling parent"}\n', encoding='utf-8')

    def git(self, cwd, *arguments):
        result = subprocess.run(
            ['git', '-c', 'protocol.file.allow=always', '-c', 'core.fsmonitor=false',
             '-c', f'core.hooksPath={self.root / "no hooks"}', '-C', str(cwd), *arguments],
            env=self.env, check=True, capture_output=True, text=True, encoding='utf-8', timeout=30)
        return result.stdout.strip()

    def init_repo(self, path, files):
        path.mkdir(parents=True, exist_ok=True)
        self.git(path, 'init', '--initial-branch=main')
        for name, contents in files.items():
            (path / name).write_text(contents, encoding='utf-8')
        self.git(path, 'add', '.')
        self.git(path, 'commit', '-m', 'Create local fixture')

    def copy_modules(self, target):
        for name in ('dev.py', 'ai.py', 'checkouts.py', 'configuration.py', 'runtime.py',
                     'diff_view.py', 'environments.py', 'terminal.py', 'dev_config.json'):
            shutil.copyfile(ROOT / name, target / name)

    def standalone_pair(self):
        primary = self.root / 'standalone installation' / 'tool'
        linked = self.root / 'standalone task'
        self.init_repo(primary, {'dev.py': '# standalone\n', 'runtime.py': '# runtime\n'})
        self.git(primary, 'worktree', 'add', '--detach', str(linked), 'HEAD')
        return primary, linked

    def python(self, arguments, *, cwd=None, environment=None):
        return subprocess.run(
            [sys.executable, '-X', 'utf8', *arguments], cwd=cwd or self.home,
            env={**self.env, **(environment or {})}, capture_output=True, text=True,
            encoding='utf-8', check=False, timeout=30)

    def module_python(self, code, *, tool=None, cwd=None, environment=None):
        return self.python(
            ['-I', '-c', 'import json, sys\nfrom pathlib import Path\n'
             'sys.path.insert(0, sys.argv[1])\n' + code, str(tool or self.primary)],
            cwd=cwd, environment=environment)


class CheckoutTestCase(unittest.TestCase):
    """Share immutable native Git topology within each focused test class."""

    @classmethod
    def setUpClass(cls):
        fixtures = ROOT / '.dev_temp' / 'checkout-tests'
        fixtures.mkdir(parents=True, exist_ok=True)
        scratch = tempfile.TemporaryDirectory(dir=fixtures)
        cls.addClassCleanup(scratch.cleanup)
        cls.checkout = CheckoutFixture(Path(scratch.name))

    def setUp(self):
        self.enterContext(patch.dict(os.environ, self.checkout.env, clear=True))


class TestCheckoutContext(CheckoutTestCase):
    def resolve(self, script_dir=None, cwd=None):
        fixture = self.checkout
        return checkouts.resolve_context(
            script_dir or fixture.primary, cwd=cwd or fixture.home, home=fixture.home)

    def assert_context(self, context, tool, private, config=None):
        self.assertEqual(context.tool_root, tool.resolve())
        self.assertEqual(context.primary_root, self.checkout.primary.resolve())
        self.assertEqual(context.private_root, private.resolve())
        self.assertEqual(context.config_file, config or private / 'dev_config.json')
        self.assertEqual(context.state_dir, self.checkout.home / '.dev_temp')

    def test_canonical_defaults_preserve_installed_paths(self):
        self.assert_context(self.resolve(), self.checkout.primary, self.checkout.home)

    def test_metadata_helpers_cannot_consume_command_input(self):
        script = (
            'import json, subprocess, sys\nfrom pathlib import Path\n'
            'sys.path.insert(0, sys.argv[1])\nimport checkouts\n'
            'real_run = subprocess.run\n'
            'def probe(command, **kwargs):\n'
            '    return real_run([sys.executable, "-c", '
            '"import sys; sys.stdout.write(sys.stdin.read())"], **kwargs)\n'
            'checkouts.subprocess.run = probe\n'
            'metadata = checkouts._git(Path.cwd(), "rev-parse", "--show-toplevel")\n'
            'print(json.dumps([metadata, sys.stdin.read()]))\n')
        result = subprocess.run(
            [sys.executable, '-I', '-c', script, str(ROOT)], cwd=self.checkout.home,
            env=self.checkout.env, input='command payload', capture_output=True,
            text=True, encoding='utf-8', check=False, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['', 'command payload'])

    def test_absorbed_primary_is_resolved_from_native_core_worktree(self):
        fixture = self.checkout
        self.assertTrue((fixture.primary / '.git').is_file())
        git_dir = Path(fixture.git(fixture.primary, 'rev-parse', '--absolute-git-dir'))
        core_worktree = fixture.git(fixture.primary, 'config', '--get', 'core.worktree')
        self.assertEqual((git_dir / core_worktree).resolve(), fixture.primary.resolve())
        self.assert_context(self.resolve(cwd=fixture.linked), fixture.linked, fixture.home)

    def test_standalone_linked_tool_uses_canonical_private_config(self):
        fixture = self.checkout
        self.assertTrue((fixture.linked / '.git').is_file())
        self.assert_context(self.resolve(fixture.linked), fixture.linked, fixture.home)
        self.assertEqual(json.loads((fixture.linked.parent / 'dev_config.json').read_text(
            encoding='utf-8'))['fixture'], 'wrong sibling parent')

    def test_nested_cwd_in_registered_tool_selects_linked_checkout(self):
        nested = self.checkout.linked / 'nested folder'
        nested.mkdir(exist_ok=True)
        self.assert_context(self.resolve(cwd=nested), self.checkout.linked, self.checkout.home)

    def test_explicit_linked_launcher_wins_over_other_managed_cwd(self):
        self.assert_context(self.resolve(self.checkout.linked, self.checkout.paired),
                            self.checkout.linked, self.checkout.home)

    def test_parent_worktree_selects_paired_registered_child_and_private_assets(self):
        fixture = self.checkout
        self.assertTrue((fixture.private / '.git').is_file())
        self.assertTrue((fixture.paired / '.git').is_file())
        self.assert_context(self.resolve(cwd=fixture.private / 'dev_ai'), fixture.paired, fixture.private)

    def test_uninitialized_parent_worktree_keeps_stable_code_and_private_config(self):
        fixture = self.checkout
        self.assertFalse((fixture.uninitialized / 'dev_cli' / '.git').exists())
        self.assert_context(self.resolve(cwd=fixture.uninitialized), fixture.primary, fixture.uninitialized)

    def test_unrelated_repository_with_tool_named_files_is_not_selected(self):
        self.assert_context(self.resolve(cwd=self.checkout.foreign),
                            self.checkout.primary, self.checkout.home)

    def test_unrelated_registered_worktree_is_not_selected(self):
        linked_foreign = self.checkout.root / 'foreign linked worktree'
        self.checkout.git(self.checkout.foreign, 'worktree', 'add', '--detach', str(linked_foreign), 'HEAD')
        self.assertTrue((linked_foreign / '.git').is_file())
        self.assert_context(self.resolve(cwd=linked_foreign), self.checkout.primary, self.checkout.home)

    def test_explicit_override_wins_without_moving_assets_or_state(self):
        override = self.checkout.root / 'explicit config' / 'settings.json'
        with patch.dict(os.environ, {'DEV_CONFIG_OVERRIDE': str(override)}):
            self.assert_context(self.resolve(cwd=self.checkout.private),
                                self.checkout.paired, self.checkout.private, override)

    def test_shared_state_never_follows_linked_checkout(self):
        for cwd in (self.checkout.linked, self.checkout.private, self.checkout.uninitialized):
            with self.subTest(checkout=cwd.name):
                context = self.resolve(cwd=cwd)
                self.assertEqual(context.state_dir, self.checkout.home / '.dev_temp')
                self.assertNotIn(cwd, context.state_dir.parents)

    def test_configuration_maps_primary_override_into_parent_worktree(self):
        with patch.object(Path, 'cwd', return_value=self.checkout.private), \
                patch.object(Path, 'home', return_value=self.checkout.home):
            self.assertEqual(configuration.resolve_override_config_file(self.checkout.primary),
                             self.checkout.private / 'dev_config.json')

    def test_configuration_linked_launcher_uses_canonical_private_config(self):
        with patch.object(Path, 'cwd', return_value=self.checkout.foreign), \
                patch.object(Path, 'home', return_value=self.checkout.home):
            self.assertEqual(configuration.resolve_override_config_file(self.checkout.linked),
                             self.checkout.home / 'dev_config.json')

    def test_standalone_primary_outside_home_preserves_parent_config_and_home_assets(self):
        fixture = self.checkout
        primary, linked = fixture.standalone_pair()
        context = checkouts.resolve_context(primary, cwd=linked, home=fixture.home)
        self.assertEqual(context.tool_root, linked)
        self.assertEqual(context.primary_root, primary)
        self.assertEqual(context.private_root, fixture.home)
        self.assertEqual(context.config_file, primary.parent / 'dev_config.json')
        self.assertEqual(context.state_dir, fixture.home / '.dev_temp')


if __name__ == '__main__':
    unittest.main()
