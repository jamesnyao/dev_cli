"""Named shell environments applied by `dev set <name>`.

A child process cannot change its parent shell, so `dev set <name> --shell X`
prints code that the `dev` shell function evaluates. Every switch rebuilds PATH
from DEV_BASE_PATH (captured once per shell) and unsets the variables the
previous environment exported, so switching never accumulates state.
"""

import os
import re

from configuration import expand_config_path

SHELLS = ('pwsh', 'bash', 'zsh')
_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _expand(value, workspace):
    previous = os.environ.get('DEV')
    os.environ['DEV'] = str(workspace)
    try:
        return expand_config_path(value)
    finally:
        if previous is None:
            os.environ.pop('DEV', None)
        else:
            os.environ['DEV'] = previous


def _path(value, workspace):
    return os.path.normpath(_expand(value, workspace))


def resolve(environment, workspace):
    """Expand an environment entry into concrete PATH entries, variables, and cwd."""
    if not isinstance(environment, dict):
        raise ValueError('environment must be an object')
    paths = environment.get('path', [])
    if isinstance(paths, str) or not all(isinstance(p, str) for p in paths):
        raise ValueError('"path" must be a list of strings')
    variables = {}
    for name, value in (environment.get('env') or {}).items():
        if not _NAME.match(name) or name in ('PATH', 'DEV_BASE_PATH', 'DEV_SET_VARS', 'DEV_ENV'):
            raise ValueError(f'invalid variable name {name!r}')
        if value is None:
            variables[name] = None
        elif isinstance(value, str):
            expanded = _expand(value, workspace)
            variables[name] = os.path.normpath(expanded) if os.path.isabs(expanded) else expanded
        else:
            raise ValueError(f'value for {name} must be a string or null')
    cwd = environment.get('cwd')
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError('"cwd" must be a string')
    return {
        'path': [_path(p, workspace) for p in paths],
        'env': variables,
        'cwd': _path(cwd, workspace) if cwd else None,
    }


def posix_path(value):
    """Git Bash on Windows uses /c/dir paths with ':' separators."""
    match = re.match(r'^([A-Za-z]):[\\/]?(.*)$', value)
    if not match:
        return value.replace('\\', '/')
    rest = match.group(2).replace('\\', '/')
    return f'/{match.group(1).lower()}/{rest}'.rstrip('/') or '/'


def pwsh_quote(value):
    return "'" + value.replace("'", "''") + "'"


def sh_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def render(name, resolved, shell, windows=None):
    """Return shell code that applies ``resolved`` in the calling shell."""
    if windows is None:
        windows = os.name == 'nt'
    exported = ' '.join(resolved['env'])
    if shell == 'pwsh':
        lines = [
            'if (-not $env:DEV_BASE_PATH) { $env:DEV_BASE_PATH = $env:PATH }',
            'foreach ($DevSetVar in ("$env:DEV_SET_VARS" -split " " | Where-Object { $_ })) '
            '{ Remove-Item -LiteralPath "Env:$DevSetVar" -ErrorAction SilentlyContinue }',
        ]
        prefix = ''.join(p + os.pathsep for p in resolved['path'])
        lines.append(f'$env:PATH = {pwsh_quote(prefix)} + $env:DEV_BASE_PATH')
        for variable, value in resolved['env'].items():
            if value is None or value == '':
                lines.append(f'Remove-Item -LiteralPath Env:{variable} -ErrorAction SilentlyContinue')
            else:
                lines.append(f'$env:{variable} = {pwsh_quote(value)}')
        lines.append(f'$env:DEV_SET_VARS = {pwsh_quote(exported)}')
        lines.append(f'$env:DEV_ENV = {pwsh_quote(name)}')
        if resolved['cwd']:
            cwd = pwsh_quote(resolved['cwd'])
            lines.append(f'if (Test-Path -LiteralPath {cwd} -PathType Container) {{ Set-Location -LiteralPath {cwd} }} '
                         f'else {{ Write-Warning ("Directory does not exist: " + {cwd}) }}')
        return '\n'.join(lines) + '\n'
    if shell not in SHELLS:
        raise ValueError(f'unsupported shell {shell!r}')
    convert = posix_path if windows else (lambda value: value)
    lines = [
        'if [ -z "${DEV_BASE_PATH:-}" ]; then export DEV_BASE_PATH="$PATH"; fi',
        'for dev_set_var in $(printf "%s" "${DEV_SET_VARS:-}"); do unset "$dev_set_var"; done; unset dev_set_var',
    ]
    prefix = ''.join(convert(p) + ':' for p in resolved['path'])
    lines.append(f'export PATH={sh_quote(prefix)}"$DEV_BASE_PATH"')
    for variable, value in resolved['env'].items():
        if value is None:
            lines.append(f'unset {variable}')
        else:
            lines.append(f'export {variable}={sh_quote(value)}')
    lines.append(f'export DEV_SET_VARS={sh_quote(exported)}')
    lines.append(f'export DEV_ENV={sh_quote(name)}')
    if resolved['cwd']:
        cwd = sh_quote(convert(resolved['cwd']))
        lines.append(f'if [ -d {cwd} ]; then builtin cd -- {cwd}; '
                     f'else printf "WARNING: Directory does not exist: %s\\n" {cwd} >&2; fi')
    return '\n'.join(lines) + '\n'
