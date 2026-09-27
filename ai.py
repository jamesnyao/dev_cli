"""Provider-specific AI setup and selected bundled, public skills."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import urllib.request
import uuid


BUNDLED_SKILLS = Path(__file__).parent / 'skills'
COPILOT_INSTALL_URL = 'https://gh.io/copilot-install'
COPILOT_DOCS = 'https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli'


def _copilot_command():
    command = shutil.which('copilot')
    if command:
        return command
    home = Path.home()
    if platform.system() == 'Windows':
        local = Path(os.environ.get('LOCALAPPDATA', home / 'AppData' / 'Local'))
        candidates = [local / 'Microsoft' / 'WinGet' / 'Links' / 'copilot.exe']
        packages = local / 'Microsoft' / 'WinGet' / 'Packages'
        candidates.extend(packages.glob('GitHub.Copilot_*/copilot.exe'))
        if os.environ.get('ProgramFiles'):
            candidates.append(Path(os.environ['ProgramFiles']) / 'WinGet' / 'Links' / 'copilot.exe')
    else:
        candidates = [home / '.local' / 'bin' / 'copilot']
    return next((str(path) for path in candidates if path.is_file()), None)


def _verify_copilot(command):
    result = subprocess.run(
        [command, '--version'], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            f"Copilot CLI is not runnable: {command} --version failed (exit {result.returncode}).")


def _install_copilot():
    system = platform.system()
    if system == 'Windows':
        winget = shutil.which('winget')
        if not winget:
            raise RuntimeError(f"WinGet is required to install Copilot CLI on Windows. See {COPILOT_DOCS}")
        command = [
            winget, 'install', '--id', 'GitHub.Copilot', '--exact', '--source', 'winget',
            '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity',
        ]
        subprocess.run(command, check=True, timeout=600)
    elif system in ('Darwin', 'Linux'):
        bash = shutil.which('bash')
        if not bash or not (shutil.which('curl') or shutil.which('wget')):
            raise RuntimeError("Copilot's native installer requires bash and curl or wget.")
        prefix = Path.home() / '.local'
        # Including the destination on PATH prevents the installer prompting to edit shell profiles.
        env = dict(os.environ, PREFIX=str(prefix),
                   PATH=str(prefix / 'bin') + os.pathsep + os.environ.get('PATH', ''))
        with urllib.request.urlopen(COPILOT_INSTALL_URL, timeout=60) as response:
            script = response.read()
        subprocess.run([bash], input=script, env=env, check=True, timeout=600)
    else:
        raise RuntimeError(f"Copilot CLI installation is unsupported on {system}.")
    command = _copilot_command()
    if not command:
        raise RuntimeError(
            "Copilot installer completed but its executable was not found. "
            "Open a new terminal and rerun dev init.")
    _verify_copilot(command)
    return command


def _write_atomic(path, content):
    staging = path.with_name(f'.{path.name}.{uuid.uuid4().hex}')
    stream = staging.open('xb')
    try:
        with stream:
            stream.write(content)
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _skill_names(options):
    names = options.get('skills', ['dev-cli'])
    if not isinstance(names, list):
        raise ValueError("ai.skills must be a list of bundled skill names.")
    seen = set()
    for name in names:
        if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
            raise ValueError(f"Invalid ai.skills name: {name!r}; use lowercase letters, numbers, and hyphens.")
        if name in seen:
            raise ValueError(f"Duplicate ai.skills name: {name!r}.")
        seen.add(name)
    return names


def _skill_digest(directory):
    digest = hashlib.sha256()
    root = directory.resolve()
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(directory)
        if path.is_symlink() or path.resolve() != root / relative:
            raise ValueError(f"Linked skill resources are not supported: {path}")
        if path.is_file():
            content = hashlib.sha256(path.read_bytes()).hexdigest()
            executable = bool(path.stat().st_mode & 0o111) if os.name != 'nt' else False
        elif path.is_dir():
            content, executable = None, False
        else:
            raise ValueError(f"Unsupported skill resource: {path}")
        record = json.dumps([relative.as_posix(), content, executable]) + '\n'
        digest.update(record.encode('utf-8'))
    return digest.hexdigest()


def _selected_skills(names):
    selected = []
    for name in names:
        source = BUNDLED_SKILLS / name
        if source.is_symlink() or source.resolve() != BUNDLED_SKILLS.resolve() / name \
                or not (source / 'SKILL.md').is_file():
            raise ValueError(f"Unsupported bundled skill: {name!r}; expected skills/{name}/SKILL.md.")
        selected.append((name, source, _skill_digest(source)))
    return selected


def _replace_skill(directory, source, digest):
    staging = directory.with_name(f'.{directory.name}.{uuid.uuid4().hex}')
    backup = directory.with_name(f'{staging.name}.previous')
    staging.mkdir()
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=True)
        if _skill_digest(staging) != digest:
            raise RuntimeError(f"Bundled skill changed during installation: {source}")
        if directory.exists():
            directory.rename(backup)
        try:
            staging.rename(directory)
        except OSError:
            if backup.exists():
                backup.rename(directory)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _install_skill(dev, name, source, digest):
    home = Path.home()
    directory = home / '.copilot' / 'skills' / name
    target = directory / 'SKILL.md'
    metadata = home / '.dev_temp' / 'dev_cli' / 'copilot-skills' / f'{name}.json'
    if directory.is_symlink() or directory.resolve() != directory.absolute() or target.is_symlink():
        dev.emit_warn(f"Preserving linked {name} skill location: {directory}")
        return
    if directory.exists():
        previous = None
        if metadata.is_file() and not metadata.is_symlink():
            try:
                previous = json.loads(metadata.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError):
                dev.emit_warn(f"Invalid skill ownership metadata; preserving {directory}")
                return
        if not isinstance(previous, dict) or previous.get('owner') != 'dev_cli' or not target.is_file():
            dev.emit_warn(f"Preserving existing {name} skill; bundled skill was not installed: {directory}")
            return
        try:
            current = _skill_digest(directory)
        except ValueError:
            dev.emit_warn(f"Preserving customized {name} skill with unsupported resources: {directory}")
            return
        if current != previous.get('sha256'):
            dev.emit_warn(f"Preserving customized {name} skill: {directory}")
            return
        if current == digest:
            dev.emit_ok(f"{name} skill is current: {directory}")
            return
    if metadata.is_symlink():
        raise RuntimeError(f"Refusing linked skill ownership metadata: {metadata}")
    directory.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    _replace_skill(directory, source, digest)
    record = json.dumps({'owner': 'dev_cli', 'sha256': digest}, indent=2) + '\n'
    _write_atomic(metadata, record.encode('utf-8'))
    dev.emit_ok(f"Installed {name} skill: {directory}")


def _setup_ghcopilot(names, dev):
    selected = _selected_skills(names)
    command = _copilot_command()
    if command:
        _verify_copilot(command)
    else:
        dev.emit_info("Installing GitHub Copilot CLI using its native installer.")
        command = _install_copilot()
    dev.emit_ok(f"Copilot CLI is runnable: {command}")
    for name, source, digest in selected:
        _install_skill(dev, name, source, digest)
    dev.emit_info(
        "Authentication was not changed or checked. Run copilot and use /login if sign-in is required.")
    return command


def _setup_claude(names, dev):
    raise NotImplementedError("Claude setup is not implemented yet. Choose ghcopilot or none.")


def _setup_none(names, dev):
    dev.emit_info("AI setup disabled (ai.provider: none).")


PROVIDERS = {
    'ghcopilot': _setup_ghcopilot,
    'claude': _setup_claude,
    'none': _setup_none,
}
DEFAULT_PROVIDER = 'ghcopilot'


def provider_name(config):
    """Validate AI options and resolve the selected provider."""
    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object.")
    options = config.get('ai', {})
    if not isinstance(options, dict) or set(options) - {'provider', 'skills'}:
        raise ValueError('ai must be an object containing only provider and skills.')
    provider = options.get('provider', DEFAULT_PROVIDER)
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError(f"ai.provider must be one of: {', '.join(PROVIDERS)}.")
    _skill_names(options)
    return provider


def setup_ai(config, dev):
    """Run the selected provider's setup, reporting unavailable providers explicitly."""
    try:
        provider = provider_name(config)
        PROVIDERS[provider](_skill_names(config.get('ai', {})), dev)
        return 0
    except (OSError, ValueError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as error:
        dev.emit_error(f"AI setup failed: {error}")
        return 1


def run_chat(config, arguments, workspace, dev):
    """Bootstrap the configured chat CLI and launch it from the workspace root."""
    try:
        provider = provider_name(config)
        if provider == 'none':
            raise ValueError("AI is disabled. Set ai.provider to ghcopilot in dev_config.json.")
        if provider == 'claude':
            raise NotImplementedError("Claude chat is not implemented yet. Choose ghcopilot.")
        workspace = Path(workspace)
        if not workspace.is_dir():
            raise ValueError(f"Workspace root does not exist or is not a directory: {workspace}")
        command = _copilot_command()
        if not command:
            command = _setup_ghcopilot(_skill_names(config.get('ai', {})), dev)
        return subprocess.run(
            [command, '--allow-all', '--add-dir', str(workspace), *arguments],
            cwd=workspace, check=False).returncode
    except (OSError, ValueError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as error:
        dev.emit_error(f"AI chat failed: {error}")
        return 1
    except KeyboardInterrupt:
        return 130
