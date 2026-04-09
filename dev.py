#!/usr/bin/env python3
"""
Dev CLI - Cross-platform development workflow tool
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Colors (ANSI escape codes, disabled on Windows cmd)
class Colors:
    if sys.platform == 'win32' and 'WT_SESSION' not in os.environ:
        RED = YELLOW = GREEN = BLUE = CYAN = NC = ''
    else:
        RED = '\033[0;31m'
        YELLOW = '\033[1;33m'
        GREEN = '\033[0;32m'
        BLUE = '\033[0;34m'
        CYAN = '\033[0;36m'
        NC = '\033[0m'

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = SCRIPT_DIR / 'repoconfig'
CONFIG_FILE = CONFIG_DIR / 'repos.json'
ADO_PAT_FILE = CONFIG_DIR / 'ado_pat.txt'
ADO_TOKEN_CACHE_FILE = CONFIG_DIR / 'ado_token_cache.json'
ADO_TOKEN_CACHE_SECONDS = 2400  # 40 minutes (tokens last ~60 min)
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
    import re
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

def get_remote_url(repo_path, normalize=False):
    success, url = run_git(repo_path, 'remote', 'get-url', 'origin')
    if success and normalize:
        return normalize_github_url(url)
    return url if success else ''

def get_current_branch(repo_path):
    success, branch = run_git(repo_path, 'rev-parse', '--abbrev-ref', 'HEAD')
    return branch if success else None

def get_default_branch(repo_path):
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

def check_stale_branch(repo_path, name):
    current = get_current_branch(repo_path)
    if not current or current == 'HEAD':
        return

    if current.startswith('mirror/'):
        return

    age_days = get_branch_age_days(repo_path)
    if age_days < 14:
        return

    default = get_default_branch(repo_path)
    if not default or current == default:
        return

    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {name}: branch '{current}' is {age_days} days old")
    try:
        response = input(f"  Switch to '{default}'? [Y/n] ").strip().lower() or 'y'
    except EOFError:
        return

    if response == 'y':
        run_git(repo_path, 'fetch', 'origin')
        run_git(repo_path, 'checkout', '-f', default)
        run_git(repo_path, 'reset', '--hard', f'origin/{default}')
        print(f"{Colors.GREEN}[OK] Switched to {default}{Colors.NC}")

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
    """Add a single file to tracking for cross-machine sync."""
    base_path = Path(get_base_path()).resolve()
    file_path = file_path.resolve()

    try:
        rel_path = file_path.relative_to(base_path)
    except ValueError:
        print(f"{Colors.RED}[X]{Colors.NC} File must be under workspace root: {base_path}")
        return 1

    rel_str = str(rel_path).replace('\\', '/')
    config = load_config()
    if 'files' not in config:
        config['files'] = []
    config['files'] = [f for f in config['files'] if f['path'] != rel_str]
    config['files'].append({
        'path': rel_str,
    })

    dest = RCFILES_DIR / rel_str
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(file_path), str(dest))

    save_config(config)
    print(f"{Colors.GREEN}Added file: {rel_str}{Colors.NC}")
    print(f"  Synced to: {dest}")
    return 0


def cmd_repo_add(args):
    """Add a repository or file to tracking."""
    target_path = Path(args.path).resolve()
    display_path = Path(os.path.abspath(args.path))

    if not target_path.exists():
        print(f"{Colors.RED}[X]{Colors.NC} Path does not exist: {target_path}")
        return 1

    if target_path.is_file():
        return _add_tracked_file(target_path)

    git_dir = target_path / '.git'
    if not git_dir.exists():
        print(f"{Colors.RED}[X]{Colors.NC} Not a git repository: {target_path}")
        return 1

    remote_url = get_remote_url(target_path, normalize=True)
    if not remote_url:
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} No 'origin' remote found")

    config = load_config()
    base_path = get_base_path()
    repo_name = compute_repo_name(display_path, base_path)
    config['repos'] = [r for r in config['repos'] if r['path'] != repo_name]

    config['repos'].append({
        'path': repo_name,
        'remoteUrl': remote_url,
    })

    save_config(config)
    print(f"{Colors.GREEN}Added repository: {repo_name}{Colors.NC}")
    print(f"  Remote: {Colors.CYAN}{remote_url}{Colors.NC}")
    print(f"  Path: {display_path}")
    return 0

def cmd_repo_remove(args):
    """Remove a repository or file from tracking."""
    config = load_config()
    name = args.name

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
        if rcfile.exists():
            rcfile.unlink()
        save_config(config)
        print(f"{Colors.GREEN}Removed file: {name}{Colors.NC}")
        return 0

    print(f"{Colors.RED}[X]{Colors.NC} '{name}' is not tracked")
    return 1

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
            print(f"{Colors.GREEN}[OK]{Colors.NC} rcfiles pushed ({ahead} commits)")
            for line in log.strip().splitlines():
                print(f"     {line}")
        else:
            print(f"{Colors.RED}[X]{Colors.NC} Failed to push rcfiles: {output}")
    else:
        if not pulled:
            print(f"{Colors.GREEN}[OK]{Colors.NC} rcfiles up to date")


def _ensure_link(link_path, repo_path):
    """Create a symlink from link_path -> repo_path if needed."""
    if link_path == repo_path:
        return
    if link_path.is_symlink():
        if link_path.resolve() == repo_path.resolve():
            return
        link_path.unlink()
    elif link_path.exists():
        return
    link_path.parent.mkdir(parents=True, exist_ok=True)
    link_path.symlink_to(repo_path, target_is_directory=True)


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
            print(f"{Colors.RED}[X]{Colors.NC} Rebase conflict in dev_scripts. Please resolve manually.")
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

    config = load_config()
    base_path = Path(get_base_path())

    print(f"{Colors.BLUE}Syncing rcfiles...{Colors.NC}")
    pulled = False
    pulled_log = os.environ.pop('_DEV_PULLED_RCFILES', None)
    if pulled_log:
        pulled = True
        print(f"{Colors.GREEN}[OK]{Colors.NC} rcfiles updated from remote:")
        for line in pulled_log.strip().splitlines():
            print(f"     {Colors.YELLOW}{line}{Colors.NC}")
    sync_rcfiles_push(pulled=pulled)
    print()

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

        if repo_path.exists():
            if link_to is not None:
                _ensure_link(link_path, repo_path)
            print(f"{Colors.GREEN}[OK]{Colors.NC} {name}")
            check_stale_branch(repo_path, name)
            skipped += 1
            continue

        # Prompt user before cloning a new repo
        try:
            response = input(f"{Colors.YELLOW}[NEW]{Colors.NC} {name} is not set up. Clone it? [y/N] ").strip().lower()
        except EOFError:
            response = 'n'

        if response != 'y':
            if devconfig:
                repo.setdefault('skipOn', []).append(devconfig)
                config_changed = True
            print(f"{Colors.GREEN}[SKIP]{Colors.NC} {name}")
            skipped += 1
            continue

        repo_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"{Colors.BLUE}[DOWN] Cloning {name}...{Colors.NC}")
        result = subprocess.run(['git', 'clone', url, str(repo_path)])
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
    import re
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

def _get_all_tracked_files():
    """Return user-tracked file paths from config."""
    config = load_config()
    return config.get('files', [])


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
            direction = 'local'
        elif rc_exists and not tgt_exists:
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


def _parse_ado_remote(remote_url):
    """Parse an ADO remote URL into (org, project, repo) or None."""
    import re
    # https://dev.azure.com/{org}/{project}/_git/{repo}
    # https://{org}@dev.azure.com/{org}/{project}/_git/{repo}
    m = re.match(r'https://(?:[^@]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/(.+?)(?:\.git)?$', remote_url)
    if m:
        return m.group(1), m.group(2), m.group(3)
    # https://{org}.visualstudio.com/{collection}/{project}/_git/{repo}
    m = re.match(r'https://([^.]+)\.visualstudio\.com/[^/]+/([^/]+)/_git/(.+?)(?:\.git)?$', remote_url)
    if m:
        return m.group(1), m.group(2), m.group(3)
    return None


def _fetch_ado_prs_for_branches(ado_info, branch_names):
    """Fetch ADO PRs for a list of branch names. Returns {branch_name: [pr_dict, ...]}."""
    import urllib.request
    import urllib.error

    org, project, repo = ado_info
    token = get_ado_token()
    if not token:
        return None

    result = {}
    for branch_name in branch_names:
        ref = f'refs/heads/{branch_name}'
        url = (f'https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}'
               f'/pullrequests?searchCriteria.sourceRefName={ref}'
               f'&searchCriteria.status=all&api-version=7.0')
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                prs = data.get('value', [])
                if prs:
                    result[branch_name] = [
                        {'id': pr['pullRequestId'], 'title': pr.get('title', ''),
                         'status': pr.get('status', '')}
                        for pr in prs
                    ]
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            continue
    return result


def cmd_repo_old(args):
    """List or delete old branches with user/developer/ prefix."""
    from datetime import timedelta

    repo_path = args.path
    if not repo_path:
        repo_path = os.getcwd()
    repo_path = Path(repo_path).resolve()

    if not (repo_path / '.git').exists():
        print(f"{Colors.RED}Error: {repo_path} is not a git repository{Colors.NC}")
        return 1

    prefix = args.prefix or 'user/developer/'
    days = args.days or 30
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    print(f"{Colors.BLUE}Scanning for branches older than {days} days with prefix '{prefix}'...{Colors.NC}")
    print(f"Repository: {repo_path}")
    print(f"Cutoff date: {cutoff.strftime('%Y-%m-%d')}")

    # Get all remote branches matching the prefix
    result = subprocess.run(
        ['git', '-C', str(repo_path), 'branch', '-r', '--list', f'*{prefix}*'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"{Colors.RED}Error listing branches: {result.stderr}{Colors.NC}")
        return 1

    branches = [b.strip() for b in result.stdout.strip().split('\n') if b.strip()]
    if not branches:
        print(f"{Colors.GREEN}No remote branches found with prefix '{prefix}'{Colors.NC}")
        return 0

    old_branches = []
    for branch in branches:
        # Get the last commit date for each branch
        result = subprocess.run(
            ['git', '-C', str(repo_path), 'log', '-1', '--format=%cI', branch],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            continue

        date_str = result.stdout.strip()
        if not date_str:
            continue

        try:
            commit_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if commit_date < cutoff:
                age_days = (datetime.now(timezone.utc) - commit_date).days
                old_branches.append((branch, commit_date, age_days))
        except ValueError:
            continue

    if not old_branches:
        print(f"{Colors.GREEN}No branches older than {days} days found{Colors.NC}")
        return 0

    # Sort by age (oldest first)
    old_branches.sort(key=lambda x: x[1])

    # Look up linked PRs from ADO
    remote_url = get_remote_url(repo_path)
    ado_info = _parse_ado_remote(remote_url) if remote_url else None
    pr_map = {}
    if ado_info:
        branch_names = [b.replace('remotes/origin/', '').replace('origin/', '')
                        for b, _, _ in old_branches]
        fetched = _fetch_ado_prs_for_branches(ado_info, branch_names)
        if fetched:
            pr_map = fetched
        org, project = ado_info[0], ado_info[1]

    print(f"\n{Colors.YELLOW}Found {len(old_branches)} old branches:{Colors.NC}\n")
    for branch, commit_date, age_days in old_branches:
        # Strip 'remotes/origin/' prefix for display
        display_name = branch.replace('remotes/origin/', 'origin/')
        branch_name = branch.replace('remotes/origin/', '').replace('origin/', '')
        print(f"  {display_name}")
        print(f"    Last commit: {commit_date.strftime('%Y-%m-%d')} ({age_days} days ago)")
        if branch_name in pr_map:
            for pr in pr_map[branch_name]:
                status_color = Colors.GREEN if pr['status'] == 'completed' else (
                    Colors.YELLOW if pr['status'] == 'active' else Colors.NC)
                pr_url = f'https://dev.azure.com/{org}/{project}/_git/pullrequest/{pr["id"]}'
                print(f"    PR !{pr['id']} [{status_color}{pr['status']}{Colors.NC}] {pr['title']}")
                print(f"       {pr_url}")

    if not args.delete:
        print(f"\n{Colors.CYAN}To delete these branches, run:{Colors.NC}")
        print("  dev repo old --delete")
        return 0

    # Delete mode
    print(f"\n{Colors.RED}WARNING: This will delete {len(old_branches)} remote branches!{Colors.NC}")
    confirm = input("Type 'yes' to confirm deletion: ")
    if confirm.lower() != 'yes':
        print("Aborted.")
        return 0

    deleted = 0
    failed = 0
    for branch, commit_date, age_days in old_branches:
        # Extract branch name without remote prefix
        remote_branch = branch.replace('remotes/origin/', '').replace('origin/', '')
        print(f"Deleting origin/{remote_branch}...", end=' ')

        result = subprocess.run(
            ['git', '-C', str(repo_path), 'push', 'origin', '--delete', remote_branch],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"{Colors.GREEN}OK{Colors.NC}")
            deleted += 1
        else:
            print(f"{Colors.RED}FAILED{Colors.NC}")
            if result.stderr:
                print(f"    {result.stderr.strip()}")
            failed += 1

    print(f"Deleted: {Colors.GREEN}{deleted}{Colors.NC} | Failed: {Colors.RED}{failed}{Colors.NC}")

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
        print(f"{Colors.RED}[X]{Colors.NC} Could not determine $PROFILE path")
        return 1

    profile_path = Path(result.stdout.strip())

    if profile_path.exists():
        content = profile_path.read_text(encoding='utf-8')
        if '.psrc.ps1' in content:
            print(f"{Colors.GREEN}[OK]{Colors.NC} $PROFILE already sources .psrc.ps1")
            return 0

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(PSRC_CONTENT, encoding='utf-8')
    print(f"{Colors.GREEN}[OK]{Colors.NC} Wrote $PROFILE -> .psrc.ps1 ({profile_path})")
    return 0


def _init_unix():
    """Ensure zsh is the running shell, or .bashrc execs into it."""
    shell = os.environ.get('SHELL', '')

    if 'zsh' in shell:
        print(f"{Colors.GREEN}[OK]{Colors.NC} zsh is the default shell")
        return 0

    if get_os_type() == 'linux' and not shutil.which('zsh'):
        print(f"{Colors.BLUE}Installing zsh...{Colors.NC}")
        result = subprocess.run(
            ['sudo', 'apt-get', 'install', '-y', 'zsh'],
            capture_output=True, text=True)
        if result.returncode != 0:
            print(f"{Colors.RED}[X]{Colors.NC} Failed to install zsh: {result.stderr}")
            return 1
        print(f"{Colors.GREEN}[OK]{Colors.NC} zsh installed")

    bashrc = Path.home() / '.bashrc'
    if not bashrc.exists():
        bashrc.write_text(f"{BASHRC_SOURCE_LINE}\n", encoding='utf-8')
        print(f"{Colors.GREEN}[OK]{Colors.NC} Created .bashrc with zsh exec")
        return 0

    content = bashrc.read_text(encoding='utf-8')
    if BASHRC_SOURCE_LINE in content:
        print(f"{Colors.GREEN}[OK]{Colors.NC} .bashrc already execs into zsh")
        return 0

    with open(bashrc, 'a', encoding='utf-8') as f:
        f.write(f"\n{BASHRC_SOURCE_LINE}\n")
    print(f"{Colors.GREEN}[OK]{Colors.NC} Added zsh exec to .bashrc")
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
        print(f"{Colors.RED}[X]{Colors.NC} test_dev.py not found")
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
            print(f"{Colors.RED}[X]{Colors.NC} No PAT provided")
            return 1

    if not pat:
        print(f"{Colors.RED}[X]{Colors.NC} PAT cannot be empty")
        return 1

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ADO_PAT_FILE.write_text(pat)
    if sys.platform != 'win32':
        os.chmod(ADO_PAT_FILE, 0o600)

    print(f"{Colors.GREEN}[OK] ADO PAT saved to {ADO_PAT_FILE}{Colors.NC}")
    print(f"     {Colors.YELLOW}Note: Keep this file secure and do not commit it.{Colors.NC}")
    return 0

def cmd_ado_show_pat(args):
    """Show if ADO PAT is configured."""
    pat = get_ado_pat()
    if pat:
        masked = pat[:4] + '*' * (len(pat) - 8) + pat[-4:] if len(pat) > 8 else '****'
        print(f"{Colors.GREEN}[OK] ADO PAT is configured: {masked}{Colors.NC}")
        print(f"     Stored at: {ADO_PAT_FILE}")
    else:
        print(f"{Colors.YELLOW}ADO PAT is not configured{Colors.NC}")
        print("     Run: dev ado set-pat")
    return 0

def cmd_ado_clear_pat(args):
    """Clear the stored ADO PAT."""
    if ADO_PAT_FILE.exists():
        ADO_PAT_FILE.unlink()
        print(f"{Colors.GREEN}[OK] ADO PAT cleared{Colors.NC}")
    else:
        print(f"{Colors.YELLOW}No ADO PAT was configured{Colors.NC}")
    return 0

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
        print(f"{Colors.RED}[X]{Colors.NC} Failed to get ADO token. Run: az login")
        return 1

    result = subprocess.run(
        ['git', '-c', f'http.extraheader=Authorization: Bearer {token}'] + git_args)
    return result.returncode


def _get_cached_ado_token():
    """Return cached token if still valid, else None."""
    if not ADO_TOKEN_CACHE_FILE.exists():
        return None
    try:
        cache = json.loads(ADO_TOKEN_CACHE_FILE.read_text())
        expires = cache.get('expires', 0)
        if datetime.now(timezone.utc).timestamp() < expires:
            return cache.get('token')
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _cache_ado_token(token):
    """Cache a token with expiry."""
    cache = {
        'token': token,
        'expires': datetime.now(timezone.utc).timestamp() + ADO_TOKEN_CACHE_SECONDS,
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
        print(f"{Colors.RED}[X]{Colors.NC} Failed to get ADO token. Run: az login", file=sys.stderr)
        return 1
    print(token)
    return 0


def main():
    parser = argparse.ArgumentParser(description='Dev CLI - Development workflow tool')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # repo subcommand
    repo_parser = subparsers.add_parser('repo', help='Manage tracked repositories')
    repo_sub = repo_parser.add_subparsers(dest='repo_command')

    add_p = repo_sub.add_parser('add', help='Add a repository or file to tracking')
    add_p.add_argument('path', help='Path to a repository or file')

    remove_p = repo_sub.add_parser('remove', help='Remove a repository or file from tracking')
    remove_p.add_argument('name', help='Name of the repository or file path')

    repo_sub.add_parser('list', help='List all tracked repositories')
    repo_sub.add_parser('sync', help='Clone missing repositories')
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

    # Init command
    subparsers.add_parser('init', help='Bootstrap shell profile ($PROFILE on Windows, .bashrc→zsh on Linux)')

    # Test command
    subparsers.add_parser('test', help='Run dev.py unit tests')

    args = parser.parse_args()

    if args.command == 'init':
        return cmd_init(args)
    elif args.command == 'test':
        return cmd_test(args)
    elif args.command == 'ado':
        cmd_map = {
            'set-pat': cmd_ado_set_pat,
            'show-pat': cmd_ado_show_pat,
            'clear-pat': cmd_ado_clear_pat,
            'git': cmd_ado_git,
            'token': cmd_ado_token,
        }
        if args.ado_command in cmd_map:
            return cmd_map[args.ado_command](args)
        else:
            ado_parser.print_help()
    elif args.command == 'repo':
        cmd_map = {
            'add': cmd_repo_add,
            'remove': cmd_repo_remove,
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
