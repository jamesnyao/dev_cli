"""Network-free tests for pinned runtime provisioning and launcher boundaries."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import queue
import threading
import time
import unittest
from unittest.mock import patch

import configuration
import runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.scratch.cleanup)
        self.home = Path(self.scratch.name) / 'home with spaces \u00e9!'
        self.root = self.home / '.dev_temp' / 'dev_cli'
        self.home_patch = patch('runtime.Path.home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.pin = {'pythonVersion': '3.12.10'}
        self.python = self.root / 'venvs' / '3.12.10' / (
            'Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def create_python(self):
        self.python.parent.mkdir(parents=True, exist_ok=True)
        self.python.touch()

    def test_exact_pin_validation(self):
        for value in (None, '', 3.12, '3', '3.12', '>=3.12.10', '3.12.10rc1',
                      '3.12.10+debug', ' 3.12.10', '3.012.10', '3.12.010',
                      '../3.12.10', '3.12.10\n', '3.1\u0662.10', '2.7.18', '3.9.20'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.configured_version({'pythonVersion': value})
        self.assertEqual(runtime.configured_version(self.pin), '3.12.10')

    def test_invalid_pin_does_not_install_anything(self):
        with patch('runtime._install_uv') as install, self.assertRaises(ValueError):
            runtime.ensure_python({'pythonVersion': '3.12'})
        install.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_override_uses_existing_jsonc_merger(self):
        sample = self.home / 'dev_config.json'
        override = self.home / 'private.json'
        self.home.mkdir(parents=True)
        sample.write_text('{\n// sample\n"pythonVersion":"3.12.10"\n}', encoding='utf-8')
        override.write_text('{\n/* local */ "pythonVersion":"3.13.2"\n}', encoding='utf-8')
        with patch.object(configuration, '__file__', str(self.home / 'configuration.py')), \
                patch.dict(os.environ, {'DEV_CONFIG_OVERRIDE': str(override)}):
            self.assertEqual(runtime.configured_version(), '3.13.2')

    def test_storage_does_not_follow_override_location(self):
        with patch.dict(os.environ, {'DEV_CONFIG_OVERRIDE': str(self.home / 'other' / 'config.json')}):
            self.assertEqual(runtime.runtime_root(), self.root)

    def test_uv_environment_ignores_caller_overrides(self):
        values = {
            'UV_CONFIG_FILE': 'hostile.toml',
            'UV_PYTHON': 'system-python',
            'UV_SYSTEM_PYTHON': '1',
            'UV_PYTHON_INSTALL_DIR': 'elsewhere',
            'UV_VENV_CLEAR': '1',
            'UV_DEFAULT_INDEX': 'https://example.invalid',
            'INSTALLER_DOWNLOAD_URL': 'https://example.invalid',
            'VIRTUAL_ENV': 'unrelated-env',
            'CONDA_PREFIX': 'conda',
            'PYTHONHOME': 'other-python',
            'PYTHONPATH': 'other-modules',
            'DEV_CONFIG_OVERRIDE': 'keep.json',
        }
        with patch.dict(os.environ, values):
            environment = runtime._uv_environment(self.root)
        for name in values.keys() - {'DEV_CONFIG_OVERRIDE', 'UV_PYTHON_INSTALL_DIR'}:
            self.assertNotIn(name, environment)
        self.assertEqual(environment['UV_PYTHON_INSTALL_DIR'], str(self.root / 'python'))
        self.assertEqual(environment['DEV_CONFIG_OVERRIDE'], 'keep.json')
        self.assertEqual(environment['UV_PYTHON_INSTALL_REGISTRY'], '0')
        self.assertEqual(environment['UV_NO_MODIFY_PATH'], '1')
        self.assertEqual(environment['UV_MANAGED_PYTHON'], '1')
        if os.name == 'nt':
            self.assertEqual(environment['PSModulePath'], str(
                Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules'))

    def test_provisioning_is_private_and_repeatable(self):
        def run_uv(root, *arguments, **kwargs):
            if arguments[0] == 'venv':
                self.create_python()

        with patch('runtime._install_uv') as install, \
                patch('runtime._run_uv', side_effect=run_uv) as uv, \
                patch('runtime._check_environment', return_value=True):
            self.assertEqual(runtime.ensure_python(self.pin), self.python)
            self.assertEqual(runtime.ensure_python(self.pin), self.python)
            self.assertEqual(runtime.python_bin(self.pin), self.python.parent)
        install.assert_called_once_with(self.root)
        self.assertEqual(uv.call_count, 2)
        self.assertEqual(uv.call_args_list[0].args[1:],
                         ('python', 'install', '--no-bin', '--no-registry', '3.12.10'))
        self.assertIn('--managed-python', uv.call_args_list[1].args)
        self.assertNotIn('--seed', uv.call_args_list[1].args)
        if os.name == 'nt':
            self.assertTrue(self.python.with_name('python3.exe').is_file())

    def test_cached_environment_never_needs_uv_or_system_python(self):
        self.create_python()
        with patch('runtime._install_uv') as install, patch('runtime._run_uv') as uv, \
                patch('runtime._check_environment', return_value=True), \
                patch('shutil.which', side_effect=AssertionError('must not look for system Python')):
            self.assertEqual(runtime.ensure_python(self.pin), self.python)
        install.assert_not_called()
        uv.assert_not_called()

    def test_failed_install_is_propagated(self):
        error = subprocess.CalledProcessError(17, ['uv', 'python', 'install'])
        with patch('runtime._install_uv') as install, patch('runtime._run_uv', side_effect=error) as uv, \
                self.assertRaises(subprocess.CalledProcessError) as failure:
            runtime.ensure_python(self.pin)
        self.assertEqual(failure.exception.returncode, 17)
        self.assertFalse(self.python.exists())
        self.assertEqual(uv.call_count, 2)
        self.assertEqual(install.call_count, 2)
        install.assert_called_with(self.root, refresh=True)

    def test_stale_uv_catalog_refreshes_once_for_private_pin(self):
        override = {'pythonVersion': '3.13.2'}
        self.python = self.root / 'venvs/3.13.2' / (
            'Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        attempts = []

        def run_uv(root, *arguments, **kwargs):
            attempts.append(arguments)
            if len(attempts) == 1:
                raise subprocess.CalledProcessError(2, ['uv', 'python', 'install'])
            if arguments[0] == 'venv':
                self.create_python()

        with patch('runtime.configuration.load_config', return_value=override), \
                patch('runtime._install_uv') as install, patch('runtime._run_uv', side_effect=run_uv), \
                patch('runtime._check_environment', return_value=True):
            self.assertEqual(runtime.ensure_python(), self.python)
        self.assertEqual(attempts[:2], [
            ('python', 'install', '--no-bin', '--no-registry', '3.13.2')] * 2)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(install.call_count, 2)
        install.assert_called_with(self.root, refresh=True)
        self.assertEqual(override['pythonVersion'], '3.13.2')

    def test_failed_interpreter_is_propagated(self):
        self.create_python()
        with patch('runtime._check_environment', side_effect=subprocess.CalledProcessError(19, ['python'])), \
                self.assertRaises(subprocess.CalledProcessError):
            runtime.ensure_python(self.pin)

    def test_installer_failure_and_missing_binary_are_not_hidden(self):
        with patch('runtime.subprocess.run', side_effect=subprocess.CalledProcessError(7, ['installer'])), \
                self.assertRaises(subprocess.CalledProcessError) as result:
            runtime._install_uv(self.root)
        self.assertEqual(result.exception.returncode, 7)
        with patch('runtime.subprocess.run'), self.assertRaisesRegex(RuntimeError, 'did not create'):
            runtime._install_uv(self.root)

    def test_cached_uv_is_not_downloaded_again(self):
        uv = runtime._uv_path(self.root)
        uv.parent.mkdir(parents=True)
        uv.touch()
        with patch('runtime.subprocess.run') as run:
            self.assertEqual(runtime._install_uv(self.root), uv)
        run.assert_not_called()

    def test_missing_pip_is_repaired_in_managed_venv(self):
        self.create_python()
        with patch('runtime.subprocess.run') as run, \
                patch('runtime._check_environment', side_effect=[False, True]):
            self.assertEqual(runtime.ensure_python(self.pin), self.python)
        self.assertEqual(run.call_args.args[0], [str(self.python), '-I', '-m', 'ensurepip', '--upgrade'])
        self.assertTrue(run.call_args.kwargs['check'])

    def test_uv_command_has_no_project_or_path_discovery(self):
        with patch('runtime.subprocess.run') as run:
            runtime._run_uv(self.root, 'python', 'find', '--system', '--managed-python', '3.12.10')
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(runtime._uv_path(self.root)))
        self.assertIn('--no-config', command)
        self.assertEqual(run.call_args.kwargs['cwd'], self.root)
        self.assertTrue(run.call_args.kwargs['check'])
        self.assertIs(run.call_args.kwargs['stdout'], sys.stderr)

    def test_interpreter_validation_rejects_wrong_version_or_system_base(self):
        good = {
            'version': '3.12.10', 'prefix': str(self.python.parent.parent),
            'base': str(self.root / 'python' / 'cpython-3.12.10'), 'pip': True,
        }
        for change in ({'version': '3.12.11'}, {'base': str(self.home / 'system-python')},
                       {'prefix': str(self.home / 'other-venv')}):
            result = subprocess.CompletedProcess([], 0, json.dumps({**good, **change}))
            with patch('runtime.subprocess.run', return_value=result), self.assertRaises(RuntimeError):
                runtime._check_environment(self.python, '3.12.10', self.root)
        with patch('runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(good))):
            self.assertTrue(runtime._check_environment(self.python, '3.12.10', self.root))

    def test_update_refreshes_uv_without_mutating_pin(self):
        with patch('runtime._install_uv') as install, \
                patch('runtime.ensure_python', return_value=self.python) as ensure:
            self.assertEqual(runtime.update_python(self.pin), self.python)
        install.assert_called_once_with(self.root, refresh=True)
        ensure.assert_called_once_with(self.pin)
        self.assertEqual(self.pin, {'pythonVersion': '3.12.10'})

    def test_explicit_update_refreshes_before_provisioning_and_dispatch(self):
        for arguments in (['--dev', 'python', 'update'], ['--dev', '--no-color', 'python', 'update']):
            events = []
            with self.subTest(arguments=arguments), \
                    patch('runtime.configuration.load_config', return_value=self.pin), \
                    patch('runtime._install_uv', side_effect=lambda *a, **k: events.append('refresh')) as install, \
                    patch('runtime.ensure_python', side_effect=lambda *a, **k: (
                        events.append('ensure'), self.python)[1]), \
                    patch('runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0)), \
                    patch('runtime.os.execve', side_effect=SystemExit):
                if os.name == 'nt':
                    self.assertEqual(runtime.main(arguments), 0)
                else:
                    with self.assertRaises(SystemExit):
                        runtime.main(arguments)
            self.assertEqual(events, ['refresh', 'ensure'])
            install.assert_called_once_with(self.root, refresh=True)
            self.assertEqual(self.pin['pythonVersion'], '3.12.10')

    def test_failed_explicit_update_never_provisions_or_dispatches(self):
        with patch('runtime.configuration.load_config', return_value=self.pin), \
                patch('runtime._install_uv', side_effect=subprocess.CalledProcessError(19, ['installer'])), \
                patch('runtime.ensure_python') as ensure, patch('runtime.subprocess.run') as run, \
                patch('runtime.os.execve') as execute, self.assertRaises(subprocess.CalledProcessError) as failure:
            runtime.main(['--dev', 'python', 'update'])
        self.assertEqual(failure.exception.returncode, 19)
        ensure.assert_not_called()
        run.assert_not_called()
        execute.assert_not_called()

    def test_python_arguments_and_exit_status(self):
        arguments = ['-c', 'print("hello")', '', 'space and \u00e9!', 'ending\\', '"quoted"']
        with patch('runtime.ensure_python', return_value=self.python), \
                patch('runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 23)) as run, \
                patch('runtime.os.execve', side_effect=SystemExit(23)) as execute:
            if os.name == 'nt':
                self.assertEqual(runtime.main(['--python', *arguments]), 23)
                command = run.call_args.args[0]
            else:
                with self.assertRaises(SystemExit) as result:
                    runtime.main(['--python', *arguments])
                self.assertEqual(result.exception.code, 23)
                command = execute.call_args.args[1]
        self.assertEqual(command, [str(self.python), '-X', 'utf8', *arguments])

    def test_encoded_arguments_preserve_windows_quoting(self):
        arguments = ['--python', '-c', 'print("quoted")', '', 'space \u00e9!', 'ending\\']
        encoded = base64.b64encode(json.dumps(arguments).encode('utf-8')).decode('ascii')
        with patch('runtime.ensure_python', return_value=self.python), \
                patch('runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                patch('runtime.os.execve', side_effect=SystemExit) as execute:
            if os.name == 'nt':
                self.assertEqual(runtime.main(['--encoded-args', encoded]), 0)
                command = run.call_args.args[0]
            else:
                with self.assertRaises(SystemExit):
                    runtime.main(['--encoded-args', encoded])
                command = execute.call_args.args[1]
        self.assertEqual(command, [str(self.python), '-X', 'utf8', *arguments[1:]])

    def test_dev_uses_isolated_interpreter_not_path(self):
        with patch('runtime.ensure_python', return_value=self.python), \
                patch('runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                patch('runtime.os.execve', side_effect=SystemExit) as execute:
            if os.name == 'nt':
                runtime.main(['--dev', 'config', 'get', 'pythonVersion'])
                command = run.call_args.args[0]
            else:
                with self.assertRaises(SystemExit):
                    runtime.main(['--dev', 'config', 'get', 'pythonVersion'])
                command = execute.call_args.args[1]
        self.assertEqual(command[:6], [
            str(self.python), '-X', 'utf8', '-I', str(runtime.SCRIPT_DIR / 'runtime.py'), '--run-dev'])
        self.assertEqual(command[6:], ['config', 'get', 'pythonVersion'])


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name) / 'launchers with spaces \u00e9!'
        self.directory.mkdir()
        self.source = Path(__file__).parent

    @unittest.skipIf(os.name == 'nt', 'native Unix shell test')
    def test_unix_launchers_preserve_arguments_symlinks_and_status(self):
        fixture = self.directory / 'runtime.sh'
        fixture.write_text(
            '#!/bin/bash\nprintf "%s\\0" "$@"\nexit 29\n', encoding='utf-8')
        arguments = ['', 'space and \u00e9!', 'print("quoted")', 'ending\\', '*']
        for name, mode in [('dev', '--dev'), ('python', '--python'), ('python3', '--python')]:
            launcher = self.directory / name
            shutil.copyfile(self.source / name, launcher)
            link = Path(self.scratch.name) / f'{name}-link'
            link.symlink_to(launcher)
            result = subprocess.run(['/bin/bash', str(link), *arguments], capture_output=True, check=False)
            self.assertEqual(result.returncode, 29, result.stderr)
            self.assertEqual(result.stdout.decode().split('\0')[:-1], [mode, *arguments])

    @unittest.skipUnless(os.name == 'nt', 'native Windows shell test')
    def test_powershell_launchers_preserve_arguments_and_status(self):
        fixture = self.directory / 'runtime.ps1'
        fixture.write_text(
            '[Console]::Out.Write((ConvertTo-Json -InputObject @($args) -Compress)); exit 29\n',
            encoding='utf-8')
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        arguments = ['', 'space and \u00e9!', 'print("quoted")', 'ending\\', '*']
        for name, mode in [('dev', '--dev'), ('python', '--python'), ('python3', '--python')]:
            launcher = self.directory / f'{name}.ps1'
            shutil.copyfile(self.source / f'{name}.ps1', launcher)
            result = subprocess.run(
                [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(launcher), *arguments],
                capture_output=True, check=False)
            self.assertEqual(result.returncode, 29, result.stderr)
            self.assertEqual(json.loads(result.stdout), [mode, *arguments])

    @unittest.skipUnless(os.name == 'nt', 'native Windows shell test')
    def test_cmd_launchers_preserve_exclamation_and_status(self):
        (self.directory / 'runtime.ps1').write_text(
            '[Console]::Out.Write((ConvertTo-Json -InputObject @($args) -Compress)); exit 29\n',
            encoding='utf-8')
        for name, mode in [('dev', '--dev'), ('python', '--python'), ('python3', '--python')]:
            for suffix in ('.cmd', '.ps1'):
                shutil.copyfile(self.source / (name + suffix), self.directory / (name + suffix))
            command = f'""{self.directory / (name + ".cmd")}" "hello!" "with space" "" "trailing\\\\" "a\\"b""'
            result = subprocess.run(
                f'"{os.environ["ComSpec"]}" /d /v:off /s /c {command}',
                capture_output=True, check=False)
            self.assertEqual(result.returncode, 29, result.stderr)
            self.assertEqual(json.loads(result.stdout), [mode, 'hello!', 'with space', '', 'trailing\\', 'a"b'])

    @unittest.skipUnless(os.name == 'nt', 'native Windows shell test')
    def test_windows_native_process_preserves_empty_quotes_unicode_and_status(self):
        source = (self.source / 'runtime.ps1').read_text(encoding='utf-8')
        functions = source.split('\n$Interpreter = $null', 1)[0]
        arguments = [
            '-c', 'import json,sys;print(json.dumps(sys.argv[1:]));sys.exit(31)',
            '', 'hello \u00e9!', 'print("quoted")', 'ending\\', '\\"', '*',
        ]
        literals = ','.join("'" + value.replace("'", "''") + "'" for value in arguments)
        executable = sys.executable.replace("'", "''")
        fixture = self.directory / 'native.ps1'
        fixture.write_text(
            functions + f"\n$Result = Invoke-RuntimeProcess '{executable}' @({literals}) -AllowFailure\n"
            'exit $Result\n', encoding='utf-8-sig')
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run(
            [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(fixture)],
            capture_output=True, check=False)
        self.assertEqual(result.returncode, 31, result.stderr)
        self.assertEqual(json.loads(result.stdout), arguments[2:])

    @unittest.skipUnless(os.name == 'nt', 'native Windows shell test')
    def test_windows_dev_output_is_captured_by_powershell_pipeline(self):
        self.create_native_runtime_fixture()
        invocation = self.directory / 'capture.ps1'
        invocation.write_text(
            "$Value = @('pipeline \u00e9!' | & \"$PSScriptRoot\\dev.ps1\" config get 'space \u00e9!' '' '\"quoted\"')\n"
            '$Status = $LASTEXITCODE\n'
            '[Console]::Out.Write((ConvertTo-Json -InputObject @{value=$Value;code=$Status} -Compress))\n',
            encoding='utf-8-sig')
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run(
            [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(invocation)],
            capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['code'], 27)
        self.assertEqual(output['value'][0], 'managed \u00e9 path')
        self.assertEqual(json.loads(output['value'][1]), [
            ['--dev', 'config', 'get', 'space \u00e9!', '', '"quoted"'], 'pipeline \u00e9!\n'])
        self.assertEqual(len(output['value']), 2)
        self.assertIn(b'child diagnostic', result.stderr)

    def create_native_runtime_fixture(self):
        source = (self.source / 'runtime.ps1').read_text(encoding='utf-8')
        executable = sys.executable.replace("'", "''")
        dispatch = source.split('\n$Json = ConvertTo-Json', 1)[1]
        (self.directory / 'runtime.ps1').write_text(
            f"$Interpreter = '{executable}'\n$Json = ConvertTo-Json" + dispatch,
            encoding='utf-8-sig')
        (self.directory / 'runtime.py').write_text(
            'import base64,json,sys\n'
            'args=json.loads(base64.b64decode(sys.argv[-1]))\n'
            'if "prompt" in args:\n'
            '    answer=input("Prompt without newline: ")\n'
            '    print("answer="+answer)\n'
            '    sys.exit(0)\n'
            'print("managed \\u00e9 path")\n'
            'print(json.dumps([args,sys.stdin.read()]))\n'
            'print("child diagnostic",file=sys.stderr)\n'
            'sys.exit(27)\n', encoding='utf-8')
        shutil.copyfile(self.source / 'dev.ps1', self.directory / 'dev.ps1')

    @unittest.skipUnless(os.name == 'nt', 'native Windows shell test')
    def test_windows_no_newline_prompt_is_visible_before_input(self):
        import ctypes
        from ctypes import wintypes

        class StartupInfo(ctypes.Structure):
            _fields_ = [
                ('cb', wintypes.DWORD), ('reserved', wintypes.LPWSTR), ('desktop', wintypes.LPWSTR),
                ('title', wintypes.LPWSTR), ('x', wintypes.DWORD), ('y', wintypes.DWORD),
                ('xsize', wintypes.DWORD), ('ysize', wintypes.DWORD), ('xchars', wintypes.DWORD),
                ('ychars', wintypes.DWORD), ('fill', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('show', wintypes.WORD), ('reserved_size', wintypes.WORD), ('reserved_bytes', ctypes.c_void_p),
                ('stdin', wintypes.HANDLE), ('stdout', wintypes.HANDLE), ('stderr', wintypes.HANDLE)]

        class StartupInfoEx(ctypes.Structure):
            _fields_ = [('startup', StartupInfo), ('attributes', ctypes.c_void_p)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [('process', wintypes.HANDLE), ('thread', wintypes.HANDLE),
                        ('pid', wintypes.DWORD), ('tid', wintypes.DWORD)]

        class Coord(ctypes.Structure):
            _fields_ = [('x', ctypes.c_short), ('y', ctypes.c_short)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        if not hasattr(kernel, 'CreatePseudoConsole'):
            self.skipTest('Windows ConPTY is required for a real interactive-console regression')
        handle_pointer = ctypes.POINTER(wintypes.HANDLE)
        kernel.CreatePipe.argtypes = [handle_pointer, handle_pointer, ctypes.c_void_p, wintypes.DWORD]
        kernel.CreatePseudoConsole.argtypes = [
            Coord, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, handle_pointer]
        kernel.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        kernel.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        kernel.CreateProcessW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
            wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(StartupInfoEx), ctypes.POINTER(ProcessInfo)]
        kernel.ReadFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
        kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]

        self.create_native_runtime_fixture()
        powershell = shutil.which('pwsh') or (
            Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        input_read, input_write, output_read, output_write, console = [wintypes.HANDLE() for _ in range(5)]
        process = ProcessInfo()
        attributes = None
        try:
            self.assertTrue(kernel.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write), None, 0))
            self.assertTrue(kernel.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write), None, 0))
            self.assertEqual(kernel.CreatePseudoConsole(
                Coord(120, 30), input_read, output_write, 0, ctypes.byref(console)), 0)
            size = ctypes.c_size_t()
            kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            self.assertTrue(kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)))
            self.assertTrue(kernel.UpdateProcThreadAttribute(
                attributes, 0, 0x00020016, console, ctypes.sizeof(console), None, None))
            startup = StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.attributes = ctypes.addressof(attributes)
            shell_command = [
                str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(self.directory / 'dev.ps1'), 'prompt']
            # Give the shell console handles, not the test runner's redirected handles.
            bootstrap = (
                'import subprocess,sys; '
                'stdin=open("CONIN$","rb",buffering=0); stdout=open("CONOUT$","wb",buffering=0); '
                f'sys.exit(subprocess.run({shell_command!r},stdin=stdin,stdout=stdout,stderr=stdout).returncode)')
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([
                sys.executable, '-I', '-c', bootstrap]))
            self.assertTrue(kernel.CreateProcessW(
                None, command, None, None, False, 0x00080000, None, str(self.directory),
                ctypes.byref(startup), ctypes.byref(process)), ctypes.get_last_error())
            received = queue.Queue()

            def read_console():
                buffer = ctypes.create_string_buffer(4096)
                count = wintypes.DWORD()
                while kernel.ReadFile(output_read, buffer, len(buffer), ctypes.byref(count), None):
                    if not count.value:
                        break
                    received.put(buffer.raw[:count.value])

            threading.Thread(target=read_console, daemon=True).start()
            output = b''
            deadline = time.monotonic() + 15
            while b'Prompt without newline:' not in output:
                self.assertLess(time.monotonic(), deadline, output)
                try:
                    output += received.get(timeout=0.2)
                except queue.Empty:
                    continue
            answer = b'answer input\r'
            written = wintypes.DWORD()
            self.assertTrue(kernel.WriteFile(input_write, answer, len(answer), ctypes.byref(written), None))
            self.assertEqual(kernel.WaitForSingleObject(process.process, 15000), 0)
            code = wintypes.DWORD()
            self.assertTrue(kernel.GetExitCodeProcess(process.process, ctypes.byref(code)))
            self.assertEqual(code.value, 0, output)
        finally:
            if process.process and kernel.WaitForSingleObject(process.process, 0) != 0:
                kernel.TerminateProcess(process.process, 1)
            if console:
                kernel.ClosePseudoConsole(console)
            if attributes is not None:
                kernel.DeleteProcThreadAttributeList(attributes)
            for handle in (process.thread, process.process, input_read, input_write, output_read, output_write):
                if handle:
                    kernel.CloseHandle(handle)

    @unittest.skipUnless(os.name == 'nt', 'native Windows shell test')
    def test_windows_bootstrap_installer_cache_pin_and_failure(self):
        home = self.directory / 'home'
        managed = home / '.dev_temp/dev_cli/python/fake/python.exe'
        managed.parent.mkdir(parents=True)
        managed.touch()
        source = (self.source / 'runtime.ps1').read_text(encoding='utf-8')
        source = source.replace('function Invoke-RuntimeProcess {', 'function Invoke-RealRuntimeProcess {')
        source = source.split('\n$Json = ConvertTo-Json', 1)[0] + (
            '\nWrite-Output (ConvertTo-Json -InputObject '
            "@(@('-I', (Join-Path $PSScriptRoot 'runtime.py')) + $args) -Compress)\nexit 31\n")
        mock = r'''
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    [Console]::Error.WriteLine('mock download')
    Set-Content -LiteralPath $OutFile -Value '# installer'
}
function Invoke-RuntimeProcess {
    param($File, $Arguments, [switch]$Capture, [switch]$Diagnostic, [switch]$AllowFailure, [switch]$InheritEnvironment)
    if ($File.EndsWith('powershell.exe')) {
        New-Item -ItemType Directory -Force (Split-Path $Uv) | Out-Null
        New-Item -ItemType File -Force $Uv | Out-Null
        Set-Content -LiteralPath "$Uv.current" -Value 'current catalog'
        return
    }
    if ($Arguments -contains 'install' -and ($env:DEV_TEST_UV_FAILURE -or
        ($env:DEV_TEST_STALE_UV -and -not (Test-Path -LiteralPath "$Uv.current")))) {
        [Console]::Error.WriteLine('mock uv failure')
        if ($AllowFailure) {
            return 17
        }
        exit 17
    }
    if ($Capture) {
        return Join-Path $RuntimeRoot 'python\fake\python.exe'
    }
    if ($Arguments[-1] -ne '3.12.10') {
        throw 'unexpected bootstrap pin'
    }
    if ($AllowFailure) {
        return 0
    }
}
'''
        fixture = self.directory / 'runtime.ps1'
        fixture.write_text(mock + source, encoding='utf-8')
        (self.directory / 'dev_config.json').write_text('{\n"pythonVersion": "3.12.10"\n}', encoding='utf-8')
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        command = [str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(fixture), '--python', '-c', 'hello!', '']
        environment = {**os.environ, 'USERPROFILE': str(home)}
        first = subprocess.run(command, env=environment, capture_output=True, check=False)
        self.assertEqual(first.returncode, 31, first.stderr)
        self.assertIn(b'mock download', first.stderr)
        self.assertEqual(json.loads(first.stdout),
                         ['-I', str(self.directory / 'runtime.py'), '--python', '-c', 'hello!', ''])
        second = subprocess.run(command, env=environment, capture_output=True, check=False)
        self.assertEqual(second.returncode, 31, second.stderr)
        self.assertNotIn(b'mock download', second.stderr)
        (home / '.dev_temp/dev_cli/uv/uv.exe.current').unlink()
        stale = subprocess.run(command, env={**environment, 'DEV_TEST_STALE_UV': '1'},
                               capture_output=True, check=False)
        self.assertEqual(stale.returncode, 31, stale.stderr)
        self.assertEqual(stale.stderr.count(b'mock download'), 1)
        self.assertIn(b'retrying once', stale.stderr)
        failed = subprocess.run(command, env={**environment, 'DEV_TEST_UV_FAILURE': '1'},
                                capture_output=True, check=False)
        self.assertEqual(failed.returncode, 17, failed.stderr)
        self.assertEqual(failed.stdout, b'')
        cached = home / '.dev_temp/dev_cli/venvs/3.12.10/Scripts/python.exe'
        cached.parent.mkdir(parents=True)
        cached.touch()
        (home / '.dev_temp/dev_cli/uv/uv.exe').unlink()
        warm = subprocess.run(command, env={**environment, 'DEV_TEST_UV_FAILURE': '1'},
                              capture_output=True, check=False)
        self.assertEqual(warm.returncode, 31, warm.stderr)
        self.assertNotIn(b'mock download', warm.stderr)

    @unittest.skipIf(os.name == 'nt', 'native Unix shell test')
    def test_unix_cold_bootstrap_installer_and_offline_cache_without_python(self):
        tools = self.directory / 'tools'
        tools.mkdir()
        for name in ('dirname', 'sed', 'mkdir', 'rm', 'cp', 'chmod'):
            executable = shutil.which(name)
            self.assertIsNotNone(executable, f'{name} is required for the bootstrap fixture')
            (tools / name).symlink_to(executable)

        def script(name, content):
            path = self.directory / name
            path.write_text(content, encoding='utf-8')
            path.chmod(0o755)
            return path

        script('fake-python', '#!/bin/bash\nprintf "%s\\0" "$@"\nexit 31\n')
        script('fake-uv', r'''#!/bin/bash
set -eu
[[ -z "${VIRTUAL_ENV:-}${UV_CONFIG_FILE:-}${PYTHONHOME:-}${UV_PYTHON:-}" ]] || exit 50
[[ "$*" == *"--no-config"* && "${!#}" == 3.12.10 ]] || exit 51
printf 'uv\n' >> "$DEV_TEST_TRACE"
if [[ "${DEV_TEST_FAILURE:-}" == uv ]]; then exit 17; fi
if [[ "${DEV_TEST_STALE_UV:-}" && ! -f "$UV_UNMANAGED_INSTALL/catalog-current" ]]; then exit 17; fi
case "$*" in
    *"python install"*)
        mkdir -p "$UV_PYTHON_INSTALL_DIR/fake"
        cp "$DEV_TEST_FIXTURES/fake-python" "$UV_PYTHON_INSTALL_DIR/fake/python"
        chmod +x "$UV_PYTHON_INSTALL_DIR/fake/python"
        ;;
    *"python find"*) printf '%s\n' "$UV_PYTHON_INSTALL_DIR/fake/python" ;;
    *) exit 52 ;;
esac
''')
        script('fake-installer', r'''#!/bin/sh
set -eu
test "$UV_NO_MODIFY_PATH" = 1
test -z "${UV_CONFIG_FILE:-}${PYTHONHOME:-}${VIRTUAL_ENV:-}"
printf 'installer\n' >> "$DEV_TEST_TRACE"
if [ "${DEV_TEST_FAILURE:-}" = installer ]; then exit 18; fi
mkdir -p "$UV_UNMANAGED_INSTALL"
cp "$DEV_TEST_FIXTURES/fake-uv" "$UV_UNMANAGED_INSTALL/uv"
chmod +x "$UV_UNMANAGED_INSTALL/uv"
printf current > "$UV_UNMANAGED_INSTALL/catalog-current"
echo 'installer diagnostic'
''')
        curl = script('fake-curl', r'''#!/bin/bash
set -eu
printf 'download\n' >> "$DEV_TEST_TRACE"
if [[ "${DEV_TEST_FAILURE:-}" == download ]]; then exit 19; fi
[[ "$*" == *"https://astral.sh/uv/install.sh"* ]] || exit 53
while [[ "$#" -gt 0 && "$1" != --output ]]; do shift; done
[[ "$#" == 2 ]] || exit 54
cp "$DEV_TEST_FIXTURES/fake-installer" "$2"
''')
        (tools / 'curl').symlink_to(curl)
        fixture = self.directory / 'runtime.sh'
        shutil.copyfile(self.source / 'runtime.sh', fixture)
        (self.directory / 'dev_config.json').write_text(
            '{\n"pythonVersion": "3.12.10"\n}', encoding='utf-8')
        trace = self.directory / 'trace.txt'
        home = self.directory / 'cold home'
        environment = {
            **os.environ, 'HOME': str(home), 'PATH': str(tools),
            'DEV_TEST_FIXTURES': str(self.directory), 'DEV_TEST_TRACE': str(trace),
            'VIRTUAL_ENV': '/unrelated', 'UV_CONFIG_FILE': 'invalid.toml',
            'UV_PYTHON': '/system/python', 'PYTHONHOME': '/other',
        }
        self.assertIsNone(shutil.which('python', path=str(tools)))
        self.assertIsNone(shutil.which('python3', path=str(tools)))
        command = ['/bin/bash', str(fixture), '--python', '', 'space \u00e9!', '"quoted"']
        first = subprocess.run(command, cwd=self.directory, env=environment,
                               capture_output=True, check=False)
        self.assertEqual(first.returncode, 31, first.stderr)
        self.assertEqual(first.stdout.decode().split('\0')[:-1],
                         ['-I', str(self.directory / 'runtime.py'), '--python', '', 'space \u00e9!', '"quoted"'])
        self.assertIn(b'installer diagnostic', first.stderr)
        self.assertEqual(trace.read_text().splitlines(), ['download', 'installer', 'uv', 'uv'])
        root = home / '.dev_temp/dev_cli'
        self.assertFalse(list((root / 'tmp').glob('install-uv-*')))
        cached = root / 'venvs/3.12.10/bin/python'
        cached.parent.mkdir(parents=True)
        shutil.copyfile(self.directory / 'fake-python', cached)
        cached.chmod(0o755)
        shutil.rmtree(root / 'uv')
        before = trace.read_bytes()
        warm = subprocess.run(command, cwd=self.directory,
                              env={**environment, 'DEV_TEST_FAILURE': 'download'},
                              capture_output=True, check=False)
        self.assertEqual(warm.returncode, 31, warm.stderr)
        self.assertEqual(warm.stdout, first.stdout)
        self.assertEqual(warm.stderr, b'')
        self.assertEqual(trace.read_bytes(), before)
        stale_home = self.directory / 'stale home'
        stale_uv = stale_home / '.dev_temp/dev_cli/uv/uv'
        stale_uv.parent.mkdir(parents=True)
        shutil.copyfile(self.directory / 'fake-uv', stale_uv)
        stale_uv.chmod(0o755)
        trace.write_text('', encoding='utf-8')
        stale = subprocess.run(
            command, cwd=self.directory,
            env={**environment, 'HOME': str(stale_home), 'DEV_TEST_STALE_UV': '1'},
            capture_output=True, check=False)
        self.assertEqual(stale.returncode, 31, stale.stderr)
        self.assertEqual(stale.stdout, first.stdout)
        self.assertIn(b'retrying once', stale.stderr)
        self.assertEqual(trace.read_text().splitlines(), ['uv', 'download', 'installer', 'uv', 'uv'])
        for boundary, code, events in (
                ('download', 19, ['download']),
                ('installer', 18, ['download', 'installer']),
                ('uv', 17, ['download', 'installer', 'uv', 'download', 'installer', 'uv'])):
            with self.subTest(boundary=boundary):
                trace.write_text('', encoding='utf-8')
                failed = subprocess.run(
                    command, cwd=self.directory,
                    env={**environment, 'HOME': str(self.directory / boundary), 'DEV_TEST_FAILURE': boundary},
                    capture_output=True, check=False)
                self.assertEqual(failed.returncode, code, failed.stderr)
                self.assertEqual(failed.stdout, b'')
                self.assertEqual(trace.read_text().splitlines(), events)

    @unittest.skipIf(os.name == 'nt', 'native Unix shell test')
    def test_unix_bootstrap_isolated_from_cwd_environment_and_system_python(self):
        home = self.directory / 'home'
        root = home / '.dev_temp/dev_cli'
        uv = root / 'uv/uv'
        uv.parent.mkdir(parents=True)
        interpreter = root / 'python/fake/python'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text('#!/bin/bash\nprintf "%s\\0" "$@"\nexit 31\n', encoding='utf-8')
        interpreter.chmod(0o755)
        uv.write_text(
            '#!/bin/bash\nset -eu\n'
            '[[ -z "${VIRTUAL_ENV:-}${UV_CONFIG_FILE:-}${PYTHONHOME:-}${UV_PYTHON:-}" ]] || exit 50\n'
            '[[ "$*" == *"--no-config"* ]] || exit 51\n'
            'if [[ "${DEV_TEST_UV_FAILURE:-}" ]]; then exit 17; fi\n'
            'case "$*" in\n'
            '  *"python find"*) printf "%s\\n" "$UV_PYTHON_INSTALL_DIR/fake/python";;\n'
            '  *"python install"*) [[ "$*" == *"3.12.10" ]] || exit 52;;\n'
            '  *) exit 53;;\nesac\n', encoding='utf-8')
        uv.chmod(0o755)
        fixture = self.directory / 'runtime.sh'
        shutil.copyfile(self.source / 'runtime.sh', fixture)
        (self.directory / 'dev_config.json').write_text('{\n"pythonVersion": "3.12.10"\n}', encoding='utf-8')
        (self.directory / '.python-version').write_text('3.11.1\n', encoding='utf-8')
        (self.directory / 'uv.toml').write_text('python-preference = "only-system"\n', encoding='utf-8')
        environment = {
            **os.environ, 'HOME': str(home), 'VIRTUAL_ENV': '/unrelated',
            'UV_CONFIG_FILE': 'invalid.toml', 'UV_PYTHON': '/system/python', 'PYTHONHOME': '/other',
        }
        command = ['/bin/bash', str(fixture), '--python', '', 'hello \u00e9!', '"quotes"']
        result = subprocess.run(command, cwd=self.directory, env=environment, capture_output=True, check=False)
        self.assertEqual(result.returncode, 31, result.stderr)
        self.assertEqual(result.stdout.decode().split('\0')[:-1],
                         ['-I', str(self.directory / 'runtime.py'), '--python', '', 'hello \u00e9!', '"quotes"'])
        cached = root / 'venvs/3.12.10/bin/python'
        cached.parent.mkdir(parents=True)
        shutil.copyfile(interpreter, cached)
        cached.chmod(0o755)
        uv.unlink()
        warm = subprocess.run(command, cwd=self.directory, env={**environment, 'DEV_TEST_UV_FAILURE': '1'},
                              capture_output=True, check=False)
        self.assertEqual(warm.returncode, 31, warm.stderr)
        self.assertEqual(warm.stderr, b'')


if __name__ == '__main__':
    unittest.main()
