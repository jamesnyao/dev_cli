#!/usr/bin/env python3
"""
Dev CLI - Cross-platform development workflow tool
"""

import argparse
import base64
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path

# Ensure stdout/stderr can print unicode (e.g. arrows, checkmarks) on Windows
# consoles where the default codec is cp1252.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

__version__ = '2.0.0'


# Colors (ANSI escape codes). The enabled state is resolved once at startup from
# NO_COLOR/FORCE_COLOR, the host console, and whether stdout is a TTY, and can be
# forced off at runtime via `--no-color`.
class Colors:
    RED = YELLOW = GREEN = BLUE = CYAN = PURPLE = GREY = NC = ''
    _PALETTE = {
        'RED': '\033[0;31m',
        'YELLOW': '\033[1;33m',
        'GREEN': '\033[0;32m',
        'BLUE': '\033[0;34m',
        'CYAN': '\033[0;36m',
        'PURPLE': '\033[0;35m',
        'GREY': '\033[0;90m',
        'NC': '\033[0m',
    }

    @classmethod
    def configure(cls, enabled):
        """Enable or disable ANSI color output across every helper."""
        for name, code in cls._PALETTE.items():
            setattr(cls, name, code if enabled else '')


def _color_default_enabled():
    """Decide whether ANSI colors are on by default for this process."""
    if os.environ.get('NO_COLOR') is not None:
        return False
    if os.environ.get('FORCE_COLOR'):
        return True
    # Legacy Windows consoles (cmd.exe / PowerShell 5) lack VT processing unless
    # running under Windows Terminal.
    if sys.platform == 'win32' and 'WT_SESSION' not in os.environ:
        return False
    # Suppress escape codes when stdout is redirected or piped.
    return bool(getattr(sys.stdout, 'isatty', lambda: False)())


Colors.configure(_color_default_enabled())


def _emit(tag, color, msg, stream):
    """Print a `[TAG] message` status line with a colored marker."""
    print(f"{color}[{tag}]{Colors.NC} {msg}", file=stream)


def emit_ok(msg):
    """Report a successful step to stdout ([OK])."""
    _emit('OK', Colors.GREEN, msg, sys.stdout)


def emit_info(msg):
    """Report neutral progress to stdout ([INFO])."""
    _emit('INFO', Colors.BLUE, msg, sys.stdout)


def emit_skip(msg):
    """Report a skipped step to stdout ([SKIP])."""
    _emit('SKIP', Colors.GREY, msg, sys.stdout)


def emit_warn(msg):
    """Report a non-fatal warning to stderr ([WARN])."""
    _emit('WARN', Colors.YELLOW, msg, sys.stderr)


def emit_error(msg):
    """Report a failure to stderr ([X])."""
    _emit('X', Colors.RED, msg, sys.stderr)


def confirm(prompt, default_yes=True, assume_yes=False):
    """Ask a yes/no question and return the answer.

    ``assume_yes`` answers 'y' without prompting (``--force``); the prompt is
    still echoed so the transcript shows what was auto-answered. An empty answer
    takes ``default_yes``; a closed stdin is always a no.
    """
    if assume_yes:
        print(f"{prompt}y")
        return True
    try:
        response = input(prompt).strip().lower()
    except EOFError:
        return False
    if not response:
        return default_yes
    return response == 'y'


SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = SCRIPT_DIR / 'repoconfig'
CONFIG_FILE = CONFIG_DIR / 'repos.json'
ADO_PAT_FILE = CONFIG_DIR / 'ado_pat.txt'
ADO_TOKEN_CACHE_FILE = CONFIG_DIR / 'ado_token_cache.json'
ADO_TOKEN_CACHE_SECONDS = 2400  # 40 minute fallback when JWT exp can't be parsed
ADO_TOKEN_EXPIRY_BUFFER = 60  # Treat token as expired this many seconds before its real exp
ADO_API_VERSION = '7.1'  # ADO REST API version used for all requests
RCFILES_DIR = CONFIG_DIR / 'rcfiles'

def get_os_type():
    system = platform.system().lower()
    if system == 'darwin':
        return 'darwin'
    elif system == 'windows':
        return 'windows'
    return 'linux'

def load_config():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for repo in config.get('repos', []):
        if 'skipOn' in repo:
            repo['skipOn'] = sorted(repo['skipOn'])
    config['repos'] = sorted(config.get('repos', []), key=lambda r: r.get('path', r.get('name', '')))
    config['files'] = sorted(config.get('files', []), key=lambda f: f.get('path', ''))
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, sort_keys=True)

def get_base_path(config=None):
    if config is None:
        config = load_config()
    devconfig = os.getenv('DEVCONFIG', '')
    roots = config.get('workspaceRoots', {})
    if devconfig and devconfig in roots:
        return os.path.expandvars(roots[devconfig])
    raise ValueError(f'No workspaceRoot found for DEVCONFIG={devconfig!r}. Check {CONFIG_FILE} workspaceRoots.')

def run_git(repo_path, *args):
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_path)] + list(args),
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception:
        return False, ''

GITHUB_SSH_HOSTS = {
    'example-user': 'github.com-personal',
    'acme-corp': 'github.com-work',
}

def normalize_github_url(url):
    """Convert GitHub URLs to SSH format with correct host alias."""
    https_match = re.match(r'https://github\.com/([^/]+)/(.+?)(?:\.git)?$', url)
    if https_match:
        org, repo = https_match.groups()
        host = GITHUB_SSH_HOSTS.get(org, 'github.com')
        return f'git@{host}:{org}/{repo}.git'
    ssh_match = re.match(r'git@github\.com:([^/]+)/(.+?)(?:\.git)?$', url)
    if ssh_match:
        org, repo = ssh_match.groups()
        host = GITHUB_SSH_HOSTS.get(org, 'github.com')
        return f'git@{host}:{org}/{repo}.git'
    return url

def _strip_url_credentials(url):
    """Strip embedded credentials from a URL for comparison."""
    return re.sub(r'https://[^@]+@', 'https://', url)

def _normalize_url_for_comparison(url):
    """Normalize a URL for comparison: strip credentials and resolve GitHub SSH aliases."""
    url = _strip_url_credentials(url)
    # Normalize github.com-* aliases back to github.com
    url = re.sub(r'git@github\.com-[^:]+:', 'git@github.com:', url)
    return url.rstrip('/').removesuffix('.git')

def get_remote_url(repo_path, normalize=False):
    success, url = run_git(repo_path, 'remote', 'get-url', 'origin')
    if success and normalize:
        return normalize_github_url(url)
    return url if success else ''

def get_current_branch(repo_path):
    success, branch = run_git(repo_path, 'rev-parse', '--abbrev-ref', 'HEAD')
    return branch if success else None

def get_default_branch(repo_path, configured=None):
    """Resolve a repo's default branch.

    ``configured`` is the optional per-repo ``defaultBranch`` from repos.json. It
    wins over origin/HEAD when the branch actually exists on origin, so a repo can
    be pinned to a working default (e.g. ``mirror/main``) that is not the remote's
    own HEAD.
    """
    if configured:
        success, _ = run_git(repo_path, 'show-ref', '--verify', '--quiet',
                             f'refs/remotes/origin/{configured}')
        if success:
            return configured
    success, ref = run_git(repo_path, 'symbolic-ref', 'refs/remotes/origin/HEAD')
    if success and ref:
        return ref.replace('refs/remotes/origin/', '')
    for branch in ['main', 'master']:
        success, _ = run_git(repo_path, 'show-ref', '--verify', '--quiet', f'refs/remotes/origin/{branch}')
        if success:
            return branch
    return None

def get_branch_age_days(repo_path):
    success, timestamp = run_git(repo_path, 'log', '-1', '--format=%ct')
    if not success or not timestamp:
        return 0
    try:
        commit_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        return (datetime.now(timezone.utc) - commit_time).days
    except Exception:
        return 0


# Age at which local work is considered abandoned: a stale branch is offered up for
# switching, and stale uncommitted changes on a default branch are stashed and reset.
STALE_DAYS = 14


def get_dirty_age_days(repo_path):
    """Age in days of the newest uncommitted change in the working tree.

    Measured from file mtimes rather than commit dates: a default branch that tracks
    origin has a recent last commit even when the local edit sitting on top of it is
    months old, so ``get_branch_age_days`` cannot answer this. Returns ``None`` when
    the tree is clean, or when no listed path can be stat'd (deletions only), which
    callers should treat as "do not touch".
    """
    success, porcelain = run_git(repo_path, 'status', '--porcelain')
    if not success or not porcelain:
        return None

    newest = None
    for line in porcelain.splitlines():
        # ``run_git`` strips its output, so the first line arrives as "M path" while
        # the rest keep the leading status column (" M path"). Match the status codes
        # instead of slicing at a fixed offset.
        match = re.match(r'^\s*[MADRCU?!]{1,2}\s+(.*)$', line)
        if not match:
            continue
        path = match.group(1).strip()
        if ' -> ' in path:  # rename/copy: the destination is the live file
            path = path.split(' -> ', 1)[1]
        path = path.strip().strip('"')
        if not path:
            continue
        try:
            mtime = (Path(repo_path) / path).stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime

    if newest is None:
        return None
    # Compare epoch floats: datetime.now() truncates to microseconds, which rounds an
    # exactly-N-day-old mtime down to N-1.
    return int((time.time() - newest) // 86400)


SYNC_STATE_DIR = Path.home() / '.dev' / 'sync-state'


def _sync_state_paths(name):
    safe = name.replace('/', '__').replace('\\', '__')
    return (
        SYNC_STATE_DIR / f'{safe}.json',
        SYNC_STATE_DIR / f'{safe}.log',
    )


def _load_sync_state(name):
    state_path, _ = _sync_state_paths(name)
    if not state_path.exists():
        return None
    try:
        with open(state_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _save_sync_state(name, state):
    state_path, _ = _sync_state_paths(name)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, state_path)


def _clear_sync_state(name):
    state_path, log_path = _sync_state_paths(name)
    for p in (state_path, log_path):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _is_pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _format_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds}s'
    if seconds < 3600:
        return f'{seconds // 60}m {seconds % 60}s'
    return f'{seconds // 3600}h {(seconds % 3600) // 60}m'


def _spawn_background_sync(name, ops, label):
    """Spawn a detached background process running a list of ops; record state.

    Each op is a dict ``{'argv': [...], 'cwd': '...'}``.
    """
    state_path, log_path = _sync_state_paths(name)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps({
        'name': name,
        'ops': ops,
        'state_path': str(state_path),
        'log_path': str(log_path),
    })
    cmd = [sys.executable, str(SCRIPT_DIR / 'dev.py'), '__bg_sync__', payload]

    popen_kwargs = {
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
        'close_fds': True,
    }
    if sys.platform == 'win32':
        popen_kwargs['creationflags'] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs['start_new_session'] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)  # pylint: disable=consider-using-with

    _save_sync_state(name, {
        'pid': proc.pid,
        'name': name,
        'label': label,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'status': 'running',
        'log_path': str(log_path),
        'ops': ops,
    })
    return proc.pid


def cmd_bg_sync(args):
    """Hidden subcommand: run a sequence of ops, log output, write status.

    Invoked only via ``_spawn_background_sync`` in a detached child process.
    """
    payload = json.loads(args.payload)
    name = payload['name']
    ops = payload['ops']
    state_path = Path(payload['state_path'])
    log_path = Path(payload['log_path'])

    log_path.parent.mkdir(parents=True, exist_ok=True)
    final_status = 'succeeded'
    final_exit = 0
    try:
        with open(log_path, 'w', encoding='utf-8') as log:
            for op in ops:
                argv = list(op['argv'])
                cwd = op.get('cwd') or None
                log.write(f"$ (cd {cwd}) {' '.join(argv)}\n")
                log.flush()
                # Resolve cmd via shell on Windows so .bat/.cmd shims work
                use_shell = sys.platform == 'win32'
                result = subprocess.run(
                    argv, stdout=log, stderr=subprocess.STDOUT,
                    cwd=cwd, shell=use_shell,
                )
                if result.returncode != 0:
                    final_status = 'failed'
                    final_exit = result.returncode
                    break
    except Exception as exc:
        final_status = 'failed'
        final_exit = -1
        try:
            with open(log_path, 'a', encoding='utf-8') as log:
                log.write(f"\n[bg-sync] exception: {exc}\n")
        except Exception:
            pass

    state = {}
    if state_path.exists():
        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            state = {}
    state.update({
        'name': name,
        'status': final_status,
        'exit_code': final_exit,
        'finished_at': datetime.now(timezone.utc).isoformat(),
        'log_path': str(log_path),
    })
    try:
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass
    return 0


def _report_background_sync_status(name):
    """Check a repo's background sync state, print status, clean up if done.

    Returns True if a background job is still running (caller should skip
    further work on this repo).
    """
    state = _load_sync_state(name)
    if not state:
        return False

    status = state.get('status', 'running')
    log_path = state.get('log_path', '')

    if status == 'running':
        pid = state.get('pid')
        if _is_pid_alive(pid):
            duration = ''
            try:
                started = datetime.fromisoformat(state['started_at'])
                duration = ' (' + _format_duration(
                    (datetime.now(timezone.utc) - started).total_seconds()
                ) + ')'
            except Exception:
                pass
            label = state.get('label', 'sync')
            print(
                f"{Colors.CYAN}[BG-RUN]{Colors.NC} {name}: {label} still running"
                f"{duration} (PID {pid}, log: {log_path})"
            )
            return True
        print(
            f"{Colors.RED}[BG-X]{Colors.NC} {name}: background job died unexpectedly "
            f"(PID {pid}, log: {log_path})"
        )
        _clear_sync_state(name)
        return False

    if status == 'succeeded':
        print(f"{Colors.GREEN}[BG-OK]{Colors.NC} {name}: {state.get('label', 'sync')} completed in background")
    else:
        exit_code = state.get('exit_code', '?')
        print(f"{Colors.RED}[BG-X]{Colors.NC} {name}: background job failed (exit {exit_code}, log: {log_path})")
    _clear_sync_state(name)
    return False


def check_stale_branch(repo_path, name, slow_sync=False, gclient_sync=False, default_branch=None,
                       assume_yes=False):
    current = get_current_branch(repo_path)
    if not current or current == 'HEAD':
        return

    default = get_default_branch(repo_path, default_branch)
    if current.startswith('mirror/') and current != default:
        return

    age_days = get_branch_age_days(repo_path)
    if age_days < STALE_DAYS:
        return

    if not default or current == default:
        return

    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {name}: branch '{current}' is {age_days} days old")
    if not confirm(f"  Switch to '{default}'? [Y/n] ", default_yes=True, assume_yes=assume_yes):
        return

    repo_path_str = str(repo_path)
    ops = [
        {'argv': ['git', '-C', repo_path_str, 'fetch', 'origin'], 'cwd': None},
        {'argv': ['git', '-C', repo_path_str, 'checkout', '-f', default], 'cwd': None},
        {'argv': ['git', '-C', repo_path_str, 'reset', '--hard', f'origin/{default}'], 'cwd': None},
    ]
    label = f'switching {current} -> {default}'
    if gclient_sync:
        gclient_cwd = str(Path(repo_path).parent)
        ops.append({'argv': ['gclient', 'sync', '-Df'], 'cwd': gclient_cwd})
        label += ' + gclient sync -Df'

    if slow_sync:
        pid = _spawn_background_sync(name, ops, label=label)
        _, log_path = _sync_state_paths(name)
        print(
            f"{Colors.CYAN}[BG]{Colors.NC} {name} {label} "
            f"in background (PID {pid}, log: {log_path})"
        )
        return

    use_shell = sys.platform == 'win32'
    for op in ops:
        result = subprocess.run(op['argv'], cwd=op.get('cwd'), check=False, shell=use_shell)
        if result.returncode != 0:
            emit_error(f"{name}: switch to {default} failed at "
                       f"`{' '.join(op['argv'])}`; still on {current}")
            return
    emit_ok(f"Switched to {default}")


def _sync_repo_latest(repo_path, default_branch=None):
    """Fetch origin and bring the local default branch up to origin's HEAD.

    Work is never destroyed without a recovery path. On a clean default branch the
    repo is hard-reset to ``origin/<default>``. On a default branch with uncommitted
    changes, the changes are left alone until they are ``STALE_DAYS`` old, after which
    they are stashed (recoverable via ``git stash list``) and the branch is reset. On a
    feature branch the current branch and working tree are untouched and only the local
    default ref is fast-forwarded. ``default_branch`` is the repo's configured
    ``defaultBranch``, if any. Returns ``(status, default_branch)`` where status is
    one of: ``updated`` (advanced to origin), ``current`` (already up to date),
    ``reset`` (stale changes stashed, branch reset to origin), ``dirty`` (recent
    uncommitted changes, left as-is), ``diverged`` (local default has commits origin
    doesn't, left as-is), or ``failed``.
    """
    repo_path_str = str(repo_path)

    def _git(*argv, timeout=None):
        return subprocess.run(
            ['git', '-C', repo_path_str, *argv],
            capture_output=True, text=True, timeout=timeout,
        )

    try:
        if _git('fetch', '--prune', 'origin').returncode != 0:
            return 'failed', None
    except Exception:
        return 'failed', None

    default = get_default_branch(repo_path, default_branch)
    if not default:
        return 'failed', None

    ok_remote, remote_sha = run_git(repo_path, 'rev-parse', f'origin/{default}')
    if not ok_remote:
        return 'failed', default
    ok_local, local_sha = run_git(repo_path, 'rev-parse', default)

    current = get_current_branch(repo_path)
    if current == default:
        if run_git(repo_path, 'status', '--porcelain')[1]:
            # Recent work is left alone; only changes abandoned for STALE_DAYS are
            # cleared, and they are stashed first so the reset stays recoverable.
            age = get_dirty_age_days(repo_path)
            if age is None or age < STALE_DAYS:
                return 'dirty', default
            label = f'dev-sync {default} {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
            if _git('stash', 'push', '--include-untracked', '-m', label).returncode != 0:
                return 'dirty', default
            if run_git(repo_path, 'status', '--porcelain')[1]:
                # The tree is still dirty right after stashing, so the diff is not
                # real content: it is a renormalization artifact (.gitattributes
                # demands an eol normalization the committed blob does not have), and
                # it reappears the instant the file is written back. Stashing it on
                # every sync would pile up junk entries forever, so undo and skip.
                top = run_git(repo_path, 'stash', 'list', '-1')[1]
                if label in top:
                    _git('stash', 'drop', 'stash@{0}')
                return 'dirty', default
            if ok_local and local_sha == remote_sha:
                return 'reset', default
            if _git('reset', '--hard', f'origin/{default}').returncode == 0:
                return 'reset', default
            return 'failed', default
        if ok_local and local_sha == remote_sha:
            return 'current', default
        if _git('reset', '--hard', f'origin/{default}').returncode == 0:
            return 'updated', default
        return 'failed', default

    # On a feature branch or detached HEAD: fast-forward the local default ref only,
    # without checkout, so the current branch and working tree are untouched.
    if ok_local and local_sha == remote_sha:
        return 'current', default
    # A refspec fetch fast-forwards refs/heads/<default>; git refuses a non
    # fast-forward, so a diverged local default is preserved rather than rewritten.
    if _git('fetch', 'origin', f'{default}:{default}').returncode == 0:
        return 'updated', default
    return 'diverged', default

def get_rcfile_git_timestamp(rel_path):
    """Get the author date of the last commit that modified an rcfile."""
    rcfile_rel = str(Path('repoconfig') / 'rcfiles' / rel_path).replace('\\', '/')
    success, ts = run_git(SCRIPT_DIR, 'log', '-1', '--format=%aI', '--', rcfile_rel)
    if success and ts:
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
    return None

def git_path_deleted(anchor_dir, name):
    """True if <name> under anchor_dir is currently untracked but was deleted in git.

    Anchoring git at the file's own directory keeps this correct whether the
    home copy and the rcfiles mirror live in one repo or two. Distinguishes a
    file deleted on another machine (a committed deletion, to be propagated)
    from a genuinely new file that has no git history.
    """
    tracked, _ = run_git(anchor_dir, 'ls-files', '--error-unmatch', '--', name)
    if tracked:
        return False
    deleted, commit = run_git(anchor_dir, 'log', '-1', '--diff-filter=D', '--format=%H', '--', name)
    return bool(deleted and commit)

def get_file_mtime(file_path):
    """Get the modification time of a file as a timezone-aware datetime."""
    try:
        mtime = file_path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
    except OSError:
        return None


def compute_repo_name(repo_path, base_path=None):
    """Compute repo name, using parent/name format for gclient enlistments."""
    repo_path = Path(os.path.abspath(str(repo_path)))

    parent = repo_path.parent
    if (parent / '.gclient').exists():
        return f"{parent.name}/{repo_path.name}"

    if base_path:
        base_path = Path(os.path.abspath(str(base_path)))
        try:
            rel_path = repo_path.relative_to(base_path)
            parts = rel_path.parts
            if len(parts) >= 2:
                intermediate = base_path / parts[0]
                if (intermediate / '.gclient').exists():
                    return '/'.join(parts[:2])
        except ValueError:
            pass

    return repo_path.name

# ============ REPO COMMANDS ============

def _add_tracked_file(file_path):
    """Add a single file (or directory) to tracking for cross-machine sync.

    A directory is tracked as a whole: every loose file under it is synced on
    each `dev repo sync`, so new files added later are picked up automatically.
    """
    base_path = Path(os.path.abspath(get_base_path()))
    file_path = Path(os.path.abspath(str(file_path)))

    try:
        rel_path = file_path.relative_to(base_path)
    except ValueError:
        emit_error(f"Path must be under workspace root: {base_path}")
        return 1

    rel_str = str(rel_path).replace('\\', '/')
    is_dir = file_path.is_dir()
    config = load_config()
    if 'files' not in config:
        config['files'] = []
    # Drop the entry itself and, for a directory, any now-redundant child entries.
    prefix = rel_str + '/'
    config['files'] = [
        f for f in config['files']
        if f['path'] != rel_str and not (is_dir and f['path'].startswith(prefix))
    ]
    config['files'].append({
        'path': rel_str,
    })

    dest = RCFILES_DIR / rel_str
    if is_dir:
        for src in sorted(file_path.rglob('*')):
            if not src.is_file():
                continue
            sub = src.relative_to(file_path)
            if '.git' in sub.parts:
                continue
            out = dest / sub
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(out))
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(file_path), str(dest))

    save_config(config)
    kind = 'directory' if is_dir else 'file'
    print(f"{Colors.GREEN}Added {kind}: {rel_str}{Colors.NC}")
    print(f"  Synced to: {dest}")
    return 0


def cmd_repo_add(args):
    """Add a repository or file to tracking."""
    target_path = Path(args.path).resolve()
    display_path = Path(os.path.abspath(args.path))

    if not target_path.exists():
        emit_error(f"Path does not exist: {target_path}")
        return 1

    if target_path.is_file():
        return _add_tracked_file(display_path)

    git_dir = target_path / '.git'
    if not git_dir.exists():
        # A non-git directory is tracked as a synced files directory.
        return _add_tracked_file(display_path)

    remote_url = get_remote_url(target_path, normalize=True)
    if not remote_url:
        emit_warn("No 'origin' remote found")

    config = load_config()
    base_path = get_base_path()
    repo_name = compute_repo_name(display_path, base_path)
    config['repos'] = [r for r in config['repos'] if r['path'] != repo_name]

    entry = {
        'path': repo_name,
        'remoteUrl': remote_url,
    }
    if getattr(args, 'slow_sync', False):
        entry['slowSync'] = True
    if getattr(args, 'gclient_sync', False):
        entry['gclientSync'] = True
    if getattr(args, 'default_branch', None):
        entry['defaultBranch'] = args.default_branch
    config['repos'].append(entry)

    save_config(config)
    print(f"{Colors.GREEN}Added repository: {repo_name}{Colors.NC}")
    print(f"  Remote: {Colors.CYAN}{remote_url}{Colors.NC}")
    print(f"  Path: {display_path}")
    if entry.get('defaultBranch'):
        print(f"  {Colors.CYAN}Default branch: {entry['defaultBranch']} (synced instead of origin/HEAD){Colors.NC}")
    if entry.get('slowSync'):
        print(f"  {Colors.CYAN}Slow sync: enabled (pull operations run in background){Colors.NC}")
    if entry.get('gclientSync'):
        print(f"  {Colors.CYAN}Gclient sync: enabled (runs `gclient sync -Df` after branch switch){Colors.NC}")
    return 0

def cmd_repo_remove(args):
    """Remove a repository or file from tracking."""
    config = load_config()
    name = args.name.replace('\\', '/')
    if name.startswith('./'):
        name = name[2:]

    original_count = len(config['repos'])
    config['repos'] = [r for r in config['repos'] if r['path'] != name]

    if len(config['repos']) < original_count:
        save_config(config)
        print(f"{Colors.GREEN}Removed repository: {name}{Colors.NC}")
        return 0

    files = config.get('files', [])
    original_count = len(files)
    config['files'] = [f for f in files if f['path'] != name]

    if len(config.get('files', [])) < original_count:
        rcfile = RCFILES_DIR / name
        if rcfile.is_dir():
            shutil.rmtree(str(rcfile))
        elif rcfile.exists():
            rcfile.unlink()
        save_config(config)
        print(f"{Colors.GREEN}Removed file: {name}{Colors.NC}")
        return 0

    emit_error(f"'{name}' is not tracked")
    return 1

def cmd_repo_delete(args):
    """Delete a single tracked file from the workspace and rcfiles.

    Unlike `remove` (which untracks a whole entry), `delete` removes one file
    that lives under a tracked directory (e.g. docs/foo.md): it deletes both the
    workspace copy and the rcfiles copy and commits the removal, so the union-
    based directory sync will not resurrect it on the next `dev repo sync`.
    """
    base_path = Path(os.path.abspath(get_base_path()))
    name = args.path.replace('\\', '/')
    if name.startswith('./'):
        name = name[2:]
    # Accept an absolute or workspace path and make it workspace-relative.
    candidate = Path(os.path.abspath(args.path))
    try:
        name = candidate.relative_to(base_path).as_posix()
    except ValueError:
        pass

    workspace_file = base_path / name.replace('/', os.sep)
    rcfile = RCFILES_DIR / name

    if workspace_file.is_dir() or rcfile.is_dir():
        emit_error(f"'{name}' is a directory; use 'dev repo remove' to untrack it")
        return 1

    tracked = {f['path'] for f in _get_all_tracked_files()}
    if name not in tracked:
        emit_error(f"'{name}' is not a tracked file")
        return 1

    removed = []
    if workspace_file.is_file():
        workspace_file.unlink()
        removed.append('workspace')
    if rcfile.is_file():
        rcfile.unlink()
        removed.append('rcfiles')

    # If it was tracked as its own explicit entry (not only via a directory),
    # drop that entry too.
    config = load_config()
    before = len(config.get('files', []))
    config['files'] = [f for f in config.get('files', []) if f['path'] != name]
    if len(config['files']) != before:
        save_config(config)

    # Commit the deletion so directory expansion won't re-add the file.
    run_git(SCRIPT_DIR, 'add', '-A')
    _, status = run_git(SCRIPT_DIR, 'status', '--porcelain')
    if status:
        run_git(SCRIPT_DIR, 'commit', '-m', _build_commit_message())

    print(f"{Colors.GREEN}Deleted:{Colors.NC} {name} ({', '.join(removed) or 'nothing on disk'})")
    print(f"  {Colors.CYAN}Run 'dev repo sync' to push the deletion.{Colors.NC}")
    return 0

def cmd_repo_list(args):
    """List all tracked repositories and files."""
    config = load_config()
    print(f"{Colors.BLUE}Tracked Repositories:{Colors.NC}")

    if not config['repos']:
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} No repositories tracked yet.")
        print("Use 'dev repo add <path>' to add a repository.")
        return 0

    base_path = Path(get_base_path())
    for repo in sorted(config['repos'], key=lambda r: r['path']):
        repo_path = base_path / repo['path'].replace('/', os.sep)
        link_to = repo.get('pathLinksTo')
        if link_to:
            repo_path = Path(os.path.expandvars(os.path.expanduser(link_to)))
        _, url = run_git(repo_path, 'remote', 'get-url', 'origin')
        url = url or repo.get('remoteUrl', 'N/A')
        print(f"  {repo['path']}  {Colors.CYAN}{url}{Colors.NC}")

    print(f"Total: {Colors.GREEN}{len(config['repos'])}{Colors.NC} repositories")

    files = _get_all_tracked_files()
    if files:
        print(f"\n{Colors.BLUE}Tracked Files:{Colors.NC}")
        for f in sorted(files, key=lambda x: x['path']):
            print(f"  {f['path']}")
        print(f"Total: {Colors.GREEN}{len(files)}{Colors.NC} files")

    return 0

def _build_commit_message():
    """Build a descriptive commit message from staged changes."""
    _, status = run_git(SCRIPT_DIR, 'diff', '--cached', '--name-status')
    if not status:
        return 'Auto-sync'

    added, modified, deleted = [], [], []
    for line in status.strip().split('\n'):
        if not line:
            continue
        parts = line.split('\t', 1)
        if len(parts) != 2:
            continue
        status_char, filepath = parts[0], parts[1]
        p = Path(filepath)
        # Show parent_dir/filename for disambiguation
        if len(p.parts) >= 2:
            short = f"{p.parts[-2]}/{p.name}"
        else:
            short = p.name
        # Remap rcfiles paths to ~/
        rcfiles_prefix = 'repoconfig/rcfiles/'
        if filepath.startswith(rcfiles_prefix):
            rel = filepath[len(rcfiles_prefix):]
            rel_p = Path(rel)
            if len(rel_p.parts) >= 2:
                short = f"~/{rel_p.parts[-2]}/{rel_p.name}"
            else:
                short = f"~/{rel_p.name}"
        if status_char.startswith('A'):
            added.append(short)
        elif status_char.startswith('M'):
            modified.append(short)
        elif status_char.startswith('D'):
            deleted.append(short)

    lines = []
    if added:
        lines.append(f"A: {', '.join(added)}")
    if modified:
        lines.append(f"M: {', '.join(modified)}")
    if deleted:
        lines.append(f"D: {', '.join(deleted)}")

    if not lines:
        return 'Auto-sync'

    msg = ' | '.join(lines)
    if len(msg) > 200:
        total = len(added) + len(modified) + len(deleted)
        msg = f'{total} files ({len(added)} added, {len(modified)} modified, {len(deleted)} deleted)'
    return msg


def sync_rcfiles_push(pulled=False):
    """Commit any pending changes and push to remote."""
    run_git(SCRIPT_DIR, 'add', '-A')
    _, status = run_git(SCRIPT_DIR, 'status', '--porcelain')
    if status:
        commit_msg = _build_commit_message()
        run_git(SCRIPT_DIR, 'commit', '-m', commit_msg)

    default_branch = get_default_branch(SCRIPT_DIR) or 'main'
    _, ahead_behind = run_git(SCRIPT_DIR, 'rev-list', '--left-right', '--count', f'HEAD...origin/{default_branch}')
    try:
        ahead, _ = ahead_behind.split()
        ahead = int(ahead)
    except Exception:
        ahead = 0

    if ahead > 0:
        success, output = run_git(SCRIPT_DIR, 'push')
        if success:
            log_range = f'origin/{default_branch}~{ahead}..origin/{default_branch}'
            _, log = run_git(SCRIPT_DIR, 'log', log_range, '--oneline')
            emit_ok(f"rcfiles pushed ({ahead} commits)")
            for line in log.strip().splitlines():
                print(f"     {line}")
        else:
            emit_error(f"Failed to push rcfiles: {output}")
    else:
        if not pulled:
            emit_ok("rcfiles up to date")


def _is_dir_link(path):
    """True if path is a symlink or (on Windows) a directory junction."""
    if path.is_symlink():
        return True
    if os.name == 'nt':
        try:
            import stat
            attrs = os.lstat(path).st_file_attributes
            return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except (OSError, AttributeError):
            return False
    return False


def _ensure_link(link_path, repo_path):
    """Create a link from link_path -> repo_path if needed.

    Uses a symlink; on Windows, where symlinks require elevation, falls back
    to a directory junction (equivalent here and needs no privilege).
    """
    if link_path == repo_path:
        return
    if _is_dir_link(link_path):
        if link_path.resolve() == repo_path.resolve():
            return
        try:
            link_path.unlink()
        except (OSError, PermissionError):
            link_path.rmdir()
    elif link_path.exists():
        return
    link_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        link_path.symlink_to(repo_path, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            raise
        result = subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(link_path), str(repo_path)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise


def _self_update():
    """Pull latest dev_scripts, re-exec if changed."""
    _, old_hash = run_git(SCRIPT_DIR, 'rev-parse', 'HEAD')

    run_git(SCRIPT_DIR, 'add', '-A')
    _, status = run_git(SCRIPT_DIR, 'status', '--porcelain')
    if status:
        commit_msg = _build_commit_message()
        run_git(SCRIPT_DIR, 'commit', '-m', commit_msg)

    _, pre_fetch_hash = run_git(SCRIPT_DIR, 'rev-parse', 'HEAD')

    success, _ = run_git(SCRIPT_DIR, 'fetch', 'origin')
    if not success:
        return

    default_branch = get_default_branch(SCRIPT_DIR) or 'main'
    _, ahead_behind = run_git(SCRIPT_DIR, 'rev-list', '--left-right', '--count', f'HEAD...origin/{default_branch}')
    try:
        _, behind = ahead_behind.split()
        behind = int(behind)
    except Exception:
        behind = 0

    if behind > 0:
        success, _ = run_git(SCRIPT_DIR, 'rebase', f'origin/{default_branch}')
        if not success:
            run_git(SCRIPT_DIR, 'rebase', '--abort')
            emit_error("Rebase conflict in dev_scripts. Please resolve manually.")
            return

    _, new_hash = run_git(SCRIPT_DIR, 'rev-parse', 'HEAD')

    if old_hash != new_hash:
        if pre_fetch_hash != new_hash:
            os.environ['_DEV_PULLED_RCFILES'] = ''
            _, log = run_git(SCRIPT_DIR, 'log', '--oneline', f'{pre_fetch_hash}..{new_hash}')
            if log:
                os.environ['_DEV_PULLED_RCFILES'] = log
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / 'dev.py')] + sys.argv[1:])
        sys.exit(result.returncode)


def cmd_repo_sync(args):
    """Clone missing repositories and sync tracked files."""
    _self_update()

    assume_yes = getattr(args, 'force', False)
    config = load_config()
    base_path = Path(get_base_path())

    print(f"{Colors.BLUE}Syncing rcfiles...{Colors.NC}")
    pulled = False
    pulled_log = os.environ.pop('_DEV_PULLED_RCFILES', None)
    if pulled_log:
        pulled = True
        emit_ok("rcfiles updated from remote:")
        for line in pulled_log.strip().splitlines():
            print(f"     {Colors.YELLOW}{line}{Colors.NC}")
    sync_rcfiles_push(pulled=pulled)
    print()

    if _get_all_tracked_files():
        print(f"{Colors.BLUE}Syncing tracked files...{Colors.NC}")
        sync_tracked_files(base_path)
        print()

    config = load_config()

    print(f"{Colors.BLUE}Syncing repositories to: {base_path}{Colors.NC}")

    if not config['repos']:
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} No repositories to sync.")
        return 0

    base_path.mkdir(parents=True, exist_ok=True)

    synced = skipped = failed = 0
    config_changed = False

    for repo in sorted(config['repos'], key=lambda r: r['path']):
        name = repo['path']
        url = repo.get('remoteUrl', '')
        link_to = repo.get('pathLinksTo')
        slow_sync = bool(repo.get('slowSync'))
        gclient_sync = bool(repo.get('gclientSync'))
        default_branch = repo.get('defaultBranch')
        # Handle nested paths like platform/src
        link_path = base_path / name.replace('/', os.sep)

        if link_to:
            repo_path = Path(os.path.expandvars(os.path.expanduser(link_to)))
        else:
            repo_path = link_path

        if not url:
            print(f"{Colors.RED}[X]{Colors.NC} Cannot clone {name} (no remote URL)")
            failed += 1
            continue

        # Check if this repo is skipped on this machine
        devconfig = os.getenv('DEVCONFIG', '')
        skip_list = repo.get('skipOn', [])
        if devconfig and devconfig in skip_list:
            print(f"{Colors.GREEN}[SKIP]{Colors.NC} {name}")
            skipped += 1
            continue

        if _report_background_sync_status(name):
            skipped += 1
            continue

        if repo_path.exists():
            if link_to is not None:
                _ensure_link(link_path, repo_path)
            actual_url = get_remote_url(repo_path)
            fixed_remote = False
            if (url and actual_url and _parse_ado_remote(url)
                    and _normalize_url_for_comparison(actual_url) != _normalize_url_for_comparison(url)):
                subprocess.run(
                    ['git', '-C', str(repo_path), 'remote', 'set-url', 'origin', url],
                    capture_output=True, text=True
                )
                fixed_remote = True
            suffix = f" {Colors.YELLOW}(fixed remote URL){Colors.NC}" if fixed_remote else ''

            # Slow-sync repos (huge enlistments like bigrepo) are never fetched
            # inline; their background sync / stale-branch prompt handles updates.
            if slow_sync:
                print(f"{Colors.GREEN}[OK]{Colors.NC} {name}{suffix}")
                check_stale_branch(repo_path, name, slow_sync=slow_sync, gclient_sync=gclient_sync,
                                   default_branch=default_branch, assume_yes=assume_yes)
                skipped += 1
                continue

            status, default = _sync_repo_latest(repo_path, default_branch)
            if status == 'updated':
                print(f"{Colors.GREEN}[OK]{Colors.NC} {name} "
                      f"{Colors.YELLOW}(synced to origin/{default}){Colors.NC}{suffix}")
                synced += 1
            elif status == 'current':
                print(f"{Colors.GREEN}[OK]{Colors.NC} {name}{suffix}")
                synced += 1
            elif status == 'reset':
                print(f"{Colors.GREEN}[OK]{Colors.NC} {name} "
                      f"{Colors.YELLOW}(stale local changes stashed; reset to origin/{default}){Colors.NC}{suffix}")
                synced += 1
            elif status == 'dirty':
                print(f"{Colors.YELLOW}[WARN]{Colors.NC} {name} "
                      f"{Colors.YELLOW}(uncommitted changes newer than {STALE_DAYS}d; "
                      f"default not reset){Colors.NC}{suffix}")
                skipped += 1
            elif status == 'diverged':
                print(f"{Colors.YELLOW}[WARN]{Colors.NC} {name} "
                      f"{Colors.YELLOW}(local {default} diverged from origin; not updated){Colors.NC}{suffix}")
                skipped += 1
            else:  # failed
                print(f"{Colors.RED}[X]{Colors.NC} {name} "
                      f"{Colors.YELLOW}(fetch/update failed){Colors.NC}{suffix}")
                failed += 1

            check_stale_branch(repo_path, name, slow_sync=slow_sync, gclient_sync=gclient_sync,
                               default_branch=default_branch, assume_yes=assume_yes)
            continue

        # Prompt user before cloning a new repo
        if not confirm(f"{Colors.YELLOW}[NEW]{Colors.NC} {name} is not set up. Clone it? [y/N] ",
                       default_yes=False, assume_yes=assume_yes):
            if devconfig:
                repo.setdefault('skipOn', []).append(devconfig)
                config_changed = True
            print(f"{Colors.GREEN}[SKIP]{Colors.NC} {name}")
            skipped += 1
            continue

        repo_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"{Colors.BLUE}[DOWN] Cloning {name}...{Colors.NC}")
        clone_argv = ['git', 'clone']
        if default_branch:
            clone_argv += ['--branch', default_branch]
        clone_argv += [url, str(repo_path)]
        result = subprocess.run(clone_argv)
        if result.returncode == 0:
            _ensure_link(link_path, repo_path)
            print(f"{Colors.GREEN}[OK] Cloned {name}{Colors.NC}")
            synced += 1
        else:
            print(f"{Colors.RED}[X]{Colors.NC} Failed to clone {name}")
            failed += 1

    if config_changed:
        save_config(config)

    print()
    synced_str = f"{Colors.GREEN}{synced}{Colors.NC}" if synced > 0 else str(synced)
    skipped_str = f"{Colors.CYAN}{skipped}{Colors.NC}" if skipped > 0 else str(skipped)
    failed_str = f"{Colors.RED}{failed}{Colors.NC}" if failed > 0 else str(failed)
    print(f"Synced: {synced_str} | Skipped: {skipped_str} | Failed: {failed_str}")

    return 0


def has_real_conflict_markers(content):
    """Check for conflict markers outside code blocks and inline code."""
    lines = content.split('\n')
    in_code_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue
        if in_code_block or '`' in line:
            continue
        if re.match(r'^<{7}\s', line) or re.match(r'^={7}\s*$', line) or re.match(r'^>{7}\s', line):
            return True

    return False


HOME_DIR = Path.home()

def _resolve_tracked_home_path(entry):
    """Home-side path for a tracked entry, honoring an optional pathLinksTo."""
    link_to = entry.get('pathLinksTo')
    if link_to:
        return Path(os.path.expandvars(os.path.expanduser(link_to)))
    return HOME_DIR / entry['path'].replace('/', os.sep)


def _expand_tracked_dir(entry):
    """Expand a directory entry into one file entry per file underneath.

    Unions the files present in the home tree and the rcfiles tree so loose
    files added to a tracked directory (e.g. docs/) on either side are picked
    up automatically. Returns None when the entry is not a directory (a plain
    file entry, which the caller keeps as-is).
    """
    rel_path = entry['path']
    link_to = entry.get('pathLinksTo')
    home_root = _resolve_tracked_home_path(entry)
    rc_root = RCFILES_DIR / rel_path

    if not home_root.is_dir() and not rc_root.is_dir():
        return None

    children = []
    seen = set()
    for root in (home_root, rc_root):
        if not root.is_dir():
            continue
        for f in sorted(root.rglob('*')):
            if not f.is_file():
                continue
            sub = f.relative_to(root).as_posix()
            if sub.startswith('.git/') or '/.git/' in f'/{sub}':
                continue
            child_rel = f'{rel_path}/{sub}'
            if child_rel in seen:
                continue
            seen.add(child_rel)
            child = {'path': child_rel}
            if link_to:
                child['pathLinksTo'] = f"{link_to.rstrip('/')}/{sub}"
            children.append(child)
    return children


def _get_all_tracked_files():
    """Return user-tracked file paths from config.

    Directory entries are expanded into per-file entries so loose files added
    under a tracked directory (e.g. docs/) are synced automatically. Duplicate
    paths (e.g. a file also covered by a tracked directory) are collapsed.
    """
    config = load_config()
    result = []
    seen = set()
    for entry in config.get('files', []):
        expanded = _expand_tracked_dir(entry)
        entries = [entry] if expanded is None else expanded
        for item in entries:
            if item['path'] in seen:
                continue
            seen.add(item['path'])
            result.append(item)
    return result


def sync_tracked_files(base_path):
    """Timestamp-based bidirectional sync between home and rcfiles.

    All tracked files sync to home (~/).
    The newer version wins. Returns True if any rcfiles were modified.
    """
    all_files = _get_all_tracked_files()
    rcfiles_changed = False

    for entry in all_files:
        rel_path = entry['path']
        link_to = entry.get('pathLinksTo')
        if link_to:
            target_file = Path(os.path.expandvars(os.path.expanduser(link_to)))
        else:
            target_file = HOME_DIR / rel_path.replace('/', os.sep)
        rcfile = RCFILES_DIR / rel_path

        tgt_exists = target_file.exists()
        rc_exists = rcfile.exists()

        if not tgt_exists and not rc_exists:
            continue

        tgt_content = target_file.read_bytes() if tgt_exists else None
        rc_content = rcfile.read_bytes() if rc_exists else None

        if tgt_content == rc_content:
            if tgt_exists and rc_exists:
                remote_ts = get_rcfile_git_timestamp(rel_path)
                if remote_ts:
                    ts_epoch = remote_ts.timestamp()
                    os.utime(str(target_file), (ts_epoch, ts_epoch))
            print(f"{Colors.GREEN}[OK]{Colors.NC} {rel_path}")
            continue

        if rel_path.endswith('.md'):
            if tgt_exists:
                tgt_text = target_file.read_text(encoding='utf-8')
                if has_real_conflict_markers(tgt_text):
                    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {rel_path} has conflict markers, skipping")
                    continue
            if rc_exists:
                rc_text = rcfile.read_text(encoding='utf-8')
                if has_real_conflict_markers(rc_text):
                    print(f"{Colors.YELLOW}[CONFLICT]{Colors.NC} {rel_path} has merge conflicts in repoconfig")
                    continue

        remote_ts = get_rcfile_git_timestamp(rel_path)
        local_ts = get_file_mtime(target_file) if tgt_exists else None

        if tgt_exists and not rc_exists:
            if git_path_deleted(rcfile.parent, rcfile.name):
                # The rcfiles mirror was deleted on another machine and pulled
                # in via git. Propagate the deletion to the home copy instead of
                # resurrecting it as a "new local file".
                try:
                    target_file.unlink()
                except OSError:
                    pass
                print(f"{Colors.GREEN}[OK]{Colors.NC} {rel_path} {Colors.CYAN}(deleted remotely){Colors.NC}")
                continue
            direction = 'local'
        elif rc_exists and not tgt_exists:
            if git_path_deleted(target_file.parent, target_file.name):
                # The home copy's deletion propagated via git; remove the mirror
                # too instead of pulling it back into the workspace.
                try:
                    rcfile.unlink()
                except OSError:
                    pass
                print(f"{Colors.GREEN}[OK]{Colors.NC} {rel_path} {Colors.CYAN}(deleted remotely){Colors.NC}")
                continue
            direction = 'remote'
        elif remote_ts and local_ts:
            direction = 'local' if local_ts > remote_ts else 'remote'
        else:
            direction = 'local'

        if direction == 'local':
            rcfile.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(target_file), str(rcfile))
            rcfiles_changed = True
            print(f"{Colors.GREEN}[OK]{Colors.NC} {rel_path} {Colors.CYAN}(local -> remote){Colors.NC}")
        else:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(rcfile), str(target_file))
            if remote_ts:
                ts_epoch = remote_ts.timestamp()
                os.utime(str(target_file), (ts_epoch, ts_epoch))
            print(f"{Colors.GREEN}[OK]{Colors.NC} {rel_path} {Colors.CYAN}(remote -> local){Colors.NC}")

    return rcfiles_changed


def cmd_repo_status(args):
    """Show which repos exist on this machine."""
    config = load_config()
    base_path = Path(get_base_path())

    print(f"{Colors.BLUE}Repository Status (base: {base_path}){Colors.NC}")

    present = missing = 0
    for repo in sorted(config['repos'], key=lambda r: r['path']):
        # Handle nested paths like platform/src
        target_path = base_path / repo['path'].replace('/', os.sep)
        if target_path.exists():
            print(f"{Colors.GREEN}[OK]{Colors.NC} {repo['path']}")
            present += 1
        else:
            print(f"{Colors.RED}[X]{Colors.NC} {repo['path']} {Colors.YELLOW}(missing){Colors.NC}")
            missing += 1

    print(f"Present: {Colors.GREEN}{present}{Colors.NC} | Missing: {Colors.RED}{missing}{Colors.NC}")

    files = _get_all_tracked_files()
    if files:
        print(f"\n{Colors.BLUE}Tracked Files:{Colors.NC}")
        f_present = f_missing = 0
        for f in sorted(files, key=lambda x: x['path']):
            workspace_file = base_path / f['path'].replace('/', os.sep)
            if workspace_file.exists():
                print(f"{Colors.GREEN}[OK]{Colors.NC} {f['path']}")
                f_present += 1
            else:
                print(f"{Colors.RED}[X]{Colors.NC} {f['path']} {Colors.YELLOW}(missing){Colors.NC}")
                f_missing += 1
        print(f"Present: {Colors.GREEN}{f_present}{Colors.NC} | Missing: {Colors.RED}{f_missing}{Colors.NC}")

    return 0


def cmd_repo_root(args):
    """Print the workspace root for the current machine."""
    config = load_config()
    base_path = get_base_path(config)
    print(base_path)
    return 0


def _fetch_ado_prs_for_branches(ado_info, branch_names, creator_prefix=None):
    """Fetch ADO PRs for a list of branch names. Returns {branch_name: [pr_dict, ...]}.

    If creator_prefix is given, only PRs whose creator uniqueName starts with it are included.
    """

    org, project, repo = ado_info
    token = get_ado_token()
    if not token:
        return None

    result = {}
    for branch_name in branch_names:
        ref = f'refs/heads/{branch_name}'
        url = (f'https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}'
               f'/pullrequests?searchCriteria.sourceRefName={ref}'
               f'&searchCriteria.status=all&api-version={ADO_API_VERSION}')
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                prs = data.get('value', [])
                if creator_prefix:
                    prs = [pr for pr in prs
                           if pr.get('createdBy', {}).get('uniqueName', '')
                           .lower().startswith(creator_prefix.lower())]
                if prs:
                    result[branch_name] = [
                        {'id': pr['pullRequestId'], 'title': pr.get('title', ''),
                         'status': pr.get('status', '')}
                        for pr in prs
                    ]
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            continue
    return result


def _delete_ado_branch(ado_info, branch_name, repo_path):
    """Delete a remote branch via the ADO refs API. Returns (ok, error_msg)."""

    org, project, repo = ado_info
    token = get_ado_token()
    if not token:
        return False, 'no ADO token'

    result = subprocess.run(
        ['git', '-C', str(repo_path), 'rev-parse', f'origin/{branch_name}'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, 'could not resolve branch ref'
    old_object_id = result.stdout.strip()

    url = (f'https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}'
           f'/refs?api-version={ADO_API_VERSION}')
    body = json.dumps([{
        'name': f'refs/heads/{branch_name}',
        'oldObjectId': old_object_id,
        'newObjectId': '0000000000000000000000000000000000000000'
    }]).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST',
                                headers={'Authorization': f'Bearer {token}',
                                         'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            results = data.get('value', [])
            if results and results[0].get('success', False):
                return True, ''
            status = results[0].get('updateStatus', 'unknown') if results else 'empty response'
            return False, status
    except urllib.error.HTTPError as e:
        return False, f'HTTP {e.code}'
    except urllib.error.URLError as e:
        return False, str(e.reason)


def _abandon_ado_pr(ado_info, pr_id):
    """Abandon an active ADO pull request. Returns True on success."""

    org, project, repo = ado_info
    token = get_ado_token()
    if not token:
        return False

    url = (f'https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}'
           f'/pullrequests/{pr_id}?api-version={ADO_API_VERSION}')
    body = json.dumps({'status': 'abandoned'}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='PATCH',
                                headers={'Authorization': f'Bearer {token}',
                                         'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except (urllib.error.URLError, urllib.error.HTTPError):
        return False


SKIP_BRANCH_PREFIXES = ('official/', 'int/', 'main', 'master', 'develop')


def _should_skip_branch(name):
    """Return True for branches that should never be listed for cleanup."""
    for prefix in SKIP_BRANCH_PREFIXES:
        if name == prefix or name.startswith(prefix):
            return True
    return False


def _scan_old_branches_ado(ado_info, creator, cutoff):
    """Scan an ADO repo for old branches owned by creator. Returns list of (branch_ref, commit_date, age_days)."""

    org, project, repo = ado_info

    # stats/branches returns commit dates inline — single API call
    url = (f'https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}'
           f'/stats/branches?api-version={ADO_API_VERSION}')
    data = _ado_get_json(url, timeout=30)
    if data is None:
        return []

    # Also get refs to check creator (stats API doesn't include pusher)
    refs_url = (f'https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}'
                f'/refs?filter=heads/&api-version={ADO_API_VERSION}')
    refs_data = _ado_get_json(refs_url, timeout=30)
    if refs_data is None:
        return []
    creator_branches = set()
    for ref in refs_data.get('value', []):
        ref_creator = ref.get('creator', {}).get('uniqueName', '')
        if creator in ref_creator:
            creator_branches.add(ref['name'].replace('refs/heads/', ''))

    old_branches = []
    for branch_stat in data.get('value', []):
        name = branch_stat.get('name', '')
        if name not in creator_branches:
            continue
        if branch_stat.get('isBaseVersion', False):
            continue
        if _should_skip_branch(name):
            continue
        commit = branch_stat.get('commit', {})
        date_str = commit.get('committer', {}).get('date', '')
        if not date_str:
            continue
        try:
            commit_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if commit_date < cutoff:
                age_days = (datetime.now(timezone.utc) - commit_date).days
                old_branches.append((f'origin/{name}', commit_date, age_days))
        except ValueError:
            continue
    return old_branches


def _scan_old_branches_git(repo_path, prefix, author_email, cutoff):
    """Scan a git repo for old branches by prefix + author. Returns list of (branch, commit_date, age_days)."""
    # Get the default branch to exclude it
    head_result = subprocess.run(
        ['git', '-C', str(repo_path), 'symbolic-ref', 'refs/remotes/origin/HEAD'],
        capture_output=True, text=True
    )
    default_ref = head_result.stdout.strip() if head_result.returncode == 0 else ''

    result = subprocess.run(
        ['git', '-C', str(repo_path), 'branch', '-r', '--list', f'*{prefix}*'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return []

    branches = []
    for b in result.stdout.strip().split('\n'):
        b = b.strip()
        if not b or 'HEAD' in b:
            continue
        branch_name = b.replace('remotes/origin/', '').replace('origin/', '')
        if _should_skip_branch(branch_name):
            continue
        if default_ref and b.replace('remotes/', 'refs/remotes/') == default_ref:
            continue
        branches.append(b)
    old_branches = []
    for branch in branches:
        result = subprocess.run(
            ['git', '-C', str(repo_path), 'log', '-1', '--format=%cI%n%ae', branch],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            continue
        lines = result.stdout.strip().split('\n')
        if len(lines) < 2:
            continue
        date_str, email = lines[0], lines[1]
        if author_email and author_email not in email:
            continue
        try:
            commit_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if commit_date < cutoff:
                age_days = (datetime.now(timezone.utc) - commit_date).days
                old_branches.append((branch, commit_date, age_days))
        except ValueError:
            continue
    return old_branches


def _print_old_branches(repo_path, old_branches, creator_prefix=None):
    """Print old branches with linked PR info for a single repo."""
    remote_url = get_remote_url(repo_path)
    ado_info = _parse_ado_remote(remote_url) if remote_url else None
    pr_map = {}
    org, project = None, None
    if ado_info:
        branch_names = [b.replace('remotes/origin/', '').replace('origin/', '')
                        for b, _, _ in old_branches]
        fetched = _fetch_ado_prs_for_branches(ado_info, branch_names, creator_prefix)
        if fetched:
            pr_map = fetched
        org, project = ado_info[0], ado_info[1]

    for branch, commit_date, age_days in old_branches:
        display_name = branch.replace('remotes/origin/', 'origin/')
        branch_name = branch.replace('remotes/origin/', '').replace('origin/', '')
        print(f"  {display_name}")
        print(f"    Last commit: {commit_date.strftime('%Y-%m-%d')} ({age_days} days ago)")
        if branch_name in pr_map:
            for pr in pr_map[branch_name]:
                status_color = Colors.GREEN if pr['status'] == 'completed' else (
                    Colors.YELLOW if pr['status'] == 'active' else (
                    Colors.GREY if pr['status'] == 'abandoned' else Colors.NC))
                pr_url = f'https://dev.azure.com/{org}/{project}/_git/{ado_info[2]}/pullrequest/{pr["id"]}'
                print(f"    PR !{pr['id']} [{status_color}{pr['status']}{Colors.NC}] {pr['title']}")
                print(f"       {pr_url}")


def cmd_repo_old(args):
    """List or delete old branches you pushed."""

    prefix = args.prefix or 'user/developer/'
    creator_email = 'developer@example.com'
    creator_alias = prefix.strip('/').split('/')[-1] if '/' in prefix else None
    days = args.days or 30
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    repo_path = args.path
    if repo_path:
        repo_path = Path(repo_path).resolve()
    else:
        repo_path = Path(os.getcwd()).resolve()

    # If the path is a git repo, scan just that repo
    if (repo_path / '.git').exists():
        repo_paths = [repo_path]
    else:
        # Scan all tracked repos
        try:
            config = load_config()
            base = get_base_path(config)
            repo_paths = []
            for repo in config.get('repos', []):
                rp = Path(base) / repo['path']
                if (rp / '.git').exists():
                    repo_paths.append(rp)
        except Exception:
            print(f"{Colors.RED}Error: {repo_path} is not a git repository and could not load tracked repos{Colors.NC}")
            return 1
        if not repo_paths:
            print(f"{Colors.RED}Error: No tracked git repositories found{Colors.NC}")
            return 1

    print(f"{Colors.BLUE}Scanning for branches older than {days} days (owned by {creator_email})...{Colors.NC}")
    print(f"Cutoff date: {cutoff.strftime('%Y-%m-%d')}")

    all_old = []  # (repo_path, branch, commit_date, age_days)
    scanned_remotes = set()
    for rp in repo_paths:
        remote_url = get_remote_url(rp)
        ado_info = _parse_ado_remote(remote_url) if remote_url else None
        remote_key = ado_info[2] if ado_info else (remote_url or str(rp))
        if remote_key in scanned_remotes:
            continue
        scanned_remotes.add(remote_key)
        if ado_info:
            old = _scan_old_branches_ado(ado_info, creator_email, cutoff)
        else:
            old = _scan_old_branches_git(rp, prefix, creator_email, cutoff)
        if old:
            old.sort(key=lambda x: x[1])
            print(f"\n{Colors.CYAN}{rp.name}{Colors.NC} ({len(old)} branches)")
            _print_old_branches(rp, old, creator_alias)
            for entry in old:
                all_old.append((rp, *entry))

    if not all_old:
        print(f"\n{Colors.GREEN}No branches older than {days} days found{Colors.NC}")
        return 0

    total = len(all_old)
    print(f"\n{Colors.YELLOW}Found {total} old branch(es) total{Colors.NC}")

    if not args.delete:
        print(f"\n{Colors.CYAN}To delete these branches, run:{Colors.NC}")
        print("  dev repo old --delete")
        return 0

    # Delete mode — build per-repo ADO info and PR map
    ado_info_map = {}
    pr_map = {}
    for rp, branch, _commit_date, _age_days in all_old:
        if rp not in ado_info_map:
            remote_url = get_remote_url(rp)
            ado_info_map[rp] = _parse_ado_remote(remote_url) if remote_url else None
        ado_info = ado_info_map[rp]
        if ado_info:
            branch_name = branch.replace('remotes/origin/', '').replace('origin/', '')
            fetched = _fetch_ado_prs_for_branches(ado_info, [branch_name], creator_alias)
            if fetched and branch_name in fetched:
                pr_map[(rp, branch)] = fetched[branch_name]

    active_pr_count = sum(
        1 for prs in pr_map.values() for pr in prs if pr['status'] == 'active')

    print(f"\n{Colors.RED}WARNING: This will delete {total} remote branch(es)"
          f" and abandon {active_pr_count} active PR(s)!{Colors.NC}")
    response = input("Type 'yes' to confirm: ")
    if response.lower() != 'yes':
        print("Aborted.")
        return 0

    deleted = 0
    failed = 0
    abandoned = 0
    for rp, branch, _commit_date, _age_days in all_old:
        remote_branch = branch.replace('remotes/origin/', '').replace('origin/', '')
        print(f"Deleting origin/{remote_branch} ({rp.name})...", end=' ')

        ado_info = ado_info_map.get(rp)
        if ado_info:
            ok, err = _delete_ado_branch(ado_info, remote_branch, rp)
        else:
            result = subprocess.run(
                ['git', '-C', str(rp), 'push', 'origin', '--delete', remote_branch],
                capture_output=True, text=True
            )
            ok = result.returncode == 0
            err = result.stderr.strip() if not ok else ''

        if not ok:
            print(f"{Colors.RED}FAILED{Colors.NC}")
            if err:
                print(f"    {err}")
            failed += 1
            continue

        print(f"{Colors.GREEN}OK{Colors.NC}")
        deleted += 1
        if (rp, branch) not in pr_map:
            continue
        prs = pr_map[(rp, branch)]
        active_prs = [pr for pr in prs if pr['status'] == 'active']
        for pr in active_prs:
            print(f"  Abandoning PR !{pr['id']}...", end=' ')
            if _abandon_ado_pr(ado_info, pr['id']):
                print(f"{Colors.GREEN}OK{Colors.NC}")
                abandoned += 1
            else:
                print(f"{Colors.RED}FAILED{Colors.NC}")

    print(f"Deleted: {Colors.GREEN}{deleted}{Colors.NC} | "
          f"Abandoned PRs: {Colors.GREEN}{abandoned}{Colors.NC} | "
          f"Failed: {Colors.RED}{failed}{Colors.NC}")

    return 0 if failed == 0 else 1



# =============================================================================
# Init Command
# =============================================================================

BASHRC_SOURCE_LINE = '[ -f ~/.zshrc ] && exec zsh'
PSRC_CONTENT = '$profile = "$HOME\\.psrc.ps1"\n. $profile\n'


def _init_windows():
    """Ensure $PROFILE sources .psrc.ps1."""
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', '$PROFILE'],
        capture_output=True, text=True)
    if result.returncode != 0:
        emit_error("Could not determine $PROFILE path")
        return 1

    profile_path = Path(result.stdout.strip())

    if profile_path.exists():
        content = profile_path.read_text(encoding='utf-8')
        if '.psrc.ps1' in content:
            emit_ok("$PROFILE already sources .psrc.ps1")
            return 0

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(PSRC_CONTENT, encoding='utf-8')
    emit_ok(f"Wrote $PROFILE -> .psrc.ps1 ({profile_path})")
    return 0


def _init_unix():
    """Ensure zsh is the running shell, or .bashrc execs into it."""
    shell = os.environ.get('SHELL', '')

    if 'zsh' in shell:
        emit_ok("zsh is the default shell")
        return 0

    if get_os_type() == 'linux' and not shutil.which('zsh'):
        print(f"{Colors.BLUE}Installing zsh...{Colors.NC}")
        result = subprocess.run(
            ['sudo', 'apt-get', 'install', '-y', 'zsh'],
            capture_output=True, text=True)
        if result.returncode != 0:
            emit_error(f"Failed to install zsh: {result.stderr}")
            return 1
        emit_ok("zsh installed")

    bashrc = Path.home() / '.bashrc'
    if not bashrc.exists():
        bashrc.write_text(f"{BASHRC_SOURCE_LINE}\n", encoding='utf-8')
        emit_ok("Created .bashrc with zsh exec")
        return 0

    content = bashrc.read_text(encoding='utf-8')
    if BASHRC_SOURCE_LINE in content:
        emit_ok(".bashrc already execs into zsh")
        return 0

    with open(bashrc, 'a', encoding='utf-8') as f:
        f.write(f"\n{BASHRC_SOURCE_LINE}\n")
    emit_ok("Added zsh exec to .bashrc")
    return 0


def cmd_init(args):
    """Bootstrap shell profile on a fresh machine."""
    if get_os_type() == 'windows':
        return _init_windows()
    return _init_unix()


def cmd_test(args):
    """Run dev.py unit tests"""
    test_file = SCRIPT_DIR / 'test_dev.py'
    if not test_file.exists():
        emit_error("test_dev.py not found")
        return 1

    print(f"{Colors.BLUE}Running tests...{Colors.NC}", flush=True)
    result = subprocess.run(
        [sys.executable, '-u', '-m', 'unittest', 'test_dev', '-b'],
        cwd=str(SCRIPT_DIR))
    if result.returncode != 0:
        return result.returncode

    print(f"\n{Colors.BLUE}Running pylint...{Colors.NC}", flush=True)
    lint = subprocess.run(
        [sys.executable, '-u', '-m', 'pylint', 'dev.py'],
        cwd=str(SCRIPT_DIR))
    return lint.returncode


# =============================================================================
# ADO (Azure DevOps) Commands
# =============================================================================

def get_ado_pat():
    """Get the stored ADO PAT, or None if not set"""
    if ADO_PAT_FILE.exists():
        return ADO_PAT_FILE.read_text().strip()
    return None

def cmd_ado_set_pat(args):
    """Set the Azure DevOps PAT."""
    pat = args.pat
    if not pat:
        # Prompt for PAT if not provided
        try:
            import getpass
            pat = getpass.getpass("Enter your Azure DevOps PAT: ").strip()
        except EOFError:
            emit_error("No PAT provided")
            return 1

    if not pat:
        emit_error("PAT cannot be empty")
        return 1

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ADO_PAT_FILE.write_text(pat)
    if sys.platform != 'win32':
        os.chmod(ADO_PAT_FILE, 0o600)

    emit_ok(f"ADO PAT saved to {ADO_PAT_FILE}")
    print(f"     {Colors.YELLOW}Note: Keep this file secure and do not commit it.{Colors.NC}")
    return 0

def cmd_ado_show_pat(args):
    """Show if ADO PAT is configured."""
    pat = get_ado_pat()
    if pat:
        masked = pat[:4] + '*' * (len(pat) - 8) + pat[-4:] if len(pat) > 8 else '****'
        emit_ok(f"ADO PAT is configured: {masked}")
        print(f"     Stored at: {ADO_PAT_FILE}")
    else:
        print(f"{Colors.YELLOW}ADO PAT is not configured{Colors.NC}")
        print("     Run: dev ado set-pat")
    return 0

def cmd_ado_clear_pat(args):
    """Clear the stored ADO PAT."""
    if ADO_PAT_FILE.exists():
        ADO_PAT_FILE.unlink()
        emit_ok("ADO PAT cleared")
    else:
        print(f"{Colors.YELLOW}No ADO PAT was configured{Colors.NC}")
    return 0

def _ado_host_from_url(url):
    """Return the ADO host from a git remote URL, or None if not an ADO host."""
    if not url:
        return None
    url = _strip_url_credentials(url)
    m = re.match(r'https://([^/]+)/', url)
    if not m:
        return None
    host = m.group(1).lower()
    if host == 'dev.azure.com' or host.endswith('.visualstudio.com'):
        return host
    return None


def _ado_credential_helper_value():
    """Return the git credential.helper value that invokes `dev ado
    credential-helper`.

    Uses an absolute interpreter + dev.py path rather than relying on `dev`
    being on PATH: tools that invoke git internally (notably toolchain_tools /
    gclient) run git with a sanitized PATH that does NOT include dev_scripts,
    so a bare `!dev ...` helper would silently fail and git would fall back to
    prompting for a username. We also prefer the canonical home copy
    (~/dev_scripts/dev.py) over the currently-running script so the helper hits
    a fast local file instead of a slow Windows-mounted path (/mnt/c/...) when
    invoked from WSL.
    """
    home_copy = Path.home() / 'dev_scripts' / 'dev.py'
    running = Path(__file__).resolve()
    dev_py = home_copy if home_copy.exists() else running
    py = shlex.quote(sys.executable or 'python3')
    script = shlex.quote(str(dev_py))
    return f'!{py} {script} ado credential-helper'


def _heal_ado_auth(repo_path):
    """Install a global, host-scoped git credential helper that supplies a fresh
    ADO bearer token on demand. Unlike a static http.extraheader, this re-fetches
    the token via `dev ado token` on every auth challenge, so it AUTO-HEALS when
    the token expires (as long as `az login` is still valid). Tools that invoke
    git internally (gclient sync, toolchain_tools selfupdate) reuse it transparently.

    Returns the host that was healed, or None if the repo has no ADO remote.
    """
    host = _ado_host_from_url(get_remote_url(repo_path))
    if not host:
        return None
    base = f'https://{host}'
    quiet = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    # Remove any stale static header we may have set previously: a forced
    # extraheader always wins over the credential helper and would send an
    # expired token, defeating auto-heal.
    subprocess.run(['git', 'config', '--global', '--unset-all',
                    f'http.{base}/.extraheader'], **quiet)
    # Reset the helper list for this host. The leading empty entry drops any
    # inherited global helper (e.g. `store`) so an ephemeral token is never
    # cached to ~/.git-credentials and served stale later.
    subprocess.run(['git', 'config', '--global', '--unset-all',
                    f'credential.{base}.helper'], **quiet)
    subprocess.run(['git', 'config', '--global', '--add',
                    f'credential.{base}.helper', ''], **quiet)
    subprocess.run(['git', 'config', '--global', '--add',
                    f'credential.{base}.helper', _ado_credential_helper_value()],
                   **quiet)
    return host


def cmd_ado_git(args):
    """Run a git command with ADO bearer token authentication."""
    git_args = args.git_args
    if git_args and git_args[0] == '--':
        git_args = git_args[1:]
    if not git_args:
        print("Usage: dev ado git <git-command> [args...]")
        print("Example: dev ado git pull")
        return 1

    token = get_ado_token()
    if not token:
        emit_error("Failed to get ADO token. Run: az login")
        return 1

    host = _heal_ado_auth(Path(os.getcwd()).resolve())
    if host:
        emit_ok(f"Installed auto-refreshing ADO auth for {host}; "
                "gclient/toolchain_tools git will reuse it and re-heal on expiry")

    result = subprocess.run(
        ['git', '-c', f'http.extraheader=Authorization: Bearer {token}'] + git_args)
    return result.returncode


def cmd_ado_credential_helper(args):
    """git credential helper: supply a fresh ADO bearer token on demand.

    Invoked by git (configured via `dev ado git`) as `... credential-helper get`.
    Only `get` produces output; `store`/`erase` are no-ops. Outputs nothing but
    the credential protocol fields so git can parse it cleanly.
    """
    # Drain git's protocol input on stdin so it doesn't see a broken pipe.
    try:
        sys.stdin.read()
    except Exception:
        pass
    if getattr(args, 'op', 'get') != 'get':
        return 0
    token = get_ado_token()
    if not token:
        return 1
    sys.stdout.write(f"username=ado\npassword={token}\n")
    return 0


def _decode_jwt_exp(token):
    """Return the JWT `exp` claim (epoch seconds) for the given token, or None.

    `az account get-access-token` returns tokens from its own cache, so a freshly
    fetched token may already be partway through (or past) its lifetime. The JWT
    itself carries the real expiry — trust that instead of a wall-clock heuristic.
    """
    if not token or not isinstance(token, str):
        return None
    parts = token.split('.')
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = '=' * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return None
    exp = claims.get('exp')
    if isinstance(exp, (int, float)):
        return float(exp)
    return None


def _get_cached_ado_token():
    """Return cached token if still valid, else None."""
    if not ADO_TOKEN_CACHE_FILE.exists():
        return None
    try:
        cache = json.loads(ADO_TOKEN_CACHE_FILE.read_text())
        expires = cache.get('expires', 0)
        token = cache.get('token')
        now = datetime.now(timezone.utc).timestamp()
        if now >= expires:
            return None
        jwt_exp = _decode_jwt_exp(token)
        if jwt_exp is not None and now >= jwt_exp - ADO_TOKEN_EXPIRY_BUFFER:
            return None
        return token
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _cache_ado_token(token):
    """Cache a token with expiry derived from its JWT `exp` claim when possible."""
    jwt_exp = _decode_jwt_exp(token)
    if jwt_exp is not None:
        expires = jwt_exp - ADO_TOKEN_EXPIRY_BUFFER
    else:
        expires = datetime.now(timezone.utc).timestamp() + ADO_TOKEN_CACHE_SECONDS
    cache = {
        'token': token,
        'expires': expires,
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ADO_TOKEN_CACHE_FILE.write_text(json.dumps(cache))


def get_ado_token():
    """Get an ADO access token, using cache if available."""
    cached = _get_cached_ado_token()
    if cached:
        return cached

    az_cmd = shutil.which('az') or 'az'
    result = subprocess.run(
        [az_cmd, 'account', 'get-access-token',
         '--resource', '499b84ac-1321-427f-aa17-267ca6975798',
         '--query', 'accessToken', '-o', 'tsv'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    if token:
        _cache_ado_token(token)
    return token


def cmd_ado_token(args):
    """Get an ADO access token (cached)."""
    token = get_ado_token()
    if not token:
        emit_error("Failed to get ADO token. Run: az login")
        return 1
    print(token)
    return 0


def _parse_ado_remote(url):
    """Parse org, project, repo from an ADO git remote URL.

    Supports:
      https://dev.azure.com/{org}/{project}/_git/{repo}
      https://{user}@dev.azure.com/{org}/{project}/_git/{repo}
      https://{org}.visualstudio.com/DefaultCollection/{project}/_git/{repo}
      https://{org}.visualstudio.com/{project}/_git/{repo}
      git@ssh.dev.azure.com:v3/{org}/{project}/{repo}
    Returns (org, project, repo) or None.
    """
    url = re.sub(r'\.git$', '', url)
    m = re.match(r'https://(?:[^@]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/(.+)', url)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = re.match(r'https://([^.]+)\.visualstudio\.com/(?:DefaultCollection/)?([^/]+)/_git/(.+)', url)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = re.match(r'git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/(.+)', url)
    if m:
        return m.group(1), m.group(2), m.group(3)
    return None


def _ado_get_json(url, timeout=None):
    """GET JSON from an ADO REST endpoint. Returns the parsed body or None.

    Handles auth, network, and decode failures uniformly so callers can treat a
    None result as "unavailable" without repeating boilerplate.
    """
    token = get_ado_token()
    if not token:
        return None
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def _fetch_pr(org, project, repo, pr_id):
    """Fetch PR details from ADO REST API. Returns the PR dict or None."""
    base = f'https://dev.azure.com/{org}/{project}/_apis/git/repositories'
    return _ado_get_json(f'{base}/{repo}/pullrequests/{pr_id}?api-version={ADO_API_VERSION}')


def _fetch_pr_threads(org, project, repo, pr_id):
    """Fetch PR comment threads from ADO REST API. Returns list of threads or None."""
    base = f'https://dev.azure.com/{org}/{project}/_apis/git/repositories'
    data = _ado_get_json(f'{base}/{repo}/pullrequests/{pr_id}/threads?api-version={ADO_API_VERSION}')
    return None if data is None else data.get('value', [])


# Every comment posted via `dev pr comments` is stamped with this marker so it is
# clearly attributable to the automated code-review agent rather than a human.
BOT_COMMENT_PREFIX = 'Code-review-bot:'


def _apply_bot_prefix(content):
    """Prepend BOT_COMMENT_PREFIX to a comment body unless already present."""
    content = (content or '').strip()
    if content.startswith(BOT_COMMENT_PREFIX):
        return content
    return f'{BOT_COMMENT_PREFIX}\n{content}'


def _ado_post_json(url, body):
    """POST a JSON body to an ADO REST endpoint. Returns parsed response or None."""
    token = get_ado_token()
    if not token:
        return None
    payload = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url, data=payload, method='POST',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')
        except OSError:
            pass
        emit_error(f"ADO POST failed ({e.code}): {detail}")
        return None


def _ado_patch_json(url, body):
    """PATCH a JSON body to an ADO REST endpoint. Returns parsed response or None."""
    token = get_ado_token()
    if not token:
        return None
    payload = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url, data=payload, method='PATCH',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')
        except OSError:
            pass
        emit_error(f"ADO PATCH failed ({e.code}): {detail}")
        return None


def _post_pr_thread_reply(org, project, repo, pr_id, thread_id, parent_id, content):
    """Reply to an existing PR thread. Returns the created comment or None."""
    base = f'https://dev.azure.com/{org}/{project}/_apis/git/repositories'
    url = f'{base}/{repo}/pullrequests/{pr_id}/threads/{thread_id}/comments?api-version={ADO_API_VERSION}'
    return _ado_post_json(url, {
        'parentCommentId': parent_id,
        'content': content,
        'commentType': 1,
    })


def _post_pr_thread_new(org, project, repo, pr_id, content, file_path=None, line=None):
    """Create a new PR thread. Anchors to file_path/line when given, else PR-level.
    Returns the created thread or None."""
    base = f'https://dev.azure.com/{org}/{project}/_apis/git/repositories'
    url = f'{base}/{repo}/pullrequests/{pr_id}/threads?api-version={ADO_API_VERSION}'
    body = {
        'comments': [{'parentCommentId': 0, 'content': content, 'commentType': 1}],
        'status': 1,
    }
    if file_path:
        if not file_path.startswith('/'):
            file_path = '/' + file_path
        thread_context = {'filePath': file_path}
        if line:
            thread_context['rightFileStart'] = {'line': line, 'offset': 1}
            thread_context['rightFileEnd'] = {'line': line, 'offset': 1}
        body['threadContext'] = thread_context
    return _ado_post_json(url, body)


def _set_pr_thread_status(org, project, repo, pr_id, thread_id, status='fixed'):
    """Set a PR thread's status (e.g. 'fixed', 'closed'). Returns updated thread or None."""
    base = f'https://dev.azure.com/{org}/{project}/_apis/git/repositories'
    url = f'{base}/{repo}/pullrequests/{pr_id}/threads/{thread_id}?api-version={ADO_API_VERSION}'
    return _ado_patch_json(url, {'status': status})


def _find_local_repo_path(org, project, repo):
    """Find the local clone path for an ADO repo from config. Returns path or None."""
    try:
        config = load_config()
        base_path = get_base_path(config)
        for entry in config.get('repos', []):
            parsed = _parse_ado_remote(entry.get('remoteUrl', ''))
            if parsed == (org, project, repo):
                return os.path.join(base_path, entry['path'])
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _resolve_pr_context(args):
    """Resolve repo path, branch, ADO remote, and find existing PR.

    If args.id is provided, fetches the PR directly by ID (repo/branch not needed).
    Returns (org, project, repo, branch, pr_id, az_cmd, git) or prints error and
    returns None.
    """
    az_cmd = shutil.which('az') or 'az'

    if getattr(args, 'id', None):
        pr_id = args.id
        token = get_ado_token()
        if not token:
            emit_error("Failed to get ADO token")
            return None

        repos_to_try = []
        seen = set()
        try:
            for entry in load_config().get('repos', []):
                parsed = _parse_ado_remote(entry.get('remoteUrl', ''))
                if parsed and parsed not in seen:
                    seen.add(parsed)
                    repos_to_try.append(parsed)
        except (OSError, json.JSONDecodeError):
            pass

        for org, project, repo in repos_to_try:
            pr = _fetch_pr(org, project, repo, pr_id)
            if pr:
                branch = pr['sourceRefName'].replace('refs/heads/', '')
                # Stash the fetched PR so callers (e.g. cmd_pr_diff) can reuse it
                # instead of issuing a second identical GET.
                args.resolved_pr = pr
                return org, project, repo, branch, pr_id, az_cmd, None

        emit_error(f"PR !{pr_id} not found in known repos")
        return None

    repo_path = args.repo or '.'

    def git(*cmd_args):
        result = subprocess.run(
            ['git', '--no-pager', '-C', repo_path] + list(cmd_args),
            capture_output=True, text=True)
        return result.returncode, result.stdout.strip()

    branch = args.branch
    if not branch:
        rc, branch = git('branch', '--show-current')
        if rc != 0 or not branch:
            emit_error("Not on a branch")
            return None

    rc, remote_url = git('remote', 'get-url', 'origin')
    if rc != 0 or not remote_url:
        emit_error("No origin remote found")
        return None

    parsed = _parse_ado_remote(remote_url)
    if not parsed:
        emit_error(f"Could not parse ADO remote: {remote_url}")
        return None
    org, project, repo = parsed

    az_cmd = shutil.which('az') or 'az'

    list_result = subprocess.run(
        [az_cmd, 'repos', 'pr', 'list',
         '--org', f'https://dev.azure.com/{org}',
         '--project', project,
         '--repository', repo,
         '--source-branch', branch,
         '--status', 'active',
         '--output', 'json'],
        capture_output=True, text=True)

    pr_id = None
    if list_result.returncode == 0 and list_result.stdout.strip():
        try:
            prs = json.loads(list_result.stdout)
            if prs:
                pr_id = prs[0].get('pullRequestId', prs[0].get('codeReviewId'))
        except (json.JSONDecodeError, KeyError):
            pass

    return org, project, repo, branch, pr_id, az_cmd, git


def _read_description_source(path):
    """Read a PR description from ``path`` (``-`` means stdin).

    Returns the text on success, or ``None`` after printing an error.
    """
    if path == '-':
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding='utf-8')
    except OSError as e:
        emit_error(f"Failed to read {path}: {e}")
        return None


def _parse_bool_arg(value):
    """Parse a string into a boolean for argparse flags that accept ``--flag [true|false]``."""
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    lv = str(value).strip().lower()
    if lv in ('true', 't', 'yes', 'y', '1'):
        return True
    if lv in ('false', 'f', 'no', 'n', '0'):
        return False
    raise argparse.ArgumentTypeError(
        f"expected a boolean value (true/false), got: {value!r}")


def cmd_pr_create(args):
    """Create an ADO pull request from the current branch.

    Creates a draft PR by default. Pass ``--draft false`` to create as active.
    """
    description = ''
    if args.description_file:
        description = _read_description_source(args.description_file)
        if description is None:
            return 1

    ctx = _resolve_pr_context(args)
    if not ctx:
        return 1
    org, project, repo, branch, pr_id, az_cmd, git = ctx

    if pr_id:
        pr_url = f'https://dev.azure.com/{org}/{project}/_git/pullrequest/{pr_id}'
        print(f"{Colors.YELLOW}[!]{Colors.NC} PR already exists: !{pr_id} {pr_url}")
        return 0

    rc, default_branch = git('rev-parse', '--abbrev-ref', 'origin/HEAD')
    if rc != 0 or not default_branch:
        default_branch = 'origin/master'
    target = default_branch.replace('origin/', '', 1)

    title = args.title
    if not title:
        _, title = git('log', '-1', '--format=%s')

    is_draft = getattr(args, 'draft', True)
    cmd = [
        az_cmd, 'repos', 'pr', 'create',
        '--org', f'https://dev.azure.com/{org}',
        '--project', project,
        '--repository', repo,
        '--source-branch', branch,
        '--target-branch', target,
        '--title', title,
        '--draft', 'true' if is_draft else 'false',
        '--output', 'table',
    ]
    label = 'draft' if is_draft else 'active'
    print(f"{Colors.CYAN}[>]{Colors.NC} Creating {label} PR: {branch} → {target}")
    with _description_as_file_arg(description) as desc_arg:
        if desc_arg:
            cmd += desc_arg
        return subprocess.run(cmd).returncode


def _print_pr_description(org, project, repo, pr_id):
    """Fetch and print the current PR description."""
    pr = _fetch_pr(org, project, repo, pr_id)
    if not pr:
        emit_error(f"Failed to fetch PR !{pr_id}")
        return 1
    desc = pr.get('description', '')
    if desc:
        print(desc)
    else:
        print(f"{Colors.YELLOW}[!]{Colors.NC} PR !{pr_id} has no description")
    return 0


def cmd_pr_desc(args):
    """Show or update the description of an existing PR."""
    ctx = _resolve_pr_context(args)
    if not ctx:
        return 1
    org, project, repo, _, pr_id, _, _ = ctx

    if not pr_id:
        emit_error("No active PR found for this branch")
        return 1

    if not args.description:
        return _print_pr_description(org, project, repo, pr_id)

    description = '\n'.join(args.description)

    if not description:
        return _print_pr_description(org, project, repo, pr_id)

    az_cmd = shutil.which('az') or 'az'
    with _description_as_file_arg(description) as desc_arg:
        result = subprocess.run([
            az_cmd, 'repos', 'pr', 'update',
            '--org', f'https://dev.azure.com/{org}',
            '--id', str(pr_id),
            *desc_arg,
            '--output', 'none',
        ])
    if result.returncode != 0:
        return result.returncode

    return _print_pr_description(org, project, repo, pr_id)


@contextmanager
def _description_as_file_arg(description):
    """Yield ``['--description', '@<tmpfile>']`` with ``description`` written
    to a tmp file, cleaning up on exit. Yields ``[]`` if description is empty.

    ``az repos pr update/create --description "<string>"`` drops newlines when
    the string is passed inline through subprocess on Windows. Azure CLI's
    ``@<file>`` argument-from-file syntax sidesteps that — the file content is
    read as-is, preserving newlines.
    """
    if not description:
        yield []
        return
    import tempfile
    tf = tempfile.NamedTemporaryFile(
        mode='wb', suffix='.md', delete=False)
    try:
        tf.write(description.encode('utf-8'))
        tf.close()
        yield ['--description', f'@{tf.name}']
    finally:
        try:
            os.unlink(tf.name)
        except OSError:
            pass


def cmd_pr_diff(args):
    """Show the PR diff as ADO would — merge-base diff against target branch."""
    ctx = _resolve_pr_context(args)
    if not ctx:
        return 1
    org, project, repo, branch, pr_id, _az_cmd, git_fn = ctx

    repo_path = getattr(args, 'repo', None) or '.'

    # When --id is used without --repo, resolve the local clone from config
    if git_fn is None and not getattr(args, 'repo', None):
        repo_path = _find_local_repo_path(org, project, repo) or repo_path

    def git(*cmd_args):
        result = subprocess.run(
            ['git', '--no-pager', '-C', repo_path] + list(cmd_args),
            capture_output=True, text=True)
        return result.returncode, result.stdout.strip()

    # Determine target branch from PR API or fall back to default
    target = None
    if pr_id:
        # Reuse the PR object already fetched by _resolve_pr_context when present.
        pr_data = getattr(args, 'resolved_pr', None) or _fetch_pr(org, project, repo, pr_id)
        if pr_data:
            target = pr_data.get('targetRefName', '').replace('refs/heads/', '')

    if not target:
        rc, default_branch = git('rev-parse', '--abbrev-ref', 'origin/HEAD')
        if rc != 0 or not default_branch:
            default_branch = 'origin/master'
        target = default_branch.replace('origin/', '', 1)

    # When resolved via --id (no local git helper), diff remote refs;
    # otherwise diff HEAD against the target (includes unpushed commits).
    use_head = git_fn is not None
    fetch_refs = [target]
    if not use_head:
        fetch_refs.append(branch)

    ref_names = ', '.join(f'origin/{r}' for r in fetch_refs)
    print(f"{Colors.CYAN}[>]{Colors.NC} Fetching {ref_names}...")
    for ref in fetch_refs:
        subprocess.run(['git', '-C', repo_path, 'fetch', 'origin', ref],
                       capture_output=True)

    source_ref = 'HEAD' if use_head else f'origin/{branch}'
    extra = args.diff_args or []
    if extra and extra[0] == '--':
        extra = extra[1:]
    if getattr(args, 'stat', False):
        extra.append('--stat')
    if getattr(args, 'name_only', False):
        extra.append('--name-only')

    print(f"{Colors.CYAN}[>]{Colors.NC} {branch} vs origin/{target} (merge-base)")
    diff_cmd = ['git', '-C', repo_path, 'diff',
                f'origin/{target}...{source_ref}'] + extra
    return subprocess.run(diff_cmd).returncode


def _post_pr_comment(org, project, repo, pr_id, args, message):
    """Post a PR comment (reply or new thread), optionally resolving the thread.

    Returns process exit code.
    """
    reply_to = getattr(args, 'reply', None)
    file_path = getattr(args, 'file', None)
    line = getattr(args, 'line', None)
    resolve = getattr(args, 'resolve', False)

    if reply_to and (file_path or line):
        emit_error("--reply cannot be combined with --file/--line")
        return 1
    if line and not file_path:
        emit_error("--line requires --file")
        return 1
    if resolve and not reply_to:
        emit_error("--resolve requires --reply <threadId>")
        return 1

    if message:
        content = _apply_bot_prefix(message)
        if reply_to:
            parent = getattr(args, 'parent', 1) or 1
            result = _post_pr_thread_reply(org, project, repo, pr_id, reply_to, parent, content)
            if not result:
                msg = f"Failed to reply to thread {reply_to} on PR !{pr_id}"
                emit_error(f"{msg}")
                return 1
            print(f"{Colors.GREEN}[+]{Colors.NC} Replied to thread {reply_to} on PR !{pr_id} "
                  f"{Colors.GREY}(comment #{result.get('id')}){Colors.NC}")
        else:
            result = _post_pr_thread_new(org, project, repo, pr_id, content, file_path, line)
            if not result:
                emit_error(f"Failed to create thread on PR !{pr_id}")
                return 1
            loc = f"{file_path}:{line}" if (file_path and line) else (file_path or '(PR-level)')
            print(f"{Colors.GREEN}[+]{Colors.NC} Created thread {result.get('id')} on PR !{pr_id} "
                  f"{Colors.GREY}({loc}){Colors.NC}")

    if resolve:
        if not _set_pr_thread_status(org, project, repo, pr_id, reply_to, 'fixed'):
            emit_error(f"Failed to resolve thread {reply_to} on PR !{pr_id}")
            return 1
        print(f"{Colors.GREEN}[+]{Colors.NC} Resolved thread {reply_to} on PR !{pr_id} "
              f"{Colors.GREY}(status: fixed){Colors.NC}")
    return 0


def cmd_pr_comments(args):
    """List or post comment threads on the PR.

    With no message, lists comment threads (active and resolved) from humans and
    bots. Pass ``--active`` to list only active (unresolved) threads. ADO
    system-activity threads (e.g. "ref was updated" push notices,
    vote/policy/status-change and "published the PR" notices, identified by the
    CodeReviewThreadType property) are skipped so only real review comments are
    shown. Prints PR id, thread id, and comment ids so replies can be issued
    without further lookups.

    With a message, posts a comment. Every posted comment is stamped with the
    ``Code-review-bot:`` prefix. Use ``--reply <threadId>`` to reply to an
    existing thread (``--parent`` selects the in-thread parent comment, default
    1), or ``--file``/``--line`` to open a new file-anchored thread. With neither,
    a new PR-level thread is created.

    Use ``--resolve`` (requires ``--reply``) to mark the thread ``fixed`` after
    replying. ``--resolve`` may be used without a message to resolve a thread
    without posting a reply.
    """
    ctx = _resolve_pr_context(args)
    if not ctx:
        return 1
    org, project, repo, _, pr_id, _, _ = ctx

    if not pr_id:
        emit_error("No active PR found for this branch")
        return 1

    message = '\n'.join(getattr(args, 'message', None) or []).strip()
    if message or getattr(args, 'resolve', False):
        return _post_pr_comment(org, project, repo, pr_id, args, message)

    threads = _fetch_pr_threads(org, project, repo, pr_id)
    if threads is None:
        emit_error(f"Failed to fetch threads for PR !{pr_id}")
        return 1

    print(f"{Colors.BLUE}PR !{pr_id}{Colors.NC} {Colors.GREY}({org}/{project}/{repo}){Colors.NC}")
    print()

    resolved_statuses = {'fixed', 'wontFix', 'closed', 'byDesign'}
    active_only = getattr(args, 'active', False)
    printed = 0
    for thread in threads:
        if thread.get('isDeleted'):
            continue
        comments = thread.get('comments') or []
        if not comments:
            continue
        # Skip ADO system-activity threads (ref-update pushes, votes, policy/
        # status changes, "published the pull request" notices). ADO stamps a
        # CodeReviewThreadType property on every auto-generated thread; real
        # human and bot review comments (e.g. PR Assistant, Ownership Enforcer)
        # never carry it, so bot comments are still shown even when their
        # individual commentType is 'system'.
        if 'CodeReviewThreadType' in (thread.get('properties') or {}):
            continue

        status = thread.get('status') or 'open'
        # With --active, list only unresolved threads.
        if active_only and status in resolved_statuses:
            continue

        tc = thread.get('threadContext') or {}
        file_path = tc.get('filePath')
        if file_path:
            anchor = tc.get('rightFileStart') or tc.get('leftFileStart') or {}
            line = anchor.get('line')
            loc = f"{file_path}:{line}" if line else file_path
        else:
            loc = '(PR-level)'

        thread_id = thread.get('id')
        status_label = f"{status}, resolved" if status in resolved_statuses else status
        print(f"{Colors.CYAN}[>]{Colors.NC} {loc}  "
              f"{Colors.GREY}thread {thread_id} ({status_label}){Colors.NC}")
        for comment in comments:
            comment_id = comment.get('id')
            author = (comment.get('author') or {}).get('displayName', 'unknown')
            published = (comment.get('publishedDate') or '')[:19]
            content = (comment.get('content') or '').rstrip()
            parent_id = comment.get('parentCommentId')
            ctype = comment.get('commentType', 'text')
            tags = []
            if parent_id:
                tags.append(f"reply to #{parent_id}")
            if ctype and ctype != 'text':
                tags.append(ctype)
            tag_str = f" {Colors.GREY}({', '.join(tags)}){Colors.NC}" if tags else ''
            print(f"  {Colors.YELLOW}#{comment_id}{Colors.NC} "
                  f"{Colors.GREEN}{author}{Colors.NC} "
                  f"{Colors.GREY}{published}{Colors.NC}{tag_str}")
            for line in content.split('\n'):
                print(f"    {line}")
        print()
        printed += 1

    if printed == 0:
        if active_only:
            print(f"{Colors.YELLOW}[!]{Colors.NC} PR !{pr_id} has no active (unresolved) comment threads")
        else:
            print(f"{Colors.YELLOW}[!]{Colors.NC} PR !{pr_id} has no human or bot comment threads")

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog='dev',
        description='Dev CLI - cross-platform development workflow tool.',
        epilog=(
            'Examples:\n'
            '  dev repo sync                 Clone/refresh every tracked repo to origin HEAD\n'
            '  dev repo status               Show which tracked repos/files exist here\n'
            '  dev repo old --days 30        List stale user branches (add --delete to prune)\n'
            '  dev pr create -t "Title"      Open a draft PR from the current branch\n'
            '  dev pr diff --id 12345        Show a PR diff by id\n'
            '  dev pr comments --active      List only unresolved PR threads\n'
            '  dev ado token                 Print a cached Azure DevOps access token\n'
            '\n'
            'Run `dev <command> -h` for command-specific help.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version', '-V', action='version', version=f'dev {__version__}')
    parser.add_argument('--no-color', action='store_true', help='Disable colored output')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # repo subcommand
    repo_parser = subparsers.add_parser('repo', help='Manage tracked repositories')
    repo_sub = repo_parser.add_subparsers(dest='repo_command')

    add_p = repo_sub.add_parser('add', help='Add a repository or file to tracking')
    add_p.add_argument('path', help='Path to a repository or file')
    add_p.add_argument('--slow-sync', action='store_true',
                       help='Mark repo for background sync (pull/switch ops run detached)')
    add_p.add_argument('--gclient-sync', action='store_true',
                       help='Run `gclient sync -Df` after a branch switch (uses repo parent as cwd)')
    add_p.add_argument('--default-branch', metavar='BRANCH',
                       help="Branch to sync instead of origin/HEAD (e.g. 'mirror/main')")

    remove_p = repo_sub.add_parser('remove', help='Remove a repository or file from tracking')
    remove_p.add_argument('name', help='Name of the repository or file path')

    delete_p = repo_sub.add_parser('delete',
                                   help='Delete a single tracked file (workspace + rcfiles) and commit the removal')
    delete_p.add_argument('path', help='Path to the tracked file (e.g. docs/foo.md)')

    repo_sub.add_parser('list', help='List all tracked repositories')
    sync_p = repo_sub.add_parser('sync', help='Clone missing repositories')
    sync_p.add_argument('-f', '--force', action='store_true',
                        help="Answer 'y' to every prompt (switch stale branches, clone missing repos)")
    repo_sub.add_parser('status', help='Show repo status on this machine')
    repo_sub.add_parser('root', help='Print workspace root for this machine')

    old_p = repo_sub.add_parser('old', help='List/delete old branches')
    old_p.add_argument('--delete', action='store_true', help='Delete the old branches')
    old_p.add_argument('--prefix', default='user/developer/', help='Branch prefix to filter (default: user/developer/)')
    old_p.add_argument('--days', type=int, default=30, help='Age threshold in days (default: 30)')
    old_p.add_argument('path', nargs='?', help='Path to git repository (default: current directory)')

    # ado subcommand
    ado_parser = subparsers.add_parser('ado', help='Azure DevOps integration')
    ado_sub = ado_parser.add_subparsers(dest='ado_command')

    set_pat_p = ado_sub.add_parser('set-pat', help='Set Azure DevOps PAT')
    set_pat_p.add_argument('pat', nargs='?', help='PAT value (will prompt if not provided)')

    ado_sub.add_parser('show-pat', help='Show if PAT is configured')
    ado_sub.add_parser('clear-pat', help='Clear stored PAT')

    git_p = ado_sub.add_parser('git', help='Run git with ADO PAT auth')
    git_p.add_argument('git_args', nargs=argparse.REMAINDER, help='Git command and arguments')

    ado_sub.add_parser('token', help='Get ADO access token (cached)')

    cred_p = ado_sub.add_parser('credential-helper',
                                help='git credential helper (internal; supplies ADO token)')
    cred_p.add_argument('op', nargs='?', default='get',
                        help='git credential operation (get/store/erase)')

    # pr subcommand
    pr_parser = subparsers.add_parser('pr', help='Pull request operations')
    pr_sub = pr_parser.add_subparsers(dest='pr_command')

    pr_create_p = pr_sub.add_parser('create', help='Create a PR (draft by default) from the current branch')
    pr_create_p.add_argument('--title', '-t', help='PR title (defaults to last commit message)')
    pr_create_p.add_argument('--description-file', '-d',
                             help='Path to a file containing the PR description (use "-" for stdin)')
    pr_create_p.add_argument('--repo', '-r', help='Path to git repository (default: current directory)')
    pr_create_p.add_argument('--branch', '-b', help='Source branch (default: current branch)')
    pr_create_p.add_argument('--id', type=int, help='Existing PR ID (skips creation, prints URL)')
    pr_create_p.add_argument('--draft', type=_parse_bool_arg, nargs='?', const=True, default=True,
                             metavar='{true,false}',
                             help='Create PR as draft (default: true). Use "--draft false" to create as active.')

    pr_desc_p = pr_sub.add_parser('desc', help='Show or update PR description')
    pr_desc_p.add_argument('description', nargs='*',
                           help='New description (omit to read current)')
    pr_desc_p.add_argument('--repo', '-r', help='Path to git repository (default: current directory)')
    pr_desc_p.add_argument('--branch', '-b', help='Source branch (default: current branch)')
    pr_desc_p.add_argument('--id', type=int, help='PR ID (alternative to --repo/--branch)')

    pr_diff_p = pr_sub.add_parser('diff', help='Show PR diff (merge-base diff against target)')
    pr_diff_p.add_argument('--repo', '-r', help='Path to git repository (default: current directory)')
    pr_diff_p.add_argument('--branch', '-b', help='Source branch (default: current branch)')
    pr_diff_p.add_argument('--id', type=int, help='PR ID (alternative to --repo/--branch)')
    pr_diff_p.add_argument('--stat', action='store_true', help='Show diffstat summary')
    pr_diff_p.add_argument('--name-only', action='store_true', help='Show only changed file names')
    pr_diff_p.add_argument('diff_args', nargs=argparse.REMAINDER,
                           help='Extra args for git diff (use -- before flags)')

    pr_comments_p = pr_sub.add_parser('comments', help='List or post PR comment threads (human and bot)')
    pr_comments_p.add_argument('message', nargs='*', help='Comment text to post; omit to list threads')
    pr_comments_p.add_argument('--repo', '-r', help='Path to git repository (default: current directory)')
    pr_comments_p.add_argument('--branch', '-b', help='Source branch (default: current branch)')
    pr_comments_p.add_argument('--id', type=int, help='PR ID (alternative to --repo/--branch)')
    pr_comments_p.add_argument('--reply', type=int, metavar='THREAD_ID', help='Reply to this existing thread id')
    pr_comments_p.add_argument('--parent', type=int, default=1, help='In-thread parent comment id (default: 1)')
    pr_comments_p.add_argument('--file', help='Anchor a new thread to this repo-relative file path')
    pr_comments_p.add_argument('--line', type=int, help='Anchor a new thread to this line (requires --file)')
    pr_comments_p.add_argument('--resolve', action='store_true',
                               help='Mark the replied-to thread as fixed (requires --reply)')
    pr_comments_p.add_argument('--active', action='store_true',
                               help='When listing, show only active (unresolved) threads')

    # Init command
    subparsers.add_parser('init', help='Bootstrap shell profile ($PROFILE on Windows, .bashrc→zsh on Linux)')

    # Test command
    subparsers.add_parser('test', help='Run dev.py unit tests')

    # Hidden subcommand: invoked by _spawn_background_sync for detached child
    bg_p = subparsers.add_parser('__bg_sync__', help=argparse.SUPPRESS)
    bg_p.add_argument('payload', help='JSON payload')

    args = parser.parse_args()

    if getattr(args, 'no_color', False):
        Colors.configure(False)

    if args.command == 'init':
        return cmd_init(args)
    elif args.command == 'test':
        return cmd_test(args)
    elif args.command == '__bg_sync__':
        return cmd_bg_sync(args)
    elif args.command == 'ado':
        cmd_map = {
            'set-pat': cmd_ado_set_pat,
            'show-pat': cmd_ado_show_pat,
            'clear-pat': cmd_ado_clear_pat,
            'git': cmd_ado_git,
            'token': cmd_ado_token,
            'credential-helper': cmd_ado_credential_helper,
        }
        if args.ado_command in cmd_map:
            return cmd_map[args.ado_command](args)
        else:
            ado_parser.print_help()
    elif args.command == 'pr':
        cmd_map = {
            'create': cmd_pr_create,
            'desc': cmd_pr_desc,
            'diff': cmd_pr_diff,
            'comments': cmd_pr_comments,
        }
        if args.pr_command in cmd_map:
            return cmd_map[args.pr_command](args)
        else:
            pr_parser.print_help()
    elif args.command == 'repo':
        cmd_map = {
            'add': cmd_repo_add,
            'remove': cmd_repo_remove,
            'delete': cmd_repo_delete,
            'list': cmd_repo_list,
            'sync': cmd_repo_sync,
            'status': cmd_repo_status,
            'old': cmd_repo_old,
            'root': cmd_repo_root,
        }
        if args.repo_command in cmd_map:
            return cmd_map[args.repo_command](args)
        else:
            repo_parser.print_help()
    else:
        parser.print_help()

    return 0

if __name__ == '__main__':
    sys.exit(main())
