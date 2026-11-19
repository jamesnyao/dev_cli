"""Shell startup tests, including inert managed-worktree regression fixtures.

The live smoke test runs only from the installed checkout; disposable profile
tests use local command stubs and never load real hooks or install anything.
"""

import os
from pathlib import Path
import json
import shutil
import subprocess
import time
import unittest

import ai
import dev
import terminal
from test_checkouts import CheckoutTestCase

HERE = Path(__file__).resolve().parent
INSTALLED = HERE == (Path.home() / 'dev_cli').resolve()
TIMEOUT = 180
SESSION_VARIABLES = ('DEV', 'DEVCONFIG', 'DEV_ENV', 'DEV_BASE_PATH', 'DEV_PROMPT_USER')


def _registered(profile, line):
    path = Path.home() / profile
    return path.is_file() and line in path.read_text(encoding='utf-8', errors='replace')


def _shells():
    """Yield (name, argv builder) for each installed shell that loads dev_cli."""
    if os.name == 'nt':
        if _registered('.psrc.ps1', dev.PSRC_SOURCE_LINE):
            for shell in ('pwsh', 'powershell'):
                if shutil.which(shell):
                    # -NoExit loads the interactive profile, as a terminal tab does.
                    yield shell, lambda command, shell=shell: [
                        shell, '-NoLogo', '-NoExit', '-Command', f'{command}; exit $LASTEXITCODE']
        bash = terminal._git_bash()  # pylint: disable=protected-access
        if bash and _registered('.bashrc', dev.BASHRC_SOURCE_LINE):
            yield 'git-bash', lambda command: [str(bash), '--login', '-i', '-c', command]
        return
    if shutil.which('bash') and _registered('.bashrc', dev.BASHRC_SOURCE_LINE):
        yield 'bash', lambda command: ['bash', '--login', '-i', '-c', command]
    if shutil.which('zsh') and _registered('.zshrc', dev.ZSHRC_SOURCE_LINE):
        yield 'zsh', lambda command: ['zsh', '-i', '-l', '-c', command]


def _fixture_shell_path(path):
    value = Path(path).as_posix()
    if os.name == 'nt':
        return '/' + value[0].lower() + value[2:]
    return value


def _fixture_bash():
    if os.name == 'nt':
        candidate = Path(os.environ.get('ProgramFiles', 'C:\\Program Files')) / 'Git/bin/bash.exe'
        return str(candidate) if candidate.is_file() else None
    return shutil.which('bash')


class TestCheckoutShellProfiles(CheckoutTestCase):
    """Source real profiles with inert local launchers, hooks, and installers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixture = cls.checkout
        for tool in (fixture.primary, fixture.paired):
            (tool / 'shell').mkdir()
            (tool / 'setup').mkdir()
            for name in ('bash.sh', 'zsh.sh', 'profile.ps1', 'context.sh'):
                shutil.copyfile(HERE / 'shell' / name, tool / 'shell' / name)
            for installer in (HERE / 'setup').glob('install_*.ps1'):
                (tool / 'setup' / installer.name).write_text('# inert fixture\n', encoding='utf-8')
            launcher = tool / 'dev'
            launcher.write_text(
                '#!/bin/sh\n'
                f'printf "%s|%s\\n" "{_fixture_shell_path(tool)}" "$*" >> "$DEV_TEST_CALLS"\n'
                'case "$*" in\n'
                '  "python path") printf "%s\\n" "$DEV_TEST_PYTHON" ;;\n'
                '  "repo root") printf "%s\\n" "$DEV_TEST_WORKSPACE" ;;\n'
                '  "config get identity.username") printf "fixture-user\\n" ;;\n'
                '  "config context --field privateRoot") printf "%s\\n" "$DEV_TEST_PRIVATE" ;;\n'
                '  *) printf "Unexpected fixture command: %s\\n" "$*" >&2; exit 93 ;;\n'
                'esac\n', encoding='utf-8', newline='\n')
            launcher.chmod(0o755)
            (tool / 'dev.ps1').write_text(
                'Add-Content -LiteralPath $env:DEV_TEST_CALLS -Value "$PSScriptRoot|$($args -join \' \')"\n'
                'switch ($args -join \' \') {\n'
                '    "python path" { $env:DEV_TEST_PYTHON }\n'
                '    "repo root" { $env:DEV_TEST_WORKSPACE }\n'
                '    "config get identity.username" { "fixture-user" }\n'
                '    "config context --field privateRoot" { $env:DEV_TEST_PRIVATE }\n'
                '    default { throw "Unexpected fixture command: $args" }\n'
                '}\n$global:LASTEXITCODE = 0\n', encoding='utf-8')
        for private, label in ((fixture.home, 'main'), (fixture.private, 'linked')):
            hooks = private / 'dev_env'
            hooks.mkdir()
            for name in ('env_mac.sh', 'env_linux.sh', 'env_windows.sh'):
                (hooks / name).write_text(
                    '[[ -n "$COPILOT_CUSTOM_INSTRUCTIONS_DIRS" ]] || return 94\n'
                    f'export DEV_TEST_HOOK={label}\n', encoding='utf-8', newline='\n')
            (hooks / 'env_windows.ps1').write_text(
                'if (-not $env:COPILOT_CUSTOM_INSTRUCTIONS_DIRS) {\n'
                '    throw "Profile exports must precede the private hook"\n'
                '}\n'
                f'$env:DEV_TEST_HOOK = "{label}"\n', encoding='utf-8')
        tools = fixture.root / 'inert shell tools'
        tools.mkdir()
        for name, body in (('uname', 'printf "Darwin\\n"'), ('zoxide', ':'), ('fzf', ':')):
            path = tools / name
            path.write_text('#!/bin/sh\n' + body + '\n', encoding='utf-8', newline='\n')
            path.chmod(0o755)
        (fixture.home / 'fixture python').mkdir()

    def setUp(self):
        super().setUp()
        self.calls = self.checkout.root / f'{self._testMethodName}.log'
        self.calls.write_text('', encoding='utf-8')

    def source_profile(self, shell, *, private=None, cwd=None, tool=None, override=None,
                       workspace=None, late_override=None, rc_file=None):
        fixture = self.checkout
        private = private or fixture.home
        tool = tool or fixture.primary
        is_powershell = shell == 'powershell'
        convert = str if is_powershell else _fixture_shell_path
        environment = {key: value for key, value in fixture.env.items()
                       if key not in (*SESSION_VARIABLES, 'COPILOT_SKILLS_DIRS',
                                      'COPILOT_CUSTOM_INSTRUCTIONS_DIRS', 'DEV_TEST_HOOK',
                                      '_dev_cli_bash_loaded', 'BASH_ENV', 'ENV', 'ZDOTDIR')}
        environment.update({
            'HOME': convert(fixture.home), 'DEVCONFIG': self._testMethodName,
            'COPILOT_HOME': convert(fixture.home / 'unchanged copilot state'),
            'DEV_TEST_HOME': str(fixture.home), 'DEV_TEST_CALLS': convert(self.calls),
            'DEV_TEST_PYTHON': convert(fixture.home / 'fixture python'),
            'DEV_TEST_WORKSPACE': convert(workspace or private),
            'DEV_TEST_PRIVATE': convert(private),
            'DEV_TEST_BIN': convert(fixture.root / 'inert shell tools'),
        })
        if override is not None:
            environment['DEV_CONFIG_OVERRIDE'] = convert(override)
        if late_override:
            environment['DEV_TEST_LATE_OVERRIDE'] = convert(fixture.root / 'late override.json')
            environment['DEV_TEST_LATE_WORKSPACE'] = convert(fixture.foreign)
        if is_powershell:
            executable = shutil.which('pwsh') or shutil.which('powershell')
            if not executable:
                self.skipTest('PowerShell is not installed')
            profile = str(rc_file or tool / 'shell' / 'profile.ps1').replace("'", "''")
            after = (
                '$env:DEV_CONFIG_OVERRIDE = $env:DEV_TEST_LATE_OVERRIDE; '
                '$env:DEV_TEST_WORKSPACE = $env:DEV_TEST_LATE_WORKSPACE; '
                '$env:DEV = Invoke-DevCached repo, root; ' if late_override else '')
            command = (
                '$ErrorActionPreference = "Stop"; '
                'Set-Variable -Name HOME -Value $env:DEV_TEST_HOME -Force; '
                f'. \'{profile}\'; '
                + after +
                '@{workspace=$env:DEV;skills=$env:COPILOT_SKILLS_DIRS;'
                'instructions=$env:COPILOT_CUSTOM_INSTRUCTIONS_DIRS;'
                'hook=$env:DEV_TEST_HOOK;copilotHome=$env:COPILOT_HOME} | ConvertTo-Json -Compress')
            argv = [executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command]
        else:
            executable = _fixture_bash() if shell == 'bash' else shutil.which('zsh')
            if not executable:
                self.skipTest(f'{shell} is not installed')
            environment['DEV_TEST_PROFILE'] = convert(rc_file or tool / 'shell' / f'{shell}.sh')
            git_bin = convert(Path(shutil.which('git')).parent)
            after = (
                'export DEV_CONFIG_OVERRIDE="$DEV_TEST_LATE_OVERRIDE"; '
                'export DEV_TEST_WORKSPACE="$DEV_TEST_LATE_WORKSPACE"; '
                'DEV="$(dev_cached repo root)" || exit $?; ' if late_override else '')
            command = (
                f'export PATH="$DEV_TEST_BIN:{git_bin}:/usr/bin:/bin"; '
                'source "$DEV_TEST_PROFILE" || exit $?; '
                + after +
                'printf "DEV_TEST_RESULT\\n%s\\n%s\\n%s\\n%s\\n%s\\n" '
                '"$DEV" "$COPILOT_SKILLS_DIRS" "$COPILOT_CUSTOM_INSTRUCTIONS_DIRS" '
                '"$DEV_TEST_HOOK" "$COPILOT_HOME"')
            argv = ([executable, '--noprofile', '--norc', '-i', '-c', command] if shell == 'bash'
                    else [executable, '-f', '-c', command])
        result = subprocess.run(
            argv, env=environment, cwd=cwd or fixture.home, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding='utf-8', check=False, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if is_powershell:
            output = json.loads(result.stdout)
        else:
            values = result.stdout.split('DEV_TEST_RESULT\n', 1)[1].splitlines()
            output = dict(zip(('workspace', 'skills', 'instructions', 'hook', 'copilotHome'), values))
        self.assertEqual(output, {
            'workspace': convert(fixture.foreign if late_override else workspace or private),
            'skills': convert(private / 'dev_ai' / 'default' / 'skills'),
            'instructions': convert(private / 'dev_ai' / 'default'),
            'hook': 'main' if private == fixture.home else 'linked',
            'copilotHome': convert(fixture.home / 'unchanged copilot state'),
        })

    def check_linked_cache(self, shell):
        self.source_profile(shell)
        self.calls.write_text('', encoding='utf-8')
        self.source_profile(shell, private=self.checkout.private, cwd=self.checkout.private)
        self.assertIn('config context --field privateRoot', self.calls.read_text(encoding='utf-8-sig'))

    def check_override_cache(self, shell):
        self.source_profile(shell)
        override = self.checkout.root / f'{shell} explicit override.json'
        override.write_text(json.dumps({'workspaceRoots': {
            self._testMethodName: str(self.checkout.foreign)}}), encoding='utf-8')
        self.source_profile(shell, override=override, workspace=self.checkout.foreign)

    def check_warm_main(self, shell):
        self.source_profile(shell)
        self.calls.write_text('', encoding='utf-8')
        self.source_profile(shell)
        self.assertEqual(self.calls.read_text(encoding='utf-8-sig'), '')

    def check_source_root(self, shell):
        self.source_profile(shell, tool=self.checkout.paired,
                            private=self.checkout.private, cwd=self.checkout.foreign)
        calls = self.calls.read_text(encoding='utf-8-sig').splitlines()
        expected = str(self.checkout.paired) if shell == 'powershell' else _fixture_shell_path(self.checkout.paired)
        self.assertEqual({line.split('|', 1)[0] for line in calls}, {expected})

    def check_generated_redirect(self, shell):
        source, name = {
            'bash': (dev.BASHRC_SOURCE_LINE, '.bashrc'),
            'zsh': (dev.ZSHRC_SOURCE_LINE, '.zshrc'),
            'powershell': (dev.PSRC_SOURCE_LINE, '.psrc.ps1'),
        }[shell]
        rc_file = self.checkout.private / name
        dev._ensure_profile_source(rc_file, source)
        if shell != 'powershell':
            self.assertNotIn(b'\r', rc_file.read_bytes())
        self.source_profile(shell, tool=self.checkout.paired, private=self.checkout.private,
                            cwd=self.checkout.foreign, rc_file=rc_file)

    def test_bash_late_override_refreshes_cached_workspace(self):
        self.source_profile('bash', late_override=True)

    def test_powershell_late_override_refreshes_cached_workspace(self):
        self.source_profile('powershell', late_override=True)

    def test_generated_bash_redirect_loads_checked_out_tool(self):
        self.check_generated_redirect('bash')

    def test_generated_powershell_redirect_loads_checked_out_tool(self):
        self.check_generated_redirect('powershell')

    def test_generated_zsh_redirect_loads_checked_out_tool(self):
        self.check_generated_redirect('zsh')

    def test_bash_worktree_ignores_warm_canonical_cache(self):
        self.check_linked_cache('bash')

    def test_bash_explicit_override_ignores_warm_main_cache(self):
        self.check_override_cache('bash')

    def test_bash_warm_main_uses_no_python_launcher(self):
        self.check_warm_main('bash')

    def test_bash_sourced_checkout_controls_launcher_profiles_and_hooks(self):
        self.check_source_root('bash')

    def test_powershell_worktree_ignores_warm_canonical_cache(self):
        self.check_linked_cache('powershell')

    def test_powershell_explicit_override_ignores_warm_main_cache(self):
        self.check_override_cache('powershell')

    def test_powershell_warm_main_uses_no_python_launcher(self):
        self.check_warm_main('powershell')

    def test_powershell_sourced_checkout_controls_launcher_profiles_and_hooks(self):
        self.check_source_root('powershell')

    def test_zsh_sourced_checkout_controls_launcher_profiles_and_hooks(self):
        self.check_source_root('zsh')


@unittest.skipUnless(INSTALLED, 'runs only from the installed ~/dev_cli checkout')
class TestNewShellStartsAndRunsAi(unittest.TestCase):
    def test_new_shells_start_and_run_ai(self):
        shells = list(_shells())
        if not shells:
            self.skipTest('no shell profile registered by dev init')
        enabled = ai.provider_name(dev.load_config()) == 'ghcopilot'
        command = 'ai --version' if enabled else 'dev repo root'
        environment = {key: value for key, value in os.environ.items()
                       if key not in SESSION_VARIABLES}
        for name, argv in shells:
            with self.subTest(shell=name):
                start = time.monotonic()
                try:
                    result = subprocess.run(
                        argv(command), stdin=subprocess.DEVNULL, capture_output=True,
                        text=True, encoding='utf-8', errors='replace', env=environment,
                        cwd=Path.home(), timeout=TIMEOUT, check=False)
                except subprocess.TimeoutExpired:
                    self.fail(f'{name} did not finish `{command}` within {TIMEOUT}s')
                elapsed = time.monotonic() - start
                detail = f'{name} `{command}` ({elapsed:.1f}s)\n{result.stdout}\n{result.stderr}'
                self.assertEqual(result.returncode, 0, detail)
                if enabled:
                    self.assertIn('Copilot', result.stdout, detail)
                else:
                    self.assertTrue(result.stdout.strip(), detail)


if __name__ == '__main__':
    unittest.main()
