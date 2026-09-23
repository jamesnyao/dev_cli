"""Private, pinned Python provisioning shared by the CLI and its launchers."""

from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import time


if __name__ == '__main__':
    # -I excludes the script directory; load only this checkout's shared modules.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import configuration  # pylint: disable=wrong-import-position


SCRIPT_DIR = Path(__file__).resolve().parent


def configured_version(config=None):
    """Read the merged configuration and require an exact supported CPython pin."""
    if config is None:
        config = configuration.load_config()
    version = config.get('pythonVersion')
    if not isinstance(version, str) or not re.fullmatch(r'3\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        raise ValueError('pythonVersion must be an exact Python version, for example "3.12.10"')
    if tuple(map(int, version.split('.'))) < (3, 10, 0):
        raise ValueError('pythonVersion must be Python 3.10 or newer')
    return version


def runtime_root():
    """Keep bootstrap storage independent of the private configuration location."""
    return Path.home() / '.dev_temp' / 'dev_cli'


def _uv_environment(root):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(('UV_', 'PYTHON', 'INSTALLER_'))
        and key.upper() not in {'VIRTUAL_ENV', 'CONDA_PREFIX', '__PYVENV_LAUNCHER__'}
    }
    environment.update({
        'UV_UNMANAGED_INSTALL': str(root / 'uv'),
        'UV_NO_MODIFY_PATH': '1',
        'UV_NO_CONFIG': '1',
        'UV_MANAGED_PYTHON': '1',
        'UV_PYTHON_INSTALL_DIR': str(root / 'python'),
        'UV_PYTHON_BIN_DIR': str(root / 'python-bin'),
        'UV_PYTHON_INSTALL_BIN': '0',
        'UV_PYTHON_INSTALL_REGISTRY': '0',
        'UV_CACHE_DIR': str(root / 'cache'),
        'UV_TOOL_DIR': str(root / 'tools'),
        'UV_TOOL_BIN_DIR': str(root / 'tool-bin'),
        'UV_LINK_MODE': 'copy',
        'TMPDIR': str(root / 'tmp'),
        'TMP': str(root / 'tmp'),
        'TEMP': str(root / 'tmp'),
    })
    if os.name == 'nt':
        environment['PSModulePath'] = str(
            Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'Modules')
    return environment


def _uv_path(root):
    return root / 'uv' / ('uv.exe' if os.name == 'nt' else 'uv')


def _install_uv(root, refresh=False):
    uv = _uv_path(root)
    if uv.is_file() and not refresh:
        return uv
    (root / 'tmp').mkdir(parents=True, exist_ok=True)
    option = '--refresh-uv' if refresh else '--install-uv-only'
    if os.name == 'nt':
        powershell = Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        command = [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                   '-File', str(SCRIPT_DIR / 'runtime.ps1'), option]
    else:
        command = ['/bin/bash', str(SCRIPT_DIR / 'runtime.sh'), option]
    subprocess.run(command, check=True, cwd=root, env=_uv_environment(root), stdout=sys.stderr)
    if not uv.is_file():
        raise RuntimeError(f'uv installer did not create {uv}')
    return uv


def _run_uv(root, *arguments, capture=False):
    return subprocess.run(
        [str(_uv_path(root)), '--no-config', '--no-progress', *map(str, arguments)],
        check=True, cwd=root, env=_uv_environment(root),
        stdout=subprocess.PIPE if capture else sys.stderr, text=True, encoding='utf-8')


@contextmanager
def _environment_lock(root, version):
    """Serialize creation so concurrent shell startups cannot see a partial venv."""
    lock = root / f'python-{version}.lock'
    with lock.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt  # pylint: disable=import-error
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b'\0')
                handle.flush()
            deadline = time.monotonic() + 300
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (13, 36) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # pylint: disable=import-error
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _check_environment(interpreter, version, root):
    result = subprocess.run(
        [str(interpreter), '-I', '-c',
         'import json,sys,importlib.util;print(json.dumps({'
         '"version":".".join(map(str,sys.version_info[:3])),'
         '"prefix":sys.prefix,"base":sys.base_prefix,'
         '"pip":importlib.util.find_spec("pip") is not None}))'],
        check=True, stdout=subprocess.PIPE, text=True, encoding='utf-8',
        cwd=root, env=_uv_environment(root))
    details = json.loads(result.stdout)
    if details['version'] != version:
        raise RuntimeError(f'Managed Python at {interpreter} is {details["version"]}, expected {version}')
    if Path(details['prefix']).resolve() != interpreter.parent.parent.resolve():
        raise RuntimeError(f'{interpreter} is not the managed virtual environment')
    if not Path(details['base']).resolve().is_relative_to((root / 'python').resolve()):
        raise RuntimeError(f'{interpreter} does not use the private uv-managed Python')
    return details['pip']


def ensure_python(config=None):
    """Return the pinned, writable venv interpreter, provisioning it if necessary."""
    version = configured_version(config)
    root = runtime_root()
    (root / 'tmp').mkdir(parents=True, exist_ok=True)
    environment = root / 'venvs' / version
    interpreter = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    with _environment_lock(root, version):
        if not interpreter.is_file():
            _install_uv(root)
            try:
                _run_uv(root, 'python', 'install', '--no-bin', '--no-registry', version)
            except subprocess.CalledProcessError:
                print(f'Python {version} installation failed; refreshing private uv and retrying once.',
                      file=sys.stderr)
                _install_uv(root, refresh=True)
                _run_uv(root, 'python', 'install', '--no-bin', '--no-registry', version)
            _run_uv(root, 'venv', '--python', version, '--managed-python',
                    '--allow-existing', environment)
        if not _check_environment(interpreter, version, root):
            subprocess.run(
                [str(interpreter), '-I', '-m', 'ensurepip', '--upgrade'],
                check=True, cwd=root, env=_uv_environment(root), stdout=sys.stderr)
            if not _check_environment(interpreter, version, root):
                raise RuntimeError(f'pip is still missing from {interpreter}')
        if os.name == 'nt':
            alias = interpreter.with_name('python3.exe')
            if not alias.exists():
                shutil.copyfile(interpreter, alias)
    return interpreter


def python_bin(config=None):
    """Return the directory to prepend to PATH, without imposing shell aliases."""
    return ensure_python(config).parent


def update_python(config=None):
    """Refresh private uv and ensure the configured pin; never change the pin."""
    configured_version(config)
    _install_uv(runtime_root(), refresh=True)
    return ensure_python(config)


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    mode = arguments.pop(0) if arguments else '--dev'
    if mode == '--encoded-args':
        if len(arguments) != 1:
            raise ValueError('Expected one encoded argument array')
        decoded = json.loads(base64.b64decode(arguments[0], validate=True).decode('utf-8'))
        if not isinstance(decoded, list) or not all(isinstance(value, str) for value in decoded):
            raise ValueError('Encoded arguments must be an array of strings')
        return main(decoded)
    if mode == '--run-dev':
        sys.path.insert(0, str(SCRIPT_DIR))
        sys.argv = [str(SCRIPT_DIR / 'dev.py'), *arguments]
        runpy.run_path(str(SCRIPT_DIR / 'dev.py'), run_name='__main__')
        return 0
    if mode not in {'--dev', '--python', '--ensure'}:
        raise ValueError(f'Unknown runtime mode: {mode}')
    command_arguments = list(arguments)
    while command_arguments and command_arguments[0] == '--no-color':
        command_arguments.pop(0)
    if mode == '--dev' and command_arguments == ['python', 'update']:
        interpreter = update_python()
    else:
        interpreter = ensure_python()
    if mode == '--ensure':
        return 0
    if mode == '--dev':
        command = [str(interpreter), '-X', 'utf8', '-I', str(SCRIPT_DIR / 'runtime.py'), '--run-dev', *arguments]
    else:
        command = [str(interpreter), '-X', 'utf8', *arguments]
    environment = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
        environment.pop(key, None)
    if os.name != 'nt':
        os.execve(interpreter, command, environment)
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        print(f'Managed Python command failed: {error}', file=sys.stderr)
        sys.exit(error.returncode)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Managed Python: {error}', file=sys.stderr)
        sys.exit(1)
