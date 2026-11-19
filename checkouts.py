"""Resolve related tool and private configuration checkouts through Git."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess


@dataclass(frozen=True)
class CheckoutContext:
    """Working sources and user state have separate owners and lifetimes."""

    tool_root: Path
    primary_root: Path
    private_root: Path
    config_file: Path
    state_dir: Path


@dataclass(frozen=True)
class _Repository:
    primary: Path
    roots: tuple[Path, ...]
    common: Path


def expand_config_path(value, home=None):
    """Expand a configured path without treating a checkout as the OS home."""
    home = Path.home() if home is None else Path(home)
    value = re.sub(r'\$(?:HOME\b|\{HOME\})', lambda _: str(home), value)
    separators = (os.sep,) if os.altsep is None else (os.sep, os.altsep)
    if value == '~' or (value.startswith('~') and value[1:2] in separators):
        value = str(home) + value[1:]
    return os.path.expandvars(os.path.expanduser(value))


def _git(directory, *arguments):
    environment = {key: value for key, value in os.environ.items()
                   if key not in {'GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'}}
    try:
        result = subprocess.run(
            ['git', '-C', str(directory), *arguments], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding='utf-8', errors='surrogateescape',
            env=environment, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _git_path(directory, *arguments):
    value = _git(directory, *arguments)
    if value is None or not value.rstrip('\r\n'):
        return None
    path = Path(value.rstrip('\r\n'))
    return (path if path.is_absolute() else directory / path).resolve()


def _top(directory):
    return _git_path(directory, 'rev-parse', '--show-toplevel')


def _common(directory):
    return _git_path(directory, 'rev-parse', '--git-common-dir')


def _repository(directory):
    if _top(directory) != directory:
        return None
    data = _git(directory, 'worktree', 'list', '--porcelain', '-z')
    common = _common(directory)
    if data is None or common is None:
        raise RuntimeError(f'Cannot read worktree metadata for {directory}')
    roots = []
    record = {}
    for field in data.split('\0'):
        if not field:
            if record and not record.get('bare') and not record.get('prunable'):
                path = Path(record['path']).resolve()
                if path.is_dir():
                    roots.append(path)
            record = {}
        elif field.startswith('worktree '):
            record['path'] = field[9:]
        elif field == 'bare':
            record['bare'] = True
        elif field.startswith('prunable'):
            record['prunable'] = True
    if not roots:
        raise RuntimeError(f'No working checkout is registered for {directory}')
    configured = _git(directory, 'config', '--file', str(common / 'config'),
                      '--path', '--get', 'core.worktree')
    if configured:
        path = Path(configured.rstrip('\r\n'))
        primary = (path if path.is_absolute() else common / path).resolve()
    elif roots[0] == common and _git_path(directory, 'rev-parse', '--absolute-git-dir') == common:
        primary = directory
    else:
        primary = roots[0]
    if _top(primary) != primary or _common(primary) != common:
        raise RuntimeError(f'Cannot resolve the primary checkout for {directory}')
    roots[0] = primary
    return _Repository(primary, tuple(dict.fromkeys(roots)), common)


def _containing(roots, location):
    matches = [root for root in roots if location.is_relative_to(root)]
    return max(matches, key=lambda root: len(root.parts), default=None)


def _related(first, second):
    if _top(second) != second:
        return False
    if _common(first) == _common(second):
        return True
    first_url = _git(first, 'remote', 'get-url', 'origin')
    return bool(first_url and first_url == _git(second, 'remote', 'get-url', 'origin'))


def _submodule(parent, relative):
    entry = _git(parent, 'ls-files', '--stage', '--', relative.as_posix())
    if not entry or len(entry.splitlines()) != 1:
        return False
    fields = entry.split('\t', 1)[0].split()
    return len(fields) == 3 and fields[0] == '160000' and fields[2] == '0'


def _paired_tool(parent, relative, primary):
    candidate = (parent / relative).resolve()
    if not candidate.is_relative_to(parent) or not _submodule(parent, relative):
        return None
    if not (candidate / 'runtime.py').is_file() or not (candidate / 'dev.py').is_file():
        return None
    return candidate if _related(primary, candidate) else None


def resolve_context(script_dir, *, cwd=None, home=None):
    """Select only registered, related worktrees; keep transient state in home."""
    script_dir = Path(script_dir).resolve()
    cwd = Path.cwd().resolve() if cwd is None else Path(cwd).resolve()
    home = Path.home().resolve() if home is None else Path(home).resolve()
    repository = _repository(script_dir)
    primary = repository.primary if repository else script_dir
    private_root = home
    config_directory = primary.parent
    selected = script_dir
    if repository:
        owner = _git_path(primary, 'rev-parse', '--show-superproject-working-tree')
        if owner is None and (primary.parent / 'dev_config.json').is_file():
            owner = primary.parent if _top(primary.parent) == primary.parent else None
        private_repository = _repository(owner) if owner is not None else None
        if private_repository:
            anchor = _containing(private_repository.roots, primary)
            if anchor is None:
                raise RuntimeError(f'Tool checkout {primary} is outside its configuration repository')
            relative_tool = primary.relative_to(anchor)
            relative_config = primary.parent.relative_to(anchor)
            canonical = _paired_tool(private_repository.primary, relative_tool, primary)
            if canonical is not None:
                repository = _repository(canonical)
                primary = repository.primary
            automatic = script_dir == primary
            if automatic:
                selected = _containing(repository.roots, cwd) or primary
            selected_owner = (_containing(private_repository.roots, selected)
                              if selected != primary else None)
            private_root = (selected_owner
                            or (_containing(private_repository.roots, cwd) if automatic else None)
                            or private_repository.primary)
            if _common(private_root) != private_repository.common or _top(private_root) != private_root:
                raise RuntimeError(f'Configuration checkout is no longer valid: {private_root}')
            config_directory = private_root / relative_config
            if automatic and selected == primary:
                selected = _paired_tool(private_root, relative_tool, primary) or selected
        elif script_dir == primary:
            selected = _containing(repository.roots, cwd) or primary
        if selected != script_dir and selected in repository.roots:
            if _top(selected) != selected or _common(selected) != repository.common:
                raise RuntimeError(f'Tool worktree is no longer valid: {selected}')
    override = os.environ.get('DEV_CONFIG_OVERRIDE')
    config_file = Path(expand_config_path(override, home)) if override else config_directory / 'dev_config.json'
    if not config_file.is_absolute():
        config_file = cwd / config_file
    return CheckoutContext(selected, primary, private_root, config_file.resolve(), home / '.dev_temp')
