"""Provider-specific AI setup and the link to bundled, public skills."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import urllib.request


BUNDLED_SKILLS = Path(__file__).parent / 'skills'
MODE_FLAGS = {'--mode', '--autopilot', '--plan'}
MODEL_TIERS = {'auto': 'auto'}
AI_OPTIONS = {'provider', 'skills', 'models', 'defaultModel'}
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


def skills_link():
    """Personal skill directory that Copilot scans and dev init links to the bundled skills."""
    return Path.home() / '.agents' / 'skills'


def _skills_enabled(options):
    value = options.get('skills', True)
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        # Earlier releases selected bundled skills by name; an empty list meant none.
        return bool(value)
    raise ValueError("ai.skills must be true or false.")


def _is_link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _same_path(first, second):
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def _create_directory_link(link, target):
    if os.name == 'nt':
        import _winapi  # pylint: disable=import-outside-toplevel
        # Junctions need neither administrator rights nor Developer Mode.
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


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


def _retire_copied_skills(dev):
    """Remove unedited skill copies installed by earlier releases; the link replaces them."""
    home = Path.home()
    personal = home / '.copilot' / 'skills'
    records = home / '.dev_temp' / 'dev_cli' / 'copilot-skills'
    if not records.is_dir() or _is_link(records):
        return
    for metadata in sorted(records.glob('*.json')):
        directory = personal / metadata.stem
        try:
            record = json.loads(metadata.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            continue
        if not isinstance(record, dict) or record.get('owner') != 'dev_cli':
            continue
        if _is_link(directory):
            continue
        if directory.is_dir():
            try:
                if _skill_digest(directory) != record.get('sha256'):
                    continue
            except ValueError:
                continue
            shutil.rmtree(directory)
            dev.emit_ok(f"Removed copied {metadata.stem} skill; it now loads from {skills_link()}")
        elif directory.exists():
            continue
        metadata.unlink()
    if not any(records.iterdir()):
        records.rmdir()


def _link_skills(dev):
    link = skills_link()
    target = BUNDLED_SKILLS.resolve()
    if _is_link(link):
        if _same_path(link, target):
            dev.emit_ok(f"Bundled skills are linked: {link}")
        else:
            dev.emit_warn(f"Preserving {link}, which links elsewhere; point it at {target} for bundled skills.")
    elif link.exists():
        dev.emit_warn(f"Preserving existing {link}; replace it with a link to {target} for bundled skills.")
    else:
        link.parent.mkdir(parents=True, exist_ok=True)
        _create_directory_link(link, target)
        dev.emit_ok(f"Linked bundled skills: {link} -> {target}")
    personal = Path.home() / '.copilot' / 'skills'
    for source in sorted(target.iterdir()):
        shadow = personal / source.name
        if (source / 'SKILL.md').is_file() and (shadow.exists() or _is_link(shadow)):
            dev.emit_warn(f"Personal skill {shadow} takes precedence over the bundled {source.name} skill.")


def _setup_ghcopilot(skills, dev):
    command = _copilot_command()
    if command:
        _verify_copilot(command)
    else:
        dev.emit_info("Installing GitHub Copilot CLI using its native installer.")
        command = _install_copilot()
    dev.emit_ok(f"Copilot CLI is runnable: {command}")
    if skills:
        _retire_copied_skills(dev)
        _link_skills(dev)
    dev.emit_info(
        "Authentication was not changed or checked. Run copilot and use /login if sign-in is required.")
    return command


def _setup_claude(skills, dev):
    raise NotImplementedError("Claude setup is not implemented yet. Choose ghcopilot or none.")


def _setup_none(skills, dev):
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
    if not isinstance(options, dict) or set(options) - AI_OPTIONS:
        raise ValueError(f"ai must be an object containing only {', '.join(sorted(AI_OPTIONS))}.")
    provider = options.get('provider', DEFAULT_PROVIDER)
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError(f"ai.provider must be one of: {', '.join(PROVIDERS)}.")
    _skills_enabled(options)
    _model_tiers(options)
    return provider


def _model_tiers(options):
    """Return the tier-to-model map (built-ins plus ai.models) and the default tier."""
    configured = options.get('models', {})
    if not isinstance(configured, dict) or not all(
            isinstance(model, str) and model for model in configured.values()):
        raise ValueError('ai.models must map tier names to model names.')
    tiers = {**MODEL_TIERS, **configured}
    default = options.get('defaultModel')
    if default is not None and default not in tiers:
        raise ValueError(f"ai.defaultModel must be one of: {', '.join(tiers)}.")
    return tiers, default


def _model_arguments(options, arguments):
    """Replace a leading tier name with --model, falling back to ai.defaultModel."""
    tiers, tier = _model_tiers(options)
    explicit = any(arg == '--model' or arg.startswith('--model=') for arg in arguments)
    if arguments and arguments[0] in tiers:
        if explicit:
            raise ValueError(f"Choose either the '{arguments[0]}' tier or --model, not both.")
        tier, arguments = arguments[0], arguments[1:]
    elif explicit:
        tier = None
    return ([] if tier is None else ['--model', tiers[tier]]), arguments


def setup_ai(config, dev):
    """Run the selected provider's setup, reporting unavailable providers explicitly."""
    try:
        provider = provider_name(config)
        PROVIDERS[provider](_skills_enabled(config.get('ai', {})), dev)
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
        model, arguments = _model_arguments(config.get('ai', {}), list(arguments))
        command = _copilot_command()
        if not command:
            command = _setup_ghcopilot(_skills_enabled(config.get('ai', {})), dev)
        mode = [] if any(arg in MODE_FLAGS or arg.startswith('--mode=') for arg in arguments) \
            else ['--mode', 'autopilot']
        return subprocess.run(
            [command, '--allow-all', *mode, *model, '--add-dir', str(workspace), *arguments],
            cwd=workspace, check=False).returncode
    except (OSError, ValueError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as error:
        dev.emit_error(f"AI chat failed: {error}")
        return 1
    except KeyboardInterrupt:
        return 130
