"""Tests for `dev set` named shell environments."""

import argparse
import json
import os
from io import StringIO
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import dev
import environments

POWERSHELL = shutil.which('pwsh') or shutil.which('powershell')
BASH = None if os.name == 'nt' else shutil.which('bash')


class TestResolve(unittest.TestCase):
    def test_expands_workspace_and_home(self):
        resolved = environments.resolve({
            'path': ['$DEV/tools', '$HOME/bin'],
            'env': {'TOOLS': '$DEV/tools', 'MODE': 'fast', 'GONE': None},
            'cwd': '$DEV/src',
        }, Path('/work'))
        self.assertEqual(resolved['path'], [os.path.normpath('/work/tools'),
                                            os.path.normpath(str(Path.home() / 'bin'))])
        self.assertEqual(resolved['env'], {
            'TOOLS': os.path.normpath('/work/tools'), 'MODE': 'fast', 'GONE': None})
        self.assertEqual(resolved['cwd'], os.path.normpath('/work/src'))

    def test_does_not_leak_dev_variable(self):
        with patch.dict(os.environ, {'DEV': 'original'}):
            environments.resolve({'path': ['$DEV']}, Path('/work'))
            self.assertEqual(os.environ['DEV'], 'original')

    def test_rejects_invalid_entries(self):
        for entry in ({'path': 'not-a-list'}, {'env': {'BAD NAME': 'x'}},
                      {'env': {'PATH': 'x'}}, {'env': {'N': 1}}, {'cwd': 5}, []):
            with self.assertRaises(ValueError):
                environments.resolve(entry, Path('/work'))

    def test_git_bash_paths(self):
        self.assertEqual(environments.posix_path('C:\\Users\\me\\tools'), '/c/Users/me/tools')
        self.assertEqual(environments.posix_path('D:\\'), '/d')
        self.assertEqual(environments.posix_path('/usr/bin'), '/usr/bin')


class TestCmdSet(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'dev_config.json'
        for patcher in (patch('dev.SAMPLE_CONFIG_FILE', self.root / 'missing.json'),
                        patch('dev.OVERRIDE_CONFIG_FILE', self.config),
                        patch.dict(os.environ, {'DEVCONFIG': 'box'})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write({'one': {'description': 'First', 'path': ['$DEV/bin']}, 'two': {}})

    def write(self, envs):
        self.config.write_text(json.dumps({
            'workspaceRoots': {'box': str(self.root)}, 'environments': envs}))

    def run_set(self, name=None, shell=None, quiet=False):
        out, err = StringIO(), StringIO()
        with patch('sys.stdout', out), patch('sys.stderr', err):
            code = dev.cmd_set(argparse.Namespace(name=name, shell=shell, quiet=quiet))
        return code, out.getvalue(), err.getvalue()

    def test_lists_and_marks_active(self):
        with patch.dict(os.environ, {'DEV_ENV': 'two'}):
            code, out, _ = self.run_set()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ['  one              First', '* two'])

    def test_empty_list_explains_config(self):
        self.write({})
        code, out, _ = self.run_set()
        self.assertEqual(code, 0)
        self.assertIn('"environments"', out)

    def test_unknown_name_fails(self):
        code, out, err = self.run_set('three', 'pwsh')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('one, two', err)

    def test_requires_shell_function(self):
        code, out, err = self.run_set('one')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('shell function', err)

    def test_warns_about_missing_path_and_confirms(self):
        code, out, err = self.run_set('one', 'bash')
        self.assertEqual(code, 0)
        self.assertIn('PATH entry does not exist', err)
        self.assertIn("'[OK] one: First'", out)
        code, out, _ = self.run_set('one', 'bash', quiet=True)
        self.assertNotIn('[OK]', out)


@unittest.skipUnless(POWERSHELL, 'requires PowerShell')
class TestPowerShellRender(unittest.TestCase):
    def test_switching_rebuilds_path_and_clears_previous_variables(self):
        with tempfile.TemporaryDirectory(prefix="dev set '") as temp:
            target = Path(temp)
            first = environments.render('first', {
                'path': [str(target / 'a')], 'env': {'ONLY_FIRST': "it's", 'SHARED': 'one'},
                'cwd': str(target)}, 'pwsh')
            second = environments.render('second', {
                'path': [str(target / 'b')], 'env': {'SHARED': 'two', 'EMPTY': ''},
                'cwd': str(target / 'missing')}, 'pwsh')
            script = target / 'apply.ps1'
            script.write_text(
                f"$env:PATH = 'base'\n{first}"
                "$after1 = @($env:PATH, $env:ONLY_FIRST, (Get-Location).Path)\n"
                f"{second}"
                "@($after1 + @($env:PATH, \"$env:ONLY_FIRST\", $env:SHARED, $env:DEV_ENV,"
                " \"$env:EMPTY\")) | ConvertTo-Json -Compress\n", encoding='utf-8')
            env = {k: v for k, v in os.environ.items()
                   if k not in ('DEV_BASE_PATH', 'DEV_SET_VARS', 'DEV_ENV')}
            result = subprocess.run(
                [POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(script)],
                env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = json.loads(result.stdout.strip().splitlines()[-1])
            sep = os.pathsep
            self.assertEqual(values[:3], [f'{target / "a"}{sep}base', "it's", str(target)])
            self.assertEqual(values[3:], [f'{target / "b"}{sep}base', '', 'two', 'second', ''])
            self.assertIn('Directory does not exist', result.stdout + result.stderr)


@unittest.skipUnless(BASH, 'requires a native bash')
class TestPosixRender(unittest.TestCase):
    def test_switching_rebuilds_path_and_clears_previous_variables(self):
        with tempfile.TemporaryDirectory(prefix="dev set '") as temp:
            target = Path(temp)
            first = environments.render('first', {
                'path': [str(target / 'a')], 'env': {'ONLY_FIRST': "it's", 'GONE': None},
                'cwd': str(target)}, 'bash', windows=False)
            second = environments.render('second', {
                'path': [], 'env': {'SHARED': 'two'}, 'cwd': None}, 'bash', windows=False)
            result = subprocess.run(
                [BASH, '--noprofile', '--norc', '-c',
                 f'export GONE=x; {first} printf "%s\\n" "$PATH" "$ONLY_FIRST" "$PWD" "${{GONE:-unset}}"; '
                 f'{second} printf "%s\\n" "$PATH" "${{ONLY_FIRST:-unset}}" "$SHARED" "$DEV_ENV"'],
                env={'PATH': '/usr/bin:/bin', 'HOME': str(target)}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [
                f'{target / "a"}:/usr/bin:/bin', "it's", str(target), 'unset',
                '/usr/bin:/bin', 'unset', 'two', 'second'])


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'requires Windows PowerShell')
class TestPowerShellDevFunction(unittest.TestCase):
    def test_set_is_evaluated_in_session_and_cached(self):
        with tempfile.TemporaryDirectory(prefix='dev set ') as temp:
            home = Path(temp)
            tool = home / 'dev_cli'
            (tool / 'shell').mkdir(parents=True)
            shutil.copy2(Path(__file__).parent / 'shell' / 'profile.ps1', tool / 'shell')
            (tool / 'setup').mkdir()
            for original in (Path(__file__).parent / 'setup').glob('install_*.ps1'):
                (tool / 'setup' / original.name).write_text('', encoding='utf-8')
            (tool / 'dev.ps1').write_text(
                'switch ($args[0]) {\n'
                '  python { Write-Output $HOME }\n'
                '  repo { Write-Output $HOME }\n'
                '  set { Add-Content "$HOME\\calls" ($args -join " "); '
                "Write-Output \"`$env:SET_TEST = '$($args[1])'\" }\n"
                '  default { Write-Output "passthrough $args" }\n'
                '}\nexit 0\n', encoding='utf-8')
            quoted_home = str(home).replace("'", "''")
            command = [
                POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-Command', f"$ErrorActionPreference='Stop'; Set-Variable HOME '{quoted_home}' -Force; "
                '. "$HOME\\dev_cli\\shell\\profile.ps1"; dev set alpha; dev set alpha; '
                '@($env:SET_TEST, (dev pr list)) | ConvertTo-Json -Compress']
            env = dict(os.environ, USERPROFILE=str(home), DEVCONFIG='example-machine')
            result = subprocess.run(command, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), ['alpha', 'passthrough pr list'])
            self.assertEqual((home / 'calls').read_text(encoding='utf-8').split(),
                             ['set', 'alpha', '--shell', 'pwsh'])


if __name__ == '__main__':
    unittest.main()
