"""Golden rule: dev_cli and its hooks must never block a new shell.

Starts each shell registered by `dev init` the way a terminal would, with the
real user profiles, and checks that `ai` (or `dev` when AI is disabled) runs.
Only runs from the installed checkout, since it exercises the live setup.
"""

import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest

import ai
import dev
import terminal

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
