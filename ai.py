"""Provider-specific AI setup and the link to bundled, public skills."""

import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import urllib.request

import environments
from configuration import load_jsonc


BUNDLED_SKILLS = Path(__file__).parent / 'skills'
MODE_FLAGS = {'--mode', '--autopilot', '--plan'}
MODEL_TIERS = {'auto': 'auto'}
AI_OPTIONS = {'provider', 'skills', 'models', 'defaultModel', 'continueModel', 'profiles'}
PROFILE_OPTIONS = {'command', 'addWorkspace', 'defaultModel', 'env', 'flags'}
DEFAULT_PROFILE = 'default'
PROFILE_NAME = re.compile(r'^[A-Za-z0-9_-]+$')
GITHUB_SHORTHAND = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
FLAG = re.compile(r'^(-[A-Za-z0-9]|--[A-Za-z0-9][A-Za-z0-9_-]*)$')
# Leading flags that dev ai or common Copilot usage already gives a meaning.
RESERVED_FLAGS = {'--profile', '--add-env', '--add-flag', '--model', '--mode', '--plan', '--autopilot', '--continue',
                  '--resume', '-p', '--prompt', '-i', '--interactive', '-h', '--help', '-v', '--version',
                  '--allow-all', '--add-dir', '--agent'}
CONTINUE_FLAGS = {'-c', '--continue'}
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
        import _winapi  # pylint: disable=import-error
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
    for skill in sorted(profiles_root().glob('*/skills/*/SKILL.md')):
        if (target / skill.parent.name / 'SKILL.md').is_file():
            dev.emit_warn(f"Bundled {skill.parent.name} skill hides {skill.parent}; rename the profile skill.")


def profiles_root():
    """Directory holding one tracked configuration folder per AI profile."""
    return Path.home() / 'dev_ai'


def plugin_cache():
    """Ignored directory holding git checkouts of profile plugins."""
    return Path.home() / '.dev_temp' / 'dev_cli' / 'plugins'


def _plugin_specs(directory, workspace):
    """Validate a profile's plugins.json, returning (local path) or (url, checkout, subpath) entries."""
    manifest = directory / 'plugins.json'
    if not manifest.is_file():
        return []
    data = load_jsonc(manifest)
    plugins = data.get('plugins') if isinstance(data, dict) else None
    if not isinstance(plugins, list):
        raise ValueError(f'{manifest} must contain {{"plugins": [...]}}.')
    specs = []
    for entry in plugins:
        if isinstance(entry, str) and entry:
            specs.append((Path(_expand(entry, workspace)),))
        elif isinstance(entry, dict) and set(entry) <= {'repo', 'path'} and isinstance(entry.get('repo'), str) \
                and isinstance(entry.get('path', ''), str):
            url = entry['repo']
            if GITHUB_SHORTHAND.match(url):
                url = f'https://github.com/{url}.git'
            parts = [part for part in re.split(r'[:/\\@]+', re.sub(r'^[a-z+]+://', '', url)) if part]
            raw = entry.get('path', '')
            relative = PurePosixPath(raw.replace('\\', '/'))
            if not parts or any(part in ('.', '..') for part in parts) or relative.is_absolute() \
                    or '..' in relative.parts or ':' in raw:
                raise ValueError(f'{manifest}: invalid plugin {entry!r}.')
            subpath = Path(*relative.parts)
            checkout = plugin_cache().joinpath(*parts).with_suffix('') if parts[-1].endswith('.git') \
                else plugin_cache().joinpath(*parts)
            specs.append((url, checkout, subpath))
        else:
            raise ValueError(f'{manifest}: each plugin must be a local path or {{"repo": ..., "path": ...}}.')
    return specs


def _update_checkout(url, checkout, dev):
    if not (checkout / '.git').is_dir():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        dev.emit_info(f"Cloning plugin repository {url}")
        subprocess.run(['git', 'clone', '--depth', '1', url, str(checkout)], check=True, timeout=600)
    else:
        subprocess.run(['git', '-C', str(checkout), 'fetch', '--depth', '1', 'origin', 'HEAD'],
                       check=True, timeout=600)
        subprocess.run(['git', '-C', str(checkout), 'reset', '--hard', '--quiet', 'FETCH_HEAD'],
                       check=True, timeout=60)


def plugin_dirs(directory, workspace, dev, update=False):
    """Resolve a profile's plugin folders, cloning missing git sources and updating them when asked."""
    folders, updated = [], set()
    for spec in _plugin_specs(directory, workspace):
        if len(spec) == 3:
            url, checkout, subpath = spec
            if checkout not in updated and (update or not (checkout / '.git').is_dir()):
                _update_checkout(url, checkout, dev)
                updated.add(checkout)
            folder = checkout / subpath
        else:
            folder = spec[0]
        if not folder.is_dir():
            raise ValueError(f'Plugin folder does not exist: {folder} (from {directory / "plugins.json"}).')
        folders.append(folder)
    return folders


def update_plugins(config, dev):
    """Update every profile's git plugin sources; used by dev init and dev repo sync."""
    root = profiles_root()
    for directory in sorted(root.iterdir() if root.is_dir() else []):
        if directory.is_dir() and PROFILE_NAME.match(directory.name) and (directory / 'plugins.json').is_file():
            for folder in plugin_dirs(directory, dev.get_base_path(config), dev, update=True):
                dev.emit_ok(f"{directory.name} plugin ready: {folder}")


def _profile_env(name, directory, profile, workspace):
    env = dict(os.environ, DEV_AI_PROFILE=name,
               COPILOT_SKILLS_DIRS=str(directory / 'skills'),
               COPILOT_CUSTOM_INSTRUCTIONS_DIRS=str(directory))
    for variable, value in environments.resolve({'env': profile.get('env') or {}}, workspace)['env'].items():
        if value is None:
            env.pop(variable, None)
        else:
            env[variable] = value
    return env


def _profiles(options):
    """Validate and return ai.profiles."""
    profiles = options.get('profiles', {})
    if not isinstance(profiles, dict):
        raise ValueError('ai.profiles must map profile names to objects.')
    tiers = _model_tiers(options)[0]
    owners = {}
    for name, profile in profiles.items():
        where = f'ai.profiles.{name}'
        if not PROFILE_NAME.match(name):
            raise ValueError(f'{where}: profile names may contain only letters, digits, "-" and "_".')
        if not isinstance(profile, dict) or set(profile) - PROFILE_OPTIONS:
            raise ValueError(f"{where} must be an object containing only {', '.join(sorted(PROFILE_OPTIONS))}.")
        if not isinstance(profile.get('command', ''), str):
            raise ValueError(f'{where}.command must be a string.')
        if not isinstance(profile.get('addWorkspace', True), bool):
            raise ValueError(f'{where}.addWorkspace must be true or false.')
        if 'defaultModel' in profile and profile['defaultModel'] not in tiers:
            raise ValueError(f"{where}.defaultModel must be one of: {', '.join(tiers)}.")
        environments.resolve({'env': profile.get('env') or {}}, Path.home())
        flags = profile.get('flags', [])
        if not isinstance(flags, list) or not all(isinstance(flag, str) and FLAG.match(flag) for flag in flags):
            raise ValueError(f'{where}.flags must be a list like ["-x", "--name"].')
        for flag in flags:
            if flag in RESERVED_FLAGS:
                raise ValueError(f'{where} cannot use {flag}, which dev ai or Copilot already uses.')
        for flag in flags:
            if flag in owners:
                raise ValueError(f'{where} and ai.profiles.{owners[flag]} both use {flag}.')
            owners[flag] = name
    return profiles


def _profile_flags(profiles):
    return {flag: name for name, profile in profiles.items() for flag in profile.get('flags', [])}


def _expand(value, workspace):
    return environments.resolve({'cwd': value}, workspace)['cwd']


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
    _profiles(options)
    return provider


def _model_tiers(options):
    """Return the tier-to-model map (built-ins plus ai.models), the default tier, and the continue tier."""
    configured = options.get('models', {})
    if not isinstance(configured, dict) or not all(
            isinstance(model, str) and model for model in configured.values()):
        raise ValueError('ai.models must map tier names to model names.')
    tiers = {**MODEL_TIERS, **configured}
    for option in ('defaultModel', 'continueModel'):
        tier = options.get(option)
        if tier is not None and tier not in tiers:
            raise ValueError(f"ai.{option} must be one of: {', '.join(tiers)}.")
    default = options.get('defaultModel')
    return tiers, default, options.get('continueModel', default)


def _model_arguments(options, arguments):
    """Replace a leading tier name with --model, falling back to ai.continueModel or ai.defaultModel."""
    tiers, tier, resume = _model_tiers(options)
    explicit = any(arg == '--model' or arg.startswith('--model=') for arg in arguments)
    if arguments and arguments[0] in tiers:
        if explicit:
            raise ValueError(f"Choose either the '{arguments[0]}' tier or --model, not both.")
        tier, arguments = arguments[0], arguments[1:]
    elif explicit:
        tier = None
    elif arguments and arguments[0] in CONTINUE_FLAGS:
        tier = resume
    return ([] if tier is None else ['--model', tiers[tier]]), arguments


def setup_ai(config, dev):
    """Run the selected provider's setup, reporting unavailable providers explicitly."""
    try:
        provider = provider_name(config)
        PROVIDERS[provider](_skills_enabled(config.get('ai', {})), dev)
        if provider == 'ghcopilot' and _skills_enabled(config.get('ai', {})):
            update_plugins(config, dev)
        return 0
    except (OSError, ValueError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as error:
        dev.emit_error(f"AI setup failed: {error}")
        return 1


def _profile_argument(arguments, profiles):
    """Split a leading profile selector (--profile NAME, --profile=NAME, or a profile flag) from the arguments."""
    if arguments[:1] == ['--profile']:
        if len(arguments) < 2:
            raise ValueError('--profile requires a profile name.')
        return arguments[1], arguments[2:]
    if arguments and arguments[0].startswith('--profile='):
        return arguments[0].split('=', 1)[1], arguments[1:]
    flags = _profile_flags(profiles)
    if arguments and arguments[0] in flags:
        return flags[arguments[0]], arguments[1:]
    return None, arguments


def _add_arguments(arguments):
    """Parse `--add-env [NAME] [--add-flag FLAG]...`."""
    name, flags, rest = None, [], arguments[1:]
    while rest:
        if rest[0] == '--add-flag':
            if len(rest) < 2:
                raise ValueError('--add-flag requires a flag such as -x.')
            flags.append(rest[1])
            rest = rest[2:]
        elif name is None and not rest[0].startswith('-'):
            name, rest = rest[0], rest[1:]
        else:
            raise ValueError(f'Unexpected argument {rest[0]!r}; use: ai --add-env NAME [--add-flag -x].')
    return name, flags


def add_profile(config, arguments, dev):
    """Register a profile with --NAME and any extra flags, and create its configuration folder."""
    name, flags = _add_arguments(arguments)
    if name is None:
        if not sys.stdin.isatty():
            raise ValueError('Profile name required: ai --add-env NAME [--add-flag -x].')
        try:
            name = input('Profile name (also usable as --NAME): ').strip()
            short = input('Extra flag, e.g. -x (Enter for none): ').strip()
        except EOFError as error:
            raise ValueError('Profile name required: ai --add-env NAME [--add-flag -x].') from error
        flags += [short] if short else []
    if name == DEFAULT_PROFILE or not PROFILE_NAME.match(name):
        raise ValueError(f'Invalid profile name {name!r}; use letters, digits, "-" and "_", not "default".')
    override = load_jsonc(dev.OVERRIDE_CONFIG_FILE) if dev.OVERRIDE_CONFIG_FILE.is_file() else {}
    profiles = {**config.get('ai', {}).get('profiles', {}), **override.get('ai', {}).get('profiles', {})}
    profile = dict(profiles.get(name, {}))
    wanted = list(dict.fromkeys([*profile.get('flags', []), f'--{name}', *flags]))
    profile['flags'] = wanted
    candidate = {**config.get('ai', {}), 'profiles': {**profiles, name: profile}}
    _profiles(candidate)
    for flag in wanted:
        if flag in CONTINUE_FLAGS:
            dev.emit_warn(f"{flag} now selects the {name} profile; use --continue to continue a session.")
    override['ai'] = {**override.get('ai', {}),
                      'profiles': {**override.get('ai', {}).get('profiles', {}), name: profile}}
    dev.save_config(override)
    directory = profiles_root() / name
    (directory / 'skills').mkdir(parents=True, exist_ok=True)
    (directory / '.github' / 'instructions').mkdir(parents=True, exist_ok=True)
    dev.emit_ok(f"Profile {name} uses {directory}; launch it with {' or '.join('ai ' + flag for flag in wanted)}.")
    return 0


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
        options = config.get('ai', {})
        profiles = _profiles(options)
        if arguments[:1] == ['--add-env']:
            return add_profile(config, list(arguments), dev)
        requested, arguments = _profile_argument(list(arguments), profiles)
        name = DEFAULT_PROFILE if requested is None else requested
        directory = profiles_root() / name
        if requested is not None and (not PROFILE_NAME.match(name)
                                      or (name not in profiles and not directory.is_dir())):
            raise ValueError(f"Unknown AI profile {name!r}; add it with ai --add-env {name}.")
        profile = profiles.get(name, {})
        launch, plugins = {}, []
        if directory.is_dir() or name in profiles:
            launch['env'] = _profile_env(name, directory, profile, workspace)
            for folder in plugin_dirs(directory, workspace, dev):
                plugins += ['--plugin-dir', str(folder)]
        if 'defaultModel' in profile:
            options = {**options, 'defaultModel': profile['defaultModel']}
        model, arguments = _model_arguments(options, arguments)
        if arguments and arguments[0] == '-c':
            arguments[0] = '--continue'
        if 'command' in profile:
            command = _expand(profile['command'], workspace)
        else:
            command = _copilot_command()
            if not command:
                command = _setup_ghcopilot(_skills_enabled(options), dev)
        mode = [] if any(arg in MODE_FLAGS or arg.startswith('--mode=') for arg in arguments) \
            else ['--mode', 'interactive']
        add_dir = ['--add-dir', str(workspace)] if profile.get('addWorkspace', True) else []
        return subprocess.run(
            [command, '--allow-all', *mode, *model, *add_dir, *plugins, *arguments],
            cwd=workspace, check=False, **launch).returncode
    except (OSError, ValueError, RuntimeError, http.client.HTTPException, subprocess.SubprocessError) as error:
        dev.emit_error(f"AI chat failed: {error}")
        return 1
    except KeyboardInterrupt:
        return 130
