"""Bootstrap synchronization against isolated, local-only Git remotes."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import dev


class BootstrapFixture(unittest.TestCase):
    RUNTIME = (
        "import runpy, sys\n"
        "from pathlib import Path\n"
        "if __name__ == '__main__':\n"
        "    assert sys.argv[1] == '--dev', sys.argv\n"
        "    root = Path(__file__).resolve().parent\n"
        "    sys.path.insert(0, str(root))\n"
        "    sys.argv = [str(root / 'dev.py'), *sys.argv[2:]]\n"
        "    runpy.run_path(str(root / 'dev.py'), run_name='__main__')\n"
    )

    def setUp(self):
        fixtures = Path.cwd() / '.dev_temp' / 'bootstrap-sync-tests'
        fixtures.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=fixtures)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('GIT_') and value}
        environment.update({
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_ALLOW_PROTOCOL': 'file',
            'GIT_TERMINAL_PROMPT': '0',
            'DEVCONFIG': 'fixture',
            'DEV_CONFIG_OVERRIDE': str(self.root / 'override.json'),
        })
        for key in ('_DEV_BOOTSTRAP_UPDATED', '_DEV_PULLED_RCFILES'):
            environment.pop(key, None)
        self._patch(patch.dict(os.environ, environment, clear=True))
        self.tool = self.root / 'bootstrap'
        self.workspace = self.root / 'workspace'
        self.config = {
            'workspaceRoots': {'fixture': str(self.workspace)},
            'repos': [{'path': 'z-bootstrap', 'pathLinksTo': str(self.tool), 'bootstrap': True}],
        }
        source_path = Path(dev.__file__)
        source = source_path.read_text(encoding='utf-8')
        self.seed, self.remote, _ = self.make_repo('bootstrap', {
            'dev.py': source,
            'dev_config.json': json.dumps(self.config),
            '.gitignore': (source_path.parent / '.gitignore').read_text(encoding='utf-8'),
            'ai.py': (source_path.parent / 'ai.py').read_text(encoding='utf-8'),
            'configuration.py': (source_path.parent / 'configuration.py').read_text(encoding='utf-8'),
            'terminal.py': (source_path.parent / 'terminal.py').read_text(encoding='utf-8'),
            # Provisioning has its own suite; sync fixtures never install a runtime.
            'runtime.py': self.RUNTIME,
        })
        for patcher in (
                patch('dev.SCRIPT_DIR', self.tool),
                patch('dev.SAMPLE_CONFIG_FILE', self.tool / 'dev_config.json'),
                patch('dev.OVERRIDE_CONFIG_FILE', self.root / 'override.json'),
                patch('dev.DEV_TEMP_DIR', self.root / 'state'),
                patch.object(sys, 'argv', [str(self.tool / 'dev.py'), 'repo', 'sync', '--force'])):
            self._patch(patcher)

    def _patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def git(self, path, *arguments):
        result = subprocess.run(
            ['git', '-C', str(path), *arguments], capture_output=True,
            text=True, encoding='utf-8', timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def make_repo(self, name, files=None):
        seed = self.root / f'{name}-seed'
        remote = self.root / f'{name}.git'
        checkout = self.root / name
        self.git(self.root, 'init', '--initial-branch=main', str(seed))
        self.identity(seed)
        for relative, content in (files or {'file.txt': 'initial\n'}).items():
            target = seed / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
        self.git(seed, 'add', '-A')
        self.git(seed, 'commit', '-m', 'initial')
        self.git(self.root, 'clone', '--bare', str(seed), str(remote))
        self.git(seed, 'remote', 'add', 'origin', str(remote))
        self.git(self.root, 'clone', str(remote), str(checkout))
        self.identity(checkout)
        return seed, remote, checkout

    def identity(self, repository):
        self.git(repository, 'config', 'user.name', 'Bootstrap Test')
        self.git(repository, 'config', 'user.email', 'bootstrap@example.invalid')

    def commit(self, repository, filename, content):
        (repository / filename).write_text(content, encoding='utf-8')
        self.git(repository, 'add', '-A')
        self.git(repository, 'commit', '-m', f'update {filename}')
        return self.git(repository, 'rev-parse', 'HEAD')

    def set_config(self, config):
        self.config = config
        self.commit(self.tool, 'dev_config.json', json.dumps(config))

    def sync(self):
        return dev.cmd_repo_sync(argparse.Namespace(force=True))


class TestBootstrapSync(BootstrapFixture):
    def test_refreshed_code_and_runtime_execute_before_non_bootstrap_updates(self):
        ordinary_seed, _, ordinary = self.make_repo('ordinary')
        ordinary_head = self.commit(ordinary_seed, 'file.txt', 'updated by refreshed code\n')
        self.git(ordinary_seed, 'push', 'origin', 'main')
        trace = self.root / 'execution.log'
        source = (self.seed / 'dev.py').read_text(encoding='utf-8')
        update_signature = 'def _self_update(repo_path):\n'
        sync_signature = 'def _sync_repo_latest(repo_path, default_branch=None):\n'
        self.assertIn(update_signature, source)
        self.assertIn(sync_signature, source)

        def record(event):
            return ("    with open(os.environ['BOOTSTRAP_TEST_TRACE'], 'a', encoding='utf-8') as trace:\n"
                    f"        trace.write({event!r} + '\\n')\n")

        old_source = source.replace(update_signature, update_signature + record('old-bootstrap'), 1)
        old_source = old_source.replace(
            sync_signature, sync_signature + "    raise RuntimeError('old code reached ordinary sync')\n", 1)
        runtime = (
            "import json, os, runpy, sys\n"
            "from pathlib import Path\n"
            "if __name__ == '__main__':\n"
            "    root = Path(__file__).resolve().parent\n"
            "    assert sys.argv[1] == '--dev', sys.argv\n"
            "    pin = json.loads((root / 'dev_config.json').read_text())['pythonVersion']\n"
            "    with open(os.environ['BOOTSTRAP_TEST_TRACE'], 'a', encoding='utf-8') as trace:\n"
            "        trace.write('runtime-VERSION:' + pin + '\\n')\n"
            "    os.environ['BOOTSTRAP_TEST_PIN'] = pin\n"
            "    sys.path.insert(0, str(root))\n"
            "    sys.argv = [str(root / 'dev.py'), *sys.argv[2:]]\n"
            "    runpy.run_path(str(root / 'dev.py'), run_name='__main__')\n"
        )
        self.config['pythonVersion'] = '3.12.10'
        (self.seed / 'dev.py').write_text(old_source, encoding='utf-8')
        (self.seed / 'runtime.py').write_text(runtime.replace('VERSION', 'old'), encoding='utf-8')
        self.commit(self.seed, 'dev_config.json', json.dumps(self.config))
        self.git(self.seed, 'push', 'origin', 'main')
        self.git(self.tool, 'fetch', 'origin')
        self.git(self.tool, 'merge', '--ff-only', 'origin/main')

        refreshed = source.replace(update_signature, update_signature + record('repeated-bootstrap'), 1)
        refreshed = refreshed.replace(
            sync_signature, sync_signature + record('new-ordinary')
            + "    assert os.environ.get('BOOTSTRAP_TEST_PIN') == '3.14.1'\n", 1)
        self.config['pythonVersion'] = '3.14.1'
        new_workspace = self.root / 'refreshed-workspace'
        self.config['workspaceRoots']['fixture'] = str(new_workspace)
        self.config['repos'].insert(0, {'path': 'a-ordinary', 'pathLinksTo': str(ordinary)})
        (self.seed / 'dev.py').write_text(refreshed, encoding='utf-8')
        (self.seed / 'runtime.py').write_text(runtime.replace('VERSION', 'new'), encoding='utf-8')
        remote_head = self.commit(self.seed, 'dev_config.json', json.dumps(self.config))
        self.git(self.seed, 'push', 'origin', 'main')

        environment = {**os.environ, 'BOOTSTRAP_TEST_TRACE': str(trace)}
        result = subprocess.run(
            [sys.executable, str(self.tool / 'dev.py'), 'repo', 'sync', '--force'],
            env=environment, capture_output=True, text=True, encoding='utf-8', timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(trace.read_text().splitlines(),
                         ['old-bootstrap', 'runtime-new:3.14.1', 'new-ordinary'])
        self.assertEqual(self.git(self.tool, 'rev-parse', 'HEAD'), remote_head)
        self.assertEqual(self.git(ordinary, 'rev-parse', 'HEAD'), ordinary_head)
        self.assertEqual((new_workspace / 'a-ordinary').resolve(), ordinary.resolve())
        self.assertFalse(self.workspace.exists())
        self.assertEqual(result.stdout.count('z-bootstrap (updated during bootstrap)'), 1)

    def test_bootstrap_checkout_can_be_native_to_the_workspace(self):
        self.set_config({
            'workspaceRoots': {'fixture': str(self.root)},
            'repos': [{'path': 'bootstrap', 'bootstrap': True}],
        })
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'),
                         self.git(self.tool, 'rev-parse', 'HEAD'))
        self.assertFalse(dev._is_dir_link(self.tool))

    def test_bootstrap_is_not_routed_through_background_or_stale_branch_sync(self):
        self.config['repos'][0]['slowSync'] = True
        self.set_config(self.config)
        with patch('dev._report_background_sync_status') as background, \
                patch('dev.check_stale_branch') as stale, patch('dev._sync_repo_latest') as ordinary:
            self.assertEqual(self.sync(), 0)
        background.assert_not_called()
        stale.assert_not_called()
        ordinary.assert_not_called()

    def test_standalone_bootstrap_precedes_other_repositories_and_is_not_synced_twice(self):
        _, _, ordinary = self.make_repo('ordinary')
        self.config['repos'].insert(0, {'path': 'a-ordinary', 'pathLinksTo': str(ordinary)})
        self.set_config(self.config)
        original = dev._sync_repo_latest

        def sync_other(path, branch):
            self.assertEqual(self.git(self.remote, 'rev-parse', 'main'),
                             self.git(self.tool, 'rev-parse', 'HEAD'))
            return original(path, branch)

        with patch('dev._sync_repo_latest', side_effect=sync_other) as tracked_sync, \
                patch('dev.run_git', wraps=dev.run_git) as git:
            self.assertEqual(self.sync(), 0)
        tracked_sync.assert_called_once_with(ordinary, None)
        fetches = [call for call in git.call_args_list
                   if call.args[0] == self.tool and call.args[1] == 'fetch']
        self.assertEqual(len(fetches), 1)
        self.assertEqual((self.workspace / 'z-bootstrap').resolve(), self.tool.resolve())

    def test_no_bootstrap_entry_does_not_modify_or_publish_the_tool(self):
        self.set_config({**self.config, 'repos': []})
        (self.tool / 'local.txt').write_text('uncommitted work\n')
        before = self.git(self.tool, 'rev-parse', 'HEAD')
        remote_before = self.git(self.remote, 'rev-parse', 'main')
        with patch('dev._self_update') as update, patch('dev.sync_rcfiles_push') as push:
            self.assertEqual(self.sync(), 0)
        update.assert_not_called()
        push.assert_not_called()
        self.assertEqual(self.git(self.tool, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), remote_before)
        self.assertEqual((self.tool / 'local.txt').read_text(), 'uncommitted work\n')

    def test_skipped_bootstrap_does_not_update_or_publish(self):
        self.config['repos'][0]['skipOn'] = ['fixture']
        self.set_config(self.config)
        with patch('dev._self_update') as update, patch('dev.sync_rcfiles_push') as push:
            self.assertEqual(self.sync(), 0)
        update.assert_not_called()
        push.assert_not_called()
        self.assertFalse((self.workspace / 'z-bootstrap').exists())

    def test_invalid_bootstrap_stops_before_other_work(self):
        for entry in (
                {'path': 'missing', 'bootstrap': True},
                {'path': 'wrong', 'pathLinksTo': str(self.seed), 'bootstrap': True}):
            with self.subTest(entry=entry), \
                    patch('dev.load_config', return_value={**self.config, 'repos': [entry]}), \
                    patch('dev._self_update') as update, \
                    patch('dev.sync_rcfiles_push') as push, \
                    patch('dev._sync_repo_latest') as ordinary:
                self.assertEqual(self.sync(), 1)
                update.assert_not_called()
                push.assert_not_called()
                ordinary.assert_not_called()

    def test_duplicate_bootstrap_is_rejected_before_mutation(self):
        config = {**self.config, 'repos': self.config['repos'] * 2}
        with patch('dev.load_config', return_value=config), \
                patch('dev._self_update') as update, patch('dev.sync_rcfiles_push') as push:
            self.assertEqual(self.sync(), 1)
        update.assert_not_called()
        push.assert_not_called()

    def test_sync_root_error_is_reported_as_failure(self):
        with patch('dev._sync_repo_dir', side_effect=RuntimeError('Git inspection failed')), \
                patch('dev.emit_error') as error:
            self.assertEqual(self.sync(), 1)
        error.assert_called_once_with('Git inspection failed')

    def test_offline_bootstrap_preserves_dirty_work_and_stops_other_work(self):
        (self.tool / 'local.txt').write_text('uncommitted work\n')
        self.git(self.tool, 'remote', 'set-url', 'origin', str(self.root / 'missing.git'))
        before = self.git(self.tool, 'rev-parse', 'HEAD')
        with patch('dev.sync_rcfiles_push') as push, \
                patch('dev._sync_repo_latest') as ordinary, patch('dev.emit_error') as error:
            self.assertEqual(self.sync(), 1)
        push.assert_not_called()
        ordinary.assert_not_called()
        self.assertIn('Cannot fetch bootstrap repository', error.call_args.args[0])
        self.assertEqual(self.git(self.tool, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.git(self.tool, 'diff', '--cached'), '')
        self.assertEqual((self.tool / 'local.txt').read_text(), 'uncommitted work\n')

    def test_authentication_failure_is_surfaced_without_success(self):
        run_git = dev.run_git

        def denied_fetch(repository, *arguments):
            if arguments[0] == 'fetch':
                return False, 'Permission denied (publickey).'
            return run_git(repository, *arguments)

        with patch('dev.run_git', side_effect=denied_fetch), \
                patch('dev.emit_error') as error, patch('dev.emit_ok') as success:
            self.assertEqual(self.sync(), 1)
        self.assertIn('Permission denied (publickey)', error.call_args.args[0])
        success.assert_not_called()

    def test_push_failure_stops_before_other_repositories(self):
        self.commit(self.tool, 'local.txt', 'unpublished commit\n')
        self.git(self.tool, 'remote', 'set-url', '--push', 'origin', str(self.root / 'missing.git'))
        before = self.git(self.tool, 'rev-parse', 'HEAD')
        with patch('dev._sync_repo_latest') as ordinary, patch('dev.emit_error') as error:
            self.assertEqual(self.sync(), 1)
        ordinary.assert_not_called()
        self.assertIn('Failed to push', error.call_args.args[0])
        self.assertEqual(self.git(self.tool, 'rev-parse', 'HEAD'), before)
        self.assertEqual((self.tool / 'local.txt').read_text(), 'unpublished commit\n')

    def test_detached_bootstrap_is_rejected_without_committing_dirty_work(self):
        self.git(self.tool, 'checkout', '--detach')
        (self.tool / 'local.txt').write_text('keep detached work\n')
        before = self.git(self.tool, 'rev-parse', 'HEAD')
        with patch('dev.emit_error') as error:
            self.assertEqual(self.sync(), 1)
        self.assertIn('detached HEAD', error.call_args.args[0])
        self.assertEqual(self.git(self.tool, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.git(self.tool, 'diff', '--cached'), '')

    def test_rebase_conflict_preserves_local_commits_and_uncommitted_content(self):
        self.commit(self.seed, 'dev.py', 'remote conflicting edit\n')
        self.git(self.seed, 'push', 'origin', 'main')
        (self.tool / 'dev.py').write_text('local conflicting edit\n')
        with patch('dev.sync_rcfiles_push') as push:
            self.assertEqual(self.sync(), 1)
        push.assert_not_called()
        self.assertEqual((self.tool / 'dev.py').read_text(), 'local conflicting edit\n')
        self.assertEqual(self.git(self.tool, 'show', 'HEAD:dev.py'), 'local conflicting edit')
        self.assertFalse((self.tool / '.git' / 'rebase-merge').exists())

    def test_current_branch_upstream_remote_is_fetched_instead_of_origin(self):
        self.git(self.tool, 'remote', 'rename', 'origin', 'upstream')
        self.git(self.tool, 'remote', 'add', 'origin', str(self.root / 'missing.git'))
        self.commit(self.tool, 'local.txt', 'publish on configured upstream\n')
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'),
                         self.git(self.tool, 'rev-parse', 'HEAD'))

    def test_restart_uses_managed_runtime_and_propagates_child_failure(self):
        self.commit(self.seed, 'remote.txt', 'remote update\n')
        self.git(self.seed, 'push', 'origin', 'main')
        (self.tool / 'runtime.py').write_text('# runtime entry point\n')
        original = subprocess.run
        restarts = []

        def run(command, **kwargs):
            if command[0] == sys.executable:
                restarts.append((command, kwargs))
                return subprocess.CompletedProcess(command, 23)
            return original(command, **kwargs)

        with patch('dev.subprocess.run', side_effect=run), \
                patch('dev.sync_rcfiles_push') as push, self.assertRaises(SystemExit) as result:
            self.sync()
        self.assertEqual(result.exception.code, 23)
        push.assert_not_called()
        self.assertEqual(len(restarts), 1)
        command, kwargs = restarts[0]
        self.assertEqual(command, [sys.executable, '-I', str(self.tool / 'runtime.py'),
                                   '--dev', 'repo', 'sync', '--force'])
        self.assertEqual(json.loads(kwargs['env']['_DEV_BOOTSTRAP_UPDATED']),
                         [str(self.tool.resolve()), self.git(self.tool, 'rev-parse', 'HEAD')])

    def test_reexec_reloads_remote_config_before_cloning_other_repositories(self):
        _, ordinary_remote, _ = self.make_repo('ordinary')
        self.config['repos'].insert(0, {'path': 'a-new', 'remoteUrl': str(ordinary_remote)})
        new_head = self.commit(self.seed, 'dev_config.json', json.dumps(self.config))
        self.git(self.seed, 'push', 'origin', 'main')
        with self.assertRaises(SystemExit) as result:
            self.sync()
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(self.git(self.tool, 'rev-parse', 'HEAD'), new_head)
        self.assertEqual(self.git(self.workspace / 'a-new', 'rev-parse', 'HEAD'),
                         self.git(ordinary_remote, 'rev-parse', 'main'))

    def test_uncommitted_standalone_work_is_committed_and_published_once(self):
        (self.tool / 'local.txt').write_text('preserve this local work\n')
        before = self.git(self.remote, 'rev-list', '--count', 'main')
        with self.assertRaises(SystemExit) as result:
            self.sync()
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(self.git(self.tool, 'status', '--porcelain'), '')
        self.assertEqual(self.git(self.remote, 'show', 'main:local.txt'),
                         'preserve this local work')
        self.assertEqual(int(self.git(self.remote, 'rev-list', '--count', 'main')), int(before) + 1)

    def test_updated_config_can_disable_bootstrap_without_a_final_push(self):
        updated = {**self.config, 'repos': []}
        self.commit(self.seed, 'dev_config.json', json.dumps(updated))
        remote_head = self.git(self.seed, 'rev-parse', 'HEAD')
        self.git(self.seed, 'push', 'origin', 'main')
        self.commit(self.tool, 'local.txt', 'keep this unpublished commit\n')
        with self.assertRaises(SystemExit) as result:
            self.sync()
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), remote_head)
        self.assertEqual((self.tool / 'local.txt').read_text(), 'keep this unpublished commit\n')
        self.assertEqual(self.git(self.tool, 'rev-list', '--count', 'origin/main..HEAD'), '1')

    def test_restart_failure_is_reported_and_stops_other_work(self):
        self.commit(self.seed, 'remote.txt', 'remote update\n')
        self.git(self.seed, 'push', 'origin', 'main')
        original = subprocess.run

        def run(command, **kwargs):
            if command[0] == sys.executable:
                raise OSError('interpreter unavailable')
            return original(command, **kwargs)

        with patch('dev.subprocess.run', side_effect=run), patch('dev.emit_error') as error, \
                patch('dev.sync_rcfiles_push') as push:
            self.assertEqual(self.sync(), 1)
        self.assertIn('Cannot restart', error.call_args.args[0])
        self.assertIn('interpreter unavailable', error.call_args.args[0])
        push.assert_not_called()

    def test_restart_continuation_skips_duplicate_update_but_still_pushes(self):
        self.commit(self.tool, 'local.txt', 'ready for push\n')
        marker = json.dumps([str(self.tool.resolve()), self.git(self.tool, 'rev-parse', 'HEAD')])
        with patch.dict(os.environ, {'_DEV_BOOTSTRAP_UPDATED': marker}), \
                patch('dev._self_update') as update:
            self.assertEqual(self.sync(), 0)
            self.assertNotIn('_DEV_BOOTSTRAP_UPDATED', os.environ)
        update.assert_not_called()
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'),
                         self.git(self.tool, 'rev-parse', 'HEAD'))

    def test_stale_restart_continuation_does_not_skip_bootstrap(self):
        marker = json.dumps([str(self.tool.resolve()), 'old-head'])
        with patch.dict(os.environ, {'_DEV_BOOTSTRAP_UPDATED': marker}), \
                patch('dev._self_update', wraps=dev._self_update) as update:
            self.assertEqual(self.sync(), 0)
        update.assert_called_once_with(self.tool)


class TestPrivateBootstrapSync(BootstrapFixture):
    def setUp(self):
        super().setUp()
        self.parent_seed, self.parent_remote, self.parent = self.make_repo('home')
        self.git(self.parent, 'submodule', 'add', '-b', 'main', str(self.remote), 'dev_cli')
        self.child = self.parent / 'dev_cli'
        self.identity(self.child)
        self.git(self.parent, 'commit', '-am', 'add tool submodule')
        self.git(self.parent, 'push', 'origin', 'main')
        self._patch(patch('dev.SCRIPT_DIR', self.child))

    def private_sync(self):
        config = {**self.config, 'repos': [{
            'path': 'home', 'pathLinksTo': str(self.parent), 'bootstrap': True,
        }]}
        with patch('dev.load_config', return_value=config):
            return self.sync()

    def test_private_parent_is_bootstrap_outside_native_workspace(self):
        self.assertEqual(dev._sync_repo_dir().resolve(), self.parent.resolve())
        self.assertEqual(self.private_sync(), 0)
        self.assertEqual((self.workspace / 'home').resolve(), self.parent.resolve())

    def test_dirty_child_stops_before_parent_commit_and_push(self):
        before = self.git(self.parent, 'rev-parse', 'HEAD')
        (self.child / 'dev.py').write_text('uncommitted child work\n')
        (self.parent / 'private.txt').write_text('uncommitted parent work\n')
        self.assertEqual(self.private_sync(), 1)
        self.assertEqual(self.git(self.parent, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.git(self.parent_remote, 'rev-parse', 'main'), before)
        self.assertEqual(self.git(self.parent, 'diff', '--cached'), '')
        self.assertEqual((self.child / 'dev.py').read_text(), 'uncommitted child work\n')
        self.assertEqual((self.parent / 'private.txt').read_text(), 'uncommitted parent work\n')

    def test_unpublished_child_commit_stops_parent_publication(self):
        before = self.git(self.parent, 'rev-parse', 'HEAD')
        child_head = self.commit(self.child, 'work.txt', 'unpublished child work\n')
        self.assertEqual(self.private_sync(), 1)
        self.assertEqual(self.git(self.child, 'rev-parse', 'HEAD'), child_head)
        self.assertEqual(self.git(self.parent_remote, 'rev-parse', 'main'), before)

    def test_child_fetch_failure_stops_parent_publication(self):
        before = self.git(self.parent, 'rev-parse', 'HEAD')
        self.git(self.child, 'remote', 'set-url', 'origin', str(self.root / 'missing-child.git'))
        self.assertEqual(self.private_sync(), 1)
        self.assertEqual(self.git(self.parent, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.git(self.parent_remote, 'rev-parse', 'main'), before)

    def test_published_submodule_update_is_committed_and_pushed_with_parent(self):
        # Keep configuration in the private parent, as a real submodule layout does.
        config = {**self.config, 'repos': [{
            'path': 'home', 'pathLinksTo': str(self.parent), 'bootstrap': True,
        }]}
        self.commit(self.parent, 'dev_config.json', json.dumps(config))
        self.git(self.parent, 'push', 'origin', 'main')
        new_child_head = self.commit(self.seed, 'remote.txt', 'published child update\n')
        self.git(self.seed, 'push', 'origin', 'main')
        with patch.dict(os.environ, {'DEV_CONFIG_OVERRIDE': str(self.parent / 'dev_config.json')}), \
                self.assertRaises(SystemExit) as result:
            self.private_sync()
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(self.git(self.child, 'rev-parse', 'HEAD'), new_child_head)
        self.assertEqual(self.git(self.parent_remote, 'rev-parse', 'main:dev_cli'), new_child_head)
        self.assertEqual(self.git(self.parent, 'status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()
