"""Windows Terminal setup for the configured default shell."""

import json
import os
from pathlib import Path
import platform
import uuid

from configuration import load_jsonc


GIT_BASH_GUID = '{00000000-0000-0000-ba54-000000000002}'
DEFAULT_SHELLS = ('system', 'bash')


def default_shell(config):
    """Validate and return the configured default shell."""
    shell = config.get('defaultShell', 'system')
    if not isinstance(shell, str) or shell not in DEFAULT_SHELLS:
        raise ValueError(f"defaultShell must be one of: {', '.join(DEFAULT_SHELLS)}.")
    return shell


def _git_bash():
    candidates = []
    for variable in ('ProgramFiles', 'LOCALAPPDATA'):
        root = os.environ.get(variable)
        if root:
            suffix = ('Git', 'bin', 'bash.exe') if variable == 'ProgramFiles' \
                else ('Programs', 'Git', 'bin', 'bash.exe')
            candidates.append(Path(root).joinpath(*suffix))
    return next((path for path in candidates if path.is_file()), None)


def _settings_path():
    local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    candidates = [
        local / 'Packages' / 'Microsoft.WindowsTerminal_8wekyb3d8bbwe'
        / 'LocalState' / 'settings.json',
        local / 'Packages' / 'Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe'
        / 'LocalState' / 'settings.json',
        local / 'Microsoft' / 'Windows Terminal' / 'settings.json',
    ]
    return next((path for path in candidates if path.is_file()), None)


def _write_settings(path, settings):
    staging = path.with_name(f'.{path.name}.{uuid.uuid4().hex}')
    try:
        with staging.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(settings, stream, indent=4)
            stream.write('\n')
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _configure_git_bash(settings, bash):
    profiles = settings.setdefault('profiles', {})
    if not isinstance(profiles, dict):
        raise ValueError("Windows Terminal profiles must be an object.")
    entries = profiles.setdefault('list', [])
    if not isinstance(entries, list):
        raise ValueError("Windows Terminal profiles.list must be an array.")

    commandline = f'"{bash}" --login -i'
    profile = next((entry for entry in entries if isinstance(entry, dict)
                    and (entry.get('guid') == GIT_BASH_GUID
                         or (isinstance(entry.get('commandline'), str)
                             and 'git\\bin\\bash.exe' in entry['commandline'].lower().replace('/', '\\')))), None)
    if profile is None:
        profile = {
            'commandline': commandline,
            'guid': GIT_BASH_GUID,
            'hidden': False,
            'name': 'Git Bash',
            'startingDirectory': '%USERPROFILE%',
        }
        entries.append(profile)
    else:
        profile['commandline'] = commandline
        profile.setdefault('guid', GIT_BASH_GUID)
        profile['hidden'] = False
        profile.setdefault('name', 'Git Bash')
        profile.setdefault('startingDirectory', '%USERPROFILE%')
    settings['defaultProfile'] = profile['guid']


def setup_windows_terminal(config, reporter):
    """Apply the configured default shell to Windows Terminal."""
    try:
        shell = default_shell(config)
        if shell == 'system' or platform.system() != 'Windows':
            return 0
        bash = _git_bash()
        if not bash:
            raise RuntimeError("Git Bash was not found; install Git for Windows and rerun dev init.")
        settings_path = _settings_path()
        if not settings_path:
            raise RuntimeError("Windows Terminal settings were not found; open Windows Terminal and rerun dev init.")
        settings = load_jsonc(settings_path)
        if not isinstance(settings, dict):
            raise ValueError("Windows Terminal settings must be an object.")
        before = json.dumps(settings, sort_keys=True)
        _configure_git_bash(settings, bash)
        if json.dumps(settings, sort_keys=True) != before:
            _write_settings(settings_path, settings)
        reporter.emit_ok(f"Windows Terminal default shell: Git Bash ({settings_path})")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        reporter.emit_error(f"Windows Terminal setup failed: {error}")
        return 1
