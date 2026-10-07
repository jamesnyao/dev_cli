"""Offline installer and shell integration tests."""

import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
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
    @staticmethod
    def bash_version():
        version = subprocess.check_output(
            ['/bin/bash', '--noprofile', '--norc', '-c',
             'printf "%s.%s" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"'], text=True)
        return tuple(int(part) for part in version.split('.'))

    def test_bash_editor_installer_is_offline_testable_and_idempotent(self):
        version = self.bash_version()
        for failure in ('', 'download', 'install', 'incomplete'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                tools = home / 'bin'
                tools.mkdir()
                archive = home / 'fixture.tar.xz'
                installer = (
                    '#!/bin/bash\nset -e\n'
                    '[[ ${INSTALL_FAILURE-} != install ]] || exit 9\n'
                    '[[ $1 == --install ]] || exit 10\n'
                    'mkdir -p "$2/blesh"\nprintf "# installed\\n" > "$2/blesh/ble.sh"\n')
                with tarfile.open(archive, 'w:xz') as bundle:
                    entry = tarfile.TarInfo('ble-nightly/ble.sh')
                    data = installer.encode('utf-8')
                    entry.size = len(data)
                    bundle.addfile(entry, io.BytesIO(data))
                (tools / 'curl').write_text(
                    '#!/bin/bash\nprintf "download\\n" >> "$HOME/downloads"\n'
                    '[[ ${INSTALL_FAILURE-} != download ]] || exit 22\n'
                    'cp "$HOME/fixture.tar.xz" "${@: -1}"\n')
                (tools / 'curl').chmod(0o755)
                environment = dict(os.environ, HOME=str(home), PATH=f'{tools}:/usr/bin:/bin',
                                   INSTALL_FAILURE=failure)
                installed = home / '.local' / 'share' / 'blesh' / 'ble.sh'
                if failure == 'incomplete':
                    installed.parent.mkdir(parents=True)
                command = ['/bin/bash', str(ROOT / 'setup' / 'install_blesh.sh')]
                result = subprocess.run(command, env=environment, capture_output=True,
                                        text=True, timeout=30)
                if version < (4, 0):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('require Bash 4 or newer', result.stdout)
                    self.assertFalse(installed.exists())
                    self.assertFalse((home / 'downloads').exists())
                    return
                if failure:
                    self.assertEqual(
                        result.returncode, {'download': 22, 'install': 9, 'incomplete': 1}[failure],
                        result.stdout + result.stderr)
                    self.assertFalse(installed.exists())
                    if failure == 'incomplete':
                        self.assertIn('Incomplete ble.sh installation', result.stderr)
                        self.assertFalse((home / 'downloads').exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(installed.read_text(), '# installed\n')
                    result = subprocess.run(command, env=dict(environment, INSTALL_FAILURE='download'),
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((home / 'downloads').read_text().splitlines(), ['download'])
                self.assertEqual(list((home / '.local' / 'share').glob('.blesh-install.*')), [])

    def test_bash_editor_defaults_allow_user_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            xdg = home / 'config' / 'blesh'
            xdg.mkdir(parents=True)
            (xdg / 'init.sh').write_text('bleopt complete_auto_complete=xdg\n')
            script = r'''
ble-import() { printf 'import %s\n' "$*"; }
bleopt() { printf 'option %s\n' "$*"; }
ble-face() { printf 'face %s\n' "$*"; }
fzf() { :; }
source "$TEST_BLERC"
'''
            environment = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(xdg.parent),
                               TEST_BLERC=str(ROOT / 'shell' / 'blerc.sh'))
            for override in ('xdg', 'home'):
                if override == 'home':
                    (home / '.blerc').write_text('bleopt complete_auto_complete=home\n')
                result = subprocess.run(['/bin/bash', '-c', script], env=environment,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                self.assertEqual(lines[:3], [
                    'import config/readline',
                    'option complete_auto_complete=1 complete_auto_complete_opts=syntax-disabled',
                    'face -s auto_complete fg=8'])
                self.assertIn('import -d integration/fzf-key-bindings', lines)
                self.assertEqual(lines[-1], f'option complete_auto_complete={override}')

    def test_bash_starts_in_workspace_only_from_home(self):
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
            workspace = home / 'workspace with spaces'
            workspace.mkdir()
            project = workspace / 'project'
            project.mkdir()
            hooks = home / 'dev_env'
            hooks.mkdir()
            hook = hooks / 'env_linux.sh'
            environment = dict(os.environ, HOME=str(home), DEVCONFIG='example-machine',
                               PATH='/usr/bin:/bin')
            command = ['/bin/bash', '--noprofile', '--norc', '-ic',
                       f'source "{ROOT / "shell" / "bash.sh"}" || exit $?; printf "%s\\n" "$PWD"']
            for start, hook_cd, expected in (
                    (home, '', workspace), (project, '', project),
                    (home, 'builtin cd "$DEV/project"\n', project)):
                with self.subTest(start=start, hook_cd=hook_cd):
                    hook.write_text('export DEV="$HOME/workspace with spaces"\n' + hook_cd)
                    result = subprocess.run(command, cwd=start, env=environment,
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), str(expected))
            alias = home / 'home-link'
            alias.symlink_to(home, target_is_directory=True)
            hook.write_text('export DEV="$HOME/workspace with spaces"\n')
            result = subprocess.run(command, cwd=home, env=dict(environment, HOME=str(alias)),
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()).resolve(), workspace)
            hook.write_text('return 23\n')
            result = subprocess.run(command, cwd=home, env=environment,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 23, result.stderr)

    def test_bash_prompt_matches_powershell_and_preserves_hooks(self):
        if self.bash_version() < (4, 4):
            self.skipTest('prompt expansion assertions require Bash 4.4 or newer')
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            tool = home / 'dev_cli'
            (tool / 'shell').mkdir(parents=True)
            shutil.copy2(ROOT / 'shell' / 'bash.sh', tool / 'shell')
            (tool / 'dev').write_text(
                '#!/bin/sh\ncase "$1" in\n'
                'python) printf "%s/dev_cli\\n" "$HOME";;\n'
                'repo) printf "%s\\n" "$HOME";;\n'
                'config) printf "prompt-user\\n";;\nesac\n')
            (tool / 'dev').chmod(0o755)
            (tool / 'zoxide').write_text('#!/bin/sh\nexit 0\n')
            (tool / 'zoxide').chmod(0o755)
            hooks = home / 'dev_env'
            hooks.mkdir()
            (hooks / 'env_linux.sh').write_text('export DEV_PROMPT_HOST=prompt-host\n')
            repo = home / r'project\n$(touch path-injected)'
            repo.mkdir()
            branch = 'topic/$(touch${IFS}branch-injected)'
            subprocess.run(['git', 'init', '-b', branch, str(repo)],
                           capture_output=True, check=True)
            environment = dict(os.environ, HOME=str(home), DEVCONFIG='example-machine',
                               PATH='/usr/bin:/bin', PROMPT_REPO=str(repo))
            environment.pop('DEV_PROMPT_USER', None)
            environment.pop('DEV_PROMPT_HOST', None)
            for platform, windows_path, expected_path in (
                    ('linux', '', str(repo)),
                    ('darwin', '', str(repo)),
                    ('mingw64_nt', 'D:/dev/dev_scripts/repoconfig', 'dev/dev_scripts/repoconfig'),
                    ('mingw64_nt', 'C:/work/project with spaces', 'work/project with spaces'),
                    ('mingw64_nt', 'C:/work/$(touch path-injected)', 'work/$(touch path-injected)'),
                    ('msys_nt', 'D:/', '/'),
                    ('cygwin_nt', '//server/share/project', '//server/share/project')):
                for array in (False, True):
                    with self.subTest(platform=platform, path=windows_path, array=array):
                        original = "('seen=$?')" if array else "'seen=$?'"
                        script = f'PROMPT_COMMAND={original}\n' + r'''
source "$HOME/dev_cli/shell/bash.sh" || exit $?
source "$HOME/dev_cli/shell/bash.sh" || exit $?
printf '%s\0' "${PROMPT_COMMAND[@]}"
platform="$PROMPT_PLATFORM"
cygpath()
{
    [[ "$1" == -m && "$2" == "$PWD" ]] || exit 23
    printf '%s' "$PROMPT_WINDOWS_PATH"
}
builtin cd "$PROMPT_REPO" || exit $?
true
eval "${PROMPT_COMMAND[0]}"
printf '%s\0' "${PS1@P}"
false
eval "${PROMPT_COMMAND[0]}"
''' + ('eval "${PROMPT_COMMAND[1]}"\n' if array else '') + r'''
printf '%s\0' "${PS1@P}" "$seen"
git symbolic-ref HEAD refs/heads/other || exit $?
_dev_prompt
printf '%s\0' "${PS1@P}"
builtin cd "$HOME" || exit $?
_dev_prompt
printf '%s\0' "${PS1@P}"
'''
                        subprocess.run(['git', '-C', str(repo), 'symbolic-ref',
                                        'HEAD', f'refs/heads/{branch}'],
                                       capture_output=True, check=True)
                        result = subprocess.run(
                            ['/bin/bash', '--noprofile', '--norc', '-ic', script],
                            env=dict(environment, PROMPT_PLATFORM=platform,
                                     PROMPT_WINDOWS_PATH=windows_path),
                            capture_output=True, text=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        parts = result.stdout.split('\0')
                        commands = ['_dev_prompt', 'seen=$?'] if array else ['_dev_prompt; seen=$?']
                        self.assertEqual(parts[:len(commands)], commands)
                        success, failure, status, switched, outside, end = parts[len(commands):]
                        self.assertEqual(end, '')
                        self.assertEqual(status, '1')
                        plain = re.sub(r'\x1b\[[0-9;]*m|[\x01\x02]', '', success)
                        self.assertEqual(
                            plain, f'prompt-user@prompt-host {expected_path} ({branch}) $ ')
                        self.assertIn('\x1b[32m', success)
                        self.assertIn('\x1b[96m', success)
                        self.assertIn('\x1b[33m', success)
                        self.assertNotIn('\x1b[31m', success)
                        self.assertIn('\x1b[31m', failure)
                        self.assertIn('(other)', switched)
                        self.assertNotIn('(other)', outside)
                        self.assertNotIn('\n', success)
                        self.assertFalse((repo / 'path-injected').exists())
                        self.assertFalse((repo / 'branch-injected').exists())

    def test_git_bash_uses_windows_launchers_and_hook(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            tool = home / 'dev_cli'
            (tool / 'shell').mkdir(parents=True)
            shutil.copy2(ROOT / 'shell' / 'bash.sh', tool / 'shell')
            managed = home / 'managed'
            managed.mkdir()
            (managed / 'python').write_text('#!/bin/sh\nexit 0\n')
            (managed / 'python').chmod(0o755)
            tools = home / 'bin'
            tools.mkdir()
            (tools / 'uname').write_text('#!/bin/sh\nprintf "MINGW64_NT-test\\n"\n')
            (tools / 'uname').chmod(0o755)
            (tools / 'cygpath').write_text('#!/bin/sh\nprintf "%s\\n" "$2"\n')
            (tools / 'cygpath').chmod(0o755)
            (tool / 'dev.cmd').write_text(
                '#!/bin/sh\ncase "$1" in\n'
                'python) printf "%s\\n" "$MANAGED_TEST_BIN";;\n'
                'repo) printf "%s\\n" "$HOME";;\n'
                'config) :;;\nesac\n')
            (tool / 'dev.cmd').chmod(0o755)
            hooks = home / 'dev_env'
            hooks.mkdir()
            (hooks / 'env_windows.sh').write_text('export WINDOWS_HOOK_SEEN=yes\n')
            command = [
                '/bin/bash', '--noprofile', '--norc', '-ic',
                f'source "{tool / "shell" / "bash.sh"}" || exit $?; '
                'printf "%s\\n" "$WINDOWS_HOOK_SEEN" "$(command -v python)"',
            ]
            result = subprocess.run(
                command,
                env=dict(os.environ, HOME=str(home), MANAGED_TEST_BIN=str(managed),
                         DEVCONFIG='example-machine', PATH=f'{tools}:/usr/bin:/bin'),
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ['yes', str(managed / 'python')])

    def test_bash_profile_caches_dev_answers_and_loads_once(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            tool = home / 'dev_cli'
            (tool / 'shell').mkdir(parents=True)
            shutil.copy2(ROOT / 'shell' / 'bash.sh', tool / 'shell')
            (tool / 'dev').write_text(
                '#!/bin/sh\nprintf "%s\\n" "$1" >> "$HOME/dev-calls"\ncase "$1" in\n'
                'python) printf "%s/dev_cli\\n" "$HOME";;\n'
                'repo) printf "%s\\n" "$HOME";;\n'
                'config) printf "cached-user\\n";;\nesac\n')
            (tool / 'dev').chmod(0o755)
            hooks = home / 'dev_env'
            hooks.mkdir()
            (hooks / 'env_linux.sh').write_text('printf "hook\\n" >> "$HOME/hook-runs"\n')
            source = f'source "{tool / "shell" / "bash.sh"}" || exit $?; '
            command = ['/bin/bash', '--noprofile', '--norc', '-ic',
                       source + source + 'printf "%s\\n" "$DEV" "$DEV_PROMPT_USER"']
            env = dict(os.environ, HOME=str(home), DEVCONFIG='example-machine',
                       PATH='/usr/bin:/bin')
            env.pop('DEV_PROMPT_USER', None)
            calls = home / 'dev-calls'
            for expected_calls in (3, 3):
                result = subprocess.run(command, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [str(home), 'cached-user'])
                self.assertEqual(len(calls.read_text().splitlines()), expected_calls)
            self.assertEqual(len((home / 'hook-runs').read_text().splitlines()), 2)
            result = subprocess.run(command, env=dict(env, DEVCONFIG='other'),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(calls.read_text().splitlines()), 6)

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
