"""Offline installer and shell integration tests."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
POWERSHELL = shutil.which('pwsh') or shutil.which('powershell')


class TestInstaller(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dev onboarding ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'new home'
        self.home.mkdir()
        self.source = self.base / 'source'
        self.source.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_ALLOW_PROTOCOL='file',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.com',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.com')
        (self.source / 'dev.py').write_text('# fixture\n', encoding='utf-8')
        (self.source / 'dev').write_text(
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/init-calls"\n',
            encoding='utf-8')
        (self.source / 'dev.ps1').write_text(
            '[System.IO.File]::AppendAllText((Join-Path $HOME "init-calls"), '
            '($args -join " ") + "`n")\nexit 0\n', encoding='utf-8')
        self.git(self.source, 'init')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-m', 'installer fixture')
        self.env['DEV_CLI_REPOSITORY'] = str(self.source)

    def git(self, path, *args):
        return subprocess.run(
            ['git', '-C', str(path), *args], env=self.env, check=True,
            text=True, capture_output=True).stdout.strip()

    def install(self):
        if os.name == 'nt':
            quoted_home = str(self.home).replace("'", "''")
            quoted_script = str(ROOT / 'install.ps1').replace("'", "''")
            command = [
                POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-Command',
                f"Set-Variable HOME '{quoted_home}' -Force; & '{quoted_script}'"]
        else:
            command = ['/bin/bash', str(ROOT / 'install.sh')]
        return subprocess.run(command, env=self.env, text=True, capture_output=True)

    def test_fresh_install_and_repeat_preserve_existing_checkout(self):
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        checkout = self.home / 'dev_cli'
        custom = checkout / 'custom.txt'
        custom.write_text('keep\n', encoding='utf-8')
        self.env['DEV_CLI_REPOSITORY'] = str(self.base / 'unavailable')
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(custom.read_text(), 'keep\n')
        self.assertEqual((self.home / 'init-calls').read_text().splitlines(), ['init', 'init'])

    def test_refuses_nonempty_destination_without_deleting_files(self):
        destination = self.home / 'dev_cli'
        destination.mkdir()
        (destination / 'keep.txt').write_text('keep', encoding='utf-8')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing to overwrite', result.stderr)
        self.assertEqual((destination / 'keep.txt').read_text(), 'keep')
        self.assertFalse((self.home / 'init-calls').exists())

    def test_initializes_existing_unpopulated_submodule(self):
        self.git(self.home, 'init')
        self.git(self.home, 'submodule', 'add', str(self.source), 'dev_cli')
        self.git(self.home, 'commit', '-m', 'bootstrap parent')
        self.git(self.home, 'submodule', 'deinit', '-f', '--', 'dev_cli')
        self.env['DEV_CLI_REPOSITORY'] = str(self.base / 'unavailable')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / 'dev_cli' / '.git').is_file())
        self.assertEqual((self.home / 'init-calls').read_text().strip(), 'init')
        self.assertEqual(self.git(self.home, 'diff', '--name-only'), '')

    def test_clone_failure_does_not_report_success(self):
        self.env['DEV_CLI_REPOSITORY'] = str(self.base / 'unavailable')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / 'init-calls').exists())

    def test_init_failure_is_propagated(self):
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        name = 'dev.ps1' if os.name == 'nt' else 'dev'
        (self.home / 'dev_cli' / name).write_text('exit 17\n', encoding='utf-8')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipIf(os.name == 'nt', 'requires native Unix shell')
    def test_root_can_bootstrap_git_without_sudo(self):
        tools = self.base / 'tools'
        tools.mkdir()
        commands = {
            'id': '#!/bin/sh\nprintf "0\\n"\n',
            'apt-get': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/packages"\n'
                       'touch "$HOME/git-ready"\n',
            'git': '#!/bin/sh\n[ -f "$HOME/git-ready" ] || exit 1\n'
                   f'exec "{shutil.which("git")}" "$@"\n',
        }
        for name, content in commands.items():
            path = tools / name
            path.write_text(content, encoding='utf-8')
            path.chmod(0o755)
        for name in ('bash', 'touch'):
            (tools / name).symlink_to(shutil.which(name))
        self.env['PATH'] = str(tools)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / 'packages').read_text().splitlines(),
                         ['update', 'install -y git'])

    @unittest.skipIf(os.name == 'nt', 'requires native Unix shell')
    def test_stock_macos_waits_for_command_line_tools_then_continues(self):
        tools = self.base / 'mac tools'
        tools.mkdir()
        commands = {
            'uname': '#!/bin/sh\nprintf "Darwin\\n"\n',
            'xcode-select': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/xcode-calls"\n'
                            'if [ "$1" = --install ]; then exit 0; fi\n'
                            '[ -f "$HOME/git-ready" ]\n',
            'sleep': '#!/bin/sh\ntouch "$HOME/git-ready"\n',
            'git': '#!/bin/sh\n[ -f "$HOME/git-ready" ] || exit 1\n'
                   f'exec "{shutil.which("git")}" "$@"\n',
        }
        for name, content in commands.items():
            path = tools / name
            path.write_text(content, encoding='utf-8')
            path.chmod(0o755)
        for name in ('bash', 'touch'):
            (tools / name).symlink_to(shutil.which(name))
        self.env['PATH'] = str(tools)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / 'xcode-calls').read_text().splitlines(),
                         ['--install', '-p', '-p'])
        self.assertEqual((self.home / 'init-calls').read_text().strip(), 'init')


@unittest.skipUnless(os.name == 'nt', 'requires Windows PowerShell')
class TestPowerShellProfile(unittest.TestCase):
    def test_managed_python_precedes_system_and_custom_hook_runs_last(self):
        with tempfile.TemporaryDirectory(prefix='dev profile ') as temp:
            home = Path(temp)
            tool = home / 'dev_cli'
            shell = tool / 'shell'
            shell.mkdir(parents=True)
            shutil.copy2(ROOT / 'shell' / 'profile.ps1', shell)
            setup = tool / 'setup'
            setup.mkdir()
            for original in (ROOT / 'setup').glob('install_*.ps1'):
                (setup / original.name).write_text('', encoding='utf-8')
            managed = home / 'managed python'
            override = home / 'custom python'
            managed.mkdir()
            override.mkdir()
            for directory in (managed, override):
                (directory / 'python.cmd').write_text('@echo off\n', encoding='utf-8')
            (tool / 'dev.ps1').write_text(
                'switch ($args[0]) {\n'
                '  python { Write-Output $env:MANAGED_TEST_BIN }\n'
                '  repo { Write-Output $HOME }\n'
                '  config { }\n'
                '}\nexit 0\n', encoding='utf-8')
            hooks = home / 'dev_env'
            hooks.mkdir()
            (hooks / 'env.ps1').write_text('throw "legacy hook must not also run"\n', encoding='utf-8')
            (hooks / 'env_windows.ps1').write_text(
                'if ((Get-Command python).Source -ne '
                '(Join-Path $env:MANAGED_TEST_BIN "python.cmd")) {\n'
                '  throw "Managed Python was not first before the custom hook"\n'
                '}\n'
                'if ($env:DEV -ne $HOME) { throw "Generic setup did not finish" }\n'
                '$env:PATH = "$env:OVERRIDE_TEST_BIN;$env:PATH"\n'
                '$env:HOOK_SEEN = "yes"\n', encoding='utf-8')
            env = dict(os.environ, USERPROFILE=str(home), MANAGED_TEST_BIN=str(managed),
                       OVERRIDE_TEST_BIN=str(override), DEVCONFIG='example-machine')
            env.pop('HOOK_SEEN', None)
            quoted_home = str(home).replace("'", "''")
            command = [
                POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-Command', f"$ErrorActionPreference='Stop'; Set-Variable HOME '{quoted_home}' -Force; "
                '. "$HOME\\dev_cli\\shell\\profile.ps1"; '
                '@($env:HOOK_SEEN, (Get-Command python).Source) | ConvertTo-Json -Compress']
            result = subprocess.run(command, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), ['yes', str(override / 'python.cmd')])
            (hooks / 'env_windows.ps1').unlink()
            result = subprocess.run(command, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [None, str(managed / 'python.cmd')])


@unittest.skipIf(os.name == 'nt', 'requires native Unix shells')
class TestUnixProfiles(unittest.TestCase):
    def test_legacy_hook_does_not_run_or_switch_shells(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            tool = home / 'dev_cli'
            tool.mkdir()
            (tool / 'dev').write_text(
                '#!/bin/sh\ncase "$1" in\n'
                'python) printf "%s/dev_cli\\n" "$HOME";;\n'
                'repo) printf "%s\\n" "$HOME";;\n'
                'config) :;;\nesac\n')
            (tool / 'dev').chmod(0o755)
            (tool / 'zsh').write_text('#!/bin/sh\nprintf "legacy-zsh\\n"\n')
            (tool / 'zsh').chmod(0o755)
            hooks = home / 'dev_env'
            hooks.mkdir()
            (hooks / 'env.sh').write_text('echo "must not run zsh settings in bash"; return 42\n')
            result = subprocess.run(
                ['/bin/bash', '--noprofile', '--norc', '-ic',
                 f'source "{ROOT / "shell" / "bash.sh"}" || exit $?; printf "bash-retained\\n"'],
                env=dict(os.environ, HOME=str(home), DEVCONFIG='example-machine',
                         PATH='/usr/bin:/bin'), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'bash-retained')

    def test_python_is_primary_and_hook_can_override_it(self):
        for shell_name, platform in (
                ('bash', 'Darwin'), ('bash', 'Linux'), ('zsh', 'Darwin'), ('zsh', 'Linux')):
            shell = shutil.which(shell_name)
            if not shell:
                continue
            with self.subTest(shell=shell_name, platform=platform), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                tool = home / 'dev_cli'
                (tool / 'shell').mkdir(parents=True)
                shutil.copy2(ROOT / 'shell' / f'{shell_name}.sh', tool / 'shell')
                managed = home / 'managed python'
                override = home / 'custom python'
                tools = home / 'bin'
                for directory in (managed, override, tools):
                    directory.mkdir()
                for directory in (managed, override):
                    (directory / 'python').write_text('#!/bin/sh\nexit 0\n')
                    (directory / 'python').chmod(0o755)
                (tools / 'uname').write_text(f'#!/bin/sh\nprintf "{platform}\\n"\n')
                (tools / 'uname').chmod(0o755)
                for optional in ('zoxide', 'fzf'):
                    (tools / optional).write_text('#!/bin/sh\nexit 0\n')
                    (tools / optional).chmod(0o755)
                (tool / 'dev').write_text(
                    '#!/bin/sh\ncase "$1" in\n'
                    'python) printf "%s\\n" "$MANAGED_TEST_BIN";;\n'
                    'repo) printf "%s\\n" "$HOME";;\n'
                    'config) :;;\nesac\n')
                (tool / 'dev').chmod(0o755)
                hooks = home / 'dev_env'
                hooks.mkdir()
                (hooks / 'env.sh').write_text('echo "legacy hook must not also run"; return 25\n')
                hook_name = 'env_mac.sh' if platform == 'Darwin' else 'env_linux.sh'
                (hooks / hook_name).write_text(
                    '[[ "$(command -v python)" == "$MANAGED_TEST_BIN/python" ]] || return 23\n'
                    '[[ "$DEV" == "$HOME" ]] || return 24\n'
                    'export PATH="$OVERRIDE_TEST_BIN:$PATH"\nexport HOOK_SEEN=yes\n')
                env = dict(os.environ, HOME=str(home), MANAGED_TEST_BIN=str(managed),
                           OVERRIDE_TEST_BIN=str(override), DEVCONFIG='example-machine',
                           PATH=f'{tools}:/usr/bin:/bin')
                env.pop('HOOK_SEEN', None)
                flags = ['--noprofile', '--norc', '-ic'] if shell_name == 'bash' else ['-f', '-c']
                command = [shell, *flags, f'source "$HOME/dev_cli/shell/{shell_name}.sh" || exit $?; '
                           'printf "%s\\n" "$HOOK_SEEN" "$(command -v python)"']
                result = subprocess.run(command, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ['yes', str(override / 'python')])
                (hooks / hook_name).unlink()
                result = subprocess.run(command, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ['', str(managed / 'python')])


if __name__ == '__main__':
    unittest.main()
