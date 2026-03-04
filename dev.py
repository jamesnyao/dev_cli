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
import tempfile
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
RCFILES_DIR = CONFIG_DIR / 'rcfiles'

def get_os_type():
    system = platform.system().lower()
    if system == 'darwin':
        return 'darwin'
    elif system == 'windows':
        return 'windows'
    return 'linux'

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def get_base_path(config=None):
    dev_path = os.getenv('DEV')
    if dev_path:
        return dev_path
    raise ValueError('DEV environment variable is missing.')

def run_git(repo_path, *args):
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_path)] + list(args),
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception:
        return False, ''

def get_remote_url(repo_path):
    success, url = run_git(repo_path, 'remote', 'get-url', 'origin')
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

def compute_repo_name(repo_path, base_path=None):
    """Compute repo name, using parent/name format for gclient enlistments."""
    repo_path = Path(repo_path).resolve()
    
    parent = repo_path.parent
    if (parent / '.gclient').exists():
        return f"{parent.name}/{repo_path.name}"
    
    if base_path:
        base_path = Path(base_path).resolve()
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
        'addedFrom': str(file_path),
        'addedAt': datetime.now(timezone.utc).isoformat()
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

    if not target_path.exists():
        print(f"{Colors.RED}[X]{Colors.NC} Path does not exist: {target_path}")
        return 1

    if target_path.is_file():
        return _add_tracked_file(target_path)

    git_dir = target_path / '.git'
    if not git_dir.exists():
        print(f"{Colors.RED}[X]{Colors.NC} Not a git repository: {target_path}")
        return 1

    remote_url = get_remote_url(target_path)
    if not remote_url:
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} No 'origin' remote found")

    config = load_config()
    base_path = get_base_path()
    repo_name = compute_repo_name(target_path, base_path)
    config['repos'] = [r for r in config['repos'] if r['name'] != repo_name]

    config['repos'].append({
        'name': repo_name,
        'remoteUrl': remote_url,
        'addedFrom': str(target_path),
        'addedAt': datetime.now(timezone.utc).isoformat()
    })

    save_config(config)
    print(f"{Colors.GREEN}Added repository: {repo_name}{Colors.NC}")
    print(f"  Remote: {Colors.CYAN}{remote_url}{Colors.NC}")
    print(f"  Path: {target_path}")
    return 0

def cmd_repo_remove(args):
    """Remove a repository or file from tracking."""
    config = load_config()
    name = args.name

    original_count = len(config['repos'])
    config['repos'] = [r for r in config['repos'] if r['name'] != name]

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
    print("-" * 60)

    if not config['repos']:
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} No repositories tracked yet.")
        print("Use 'dev repo add <path>' to add a repository.")
        return 0

    for repo in sorted(config['repos'], key=lambda r: r['name']):
        print(f"  {repo['name']}")
        print(f"    Remote: {repo.get('remoteUrl', 'N/A')}")
        print(f"    Added: {repo.get('addedAt', 'Unknown')}")
        print()

    print("-" * 60)
    print(f"Total: {Colors.GREEN}{len(config['repos'])}{Colors.NC} repositories")

    files = _get_all_tracked_files()
    if files:
        print(f"\n{Colors.BLUE}Tracked Files:{Colors.NC}")
        print("-" * 60)
        for f in sorted(files, key=lambda x: x['path']):
            print(f"  {f['path']}")
        print("-" * 60)
        print(f"Total: {Colors.GREEN}{len(files)}{Colors.NC} files")

    return 0

def sync_rcfiles():
    """Commit local changes, fetch remote, rebase, and push."""
    print(f"{Colors.BLUE}Syncing rcfiles (dev_scripts)...{Colors.NC}")
    
    run_git(SCRIPT_DIR, 'add', '-A')
    _, status = run_git(SCRIPT_DIR, 'status', '--porcelain')
    if status:
        run_git(SCRIPT_DIR, 'commit', '-m', 'Auto-sync local changes')
    
    success, _ = run_git(SCRIPT_DIR, 'fetch', 'origin')
    if not success:
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} Could not fetch from remote")
        return
    
    default_branch = get_default_branch(SCRIPT_DIR) or 'main'
    _, ahead_behind = run_git(SCRIPT_DIR, 'rev-list', '--left-right', '--count', f'HEAD...origin/{default_branch}')
    
    try:
        ahead, behind = ahead_behind.split()
        ahead, behind = int(ahead), int(behind)
    except:
        ahead, behind = 0, 0
    
    if behind > 0:
        success, output = run_git(SCRIPT_DIR, 'rebase', f'origin/{default_branch}')
        if not success:
            run_git(SCRIPT_DIR, 'rebase', '--abort')
            print(f"{Colors.RED}[X]{Colors.NC} Rebase conflict in rcfiles. Please resolve manually.")
            return
    
    _, ahead_behind = run_git(SCRIPT_DIR, 'rev-list', '--left-right', '--count', f'HEAD...origin/{default_branch}')
    try:
        ahead, _ = ahead_behind.split()
        ahead = int(ahead)
    except:
        ahead = 0
    
    if ahead > 0:
        success, output = run_git(SCRIPT_DIR, 'push')
        if success:
            print(f"{Colors.GREEN}[OK]{Colors.NC} rcfiles pushed ({ahead} commits)")
        else:
            print(f"{Colors.RED}[X]{Colors.NC} Failed to push rcfiles: {output}")
    else:
        print(f"{Colors.GREEN}[OK]{Colors.NC} rcfiles up to date")


def cmd_repo_sync(args):
    """Clone missing repositories and sync tracked files."""
    config = load_config()
    base_path = Path(get_base_path())
    
    _migrate_legacy_rcfiles()
    files_updated_from_workspace = merge_tracked_files_to_repoconfig(base_path)
    sync_rcfiles()
    files_updated_from_remote = apply_tracked_files_to_workspace(base_path)

    for entry in _get_all_tracked_files():
        rel_path = entry['path']
        if files_updated_from_workspace:
            print(f"{Colors.GREEN}[OK]{Colors.NC} {rel_path} (updated from workspace)")
        elif files_updated_from_remote:
            print(f"{Colors.GREEN}[OK]{Colors.NC} {rel_path} (updated from remote)")
        else:
            print(f"{Colors.GREEN}[OK]{Colors.NC} {rel_path}")
    print()

    print(f"{Colors.BLUE}Syncing repositories to: {base_path}{Colors.NC}")
    print("-" * 60)

    if not config['repos']:
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} No repositories to sync.")
        return 0

    base_path.mkdir(parents=True, exist_ok=True)

    synced = skipped = failed = 0
    config_changed = False

    for repo in sorted(config['repos'], key=lambda r: r['name']):
        name = repo['name']
        url = repo.get('remoteUrl', '')
        # Handle nested paths like platform/src
        target_path = base_path / name.replace('/', os.sep)

        if target_path.exists():
            print(f"{Colors.GREEN}[OK]{Colors.NC} {name}")
            check_stale_branch(target_path, name)
            skipped += 1
            continue

        if not url:
            print(f"{Colors.RED}[X]{Colors.NC} Cannot clone {name} (no remote URL)")
            failed += 1
            continue

        # Check if this repo was previously declined on this machine
        devconfig = os.getenv('DEVCONFIG', '')
        skip_list = repo.get('skipOn', [])
        if devconfig and devconfig in skip_list:
            print(f"{Colors.GREEN}[SKIP]{Colors.NC} {name}")
            skipped += 1
            continue

        # Prompt user before cloning a new repo
        try:
            response = input(f"{Colors.YELLOW}[NEW]{Colors.NC} {name} is not set up. Clone it? [y/N] ").strip().lower()
        except EOFError:
            response = 'n'

        if response != 'y':
            if devconfig:
                if 'skipOn' not in repo:
                    repo['skipOn'] = []
                if devconfig not in repo['skipOn']:
                    repo['skipOn'].append(devconfig)
                    config_changed = True
            print(f"{Colors.GREEN}[SKIP]{Colors.NC} {name}")
            skipped += 1
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"{Colors.BLUE}[DOWN] Cloning {name}...{Colors.NC}")
        result = subprocess.run(['git', 'clone', url, str(target_path)])
        if result.returncode == 0:
            print(f"{Colors.GREEN}[OK] Cloned {name}{Colors.NC}")
            synced += 1
        else:
            print(f"{Colors.RED}[X]{Colors.NC} Failed to clone {name}")
            failed += 1

    if config_changed:
        save_config(config)

    print("-" * 60)
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


BUILTIN_FILES = [
    {'path': '.github/copilot-instructions.md'},
    {'path': '.claude/CLAUDE.md'},
]

def _get_all_tracked_files():
    """Return combined list of built-in + user-tracked file paths."""
    config = load_config()
    all_files = list(BUILTIN_FILES)
    builtin_paths = {b['path'] for b in BUILTIN_FILES}
    for f in config.get('files', []):
        if f['path'] not in builtin_paths:
            all_files.append(f)
    return all_files


def _migrate_legacy_rcfiles():
    """Move legacy flat repoconfig files (copilot-instructions.md, CLAUDE.md) into rcfiles/."""
    migrations = [
        (CONFIG_DIR / 'copilot-instructions.md', RCFILES_DIR / '.github' / 'copilot-instructions.md'),
        (CONFIG_DIR / 'CLAUDE.md', RCFILES_DIR / '.claude' / 'CLAUDE.md'),
    ]
    for old, new in migrations:
        if old.exists() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))


def merge_tracked_files_to_repoconfig(base_path):
    """Copy tracked workspace files to repoconfig/rcfiles/ before push."""
    all_files = _get_all_tracked_files()
    if not all_files:
        return False

    changed = False
    base = Path(base_path)

    for entry in all_files:
        rel_path = entry['path']
        workspace_file = base / rel_path.replace('/', os.sep)
        rcfile = RCFILES_DIR / rel_path

        if not workspace_file.exists():
            continue

        # Conflict marker check for markdown files
        if rel_path.endswith('.md'):
            content = workspace_file.read_text(encoding='utf-8')
            if has_real_conflict_markers(content):
                print(f"{Colors.YELLOW}[WARN]{Colors.NC} {rel_path} has conflict markers, skipping")
                print("  Please resolve them first, then run 'dev repo sync' again")
                continue

        workspace_content = workspace_file.read_bytes()
        rcfile_content = rcfile.read_bytes() if rcfile.exists() else b''

        if workspace_content != rcfile_content:
            rcfile.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(workspace_file), str(rcfile))
            changed = True

    return changed


def apply_tracked_files_to_workspace(base_path):
    """Copy tracked files from repoconfig/rcfiles/ to workspace after pull."""
    all_files = _get_all_tracked_files()
    if not all_files:
        return False

    changed = False
    base = Path(base_path)

    for entry in all_files:
        rel_path = entry['path']
        workspace_file = base / rel_path.replace('/', os.sep)
        rcfile = RCFILES_DIR / rel_path

        if not rcfile.exists():
            continue

        # Conflict marker check for markdown files
        if rel_path.endswith('.md'):
            content = rcfile.read_text(encoding='utf-8')
            if has_real_conflict_markers(content):
                print(f"{Colors.YELLOW}[CONFLICT]{Colors.NC} {rel_path} has merge conflicts in repoconfig")
                print(f"  File: {rcfile}")
                print("  Please resolve, then run 'dev repo sync' again")
                continue

        rcfile_content = rcfile.read_bytes()
        workspace_content = workspace_file.read_bytes() if workspace_file.exists() else b''

        if rcfile_content != workspace_content:
            workspace_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(rcfile), str(workspace_file))
            changed = True

    return changed


def cmd_repo_status(args):
    """Show which repos exist on this machine."""
    config = load_config()
    base_path = Path(get_base_path())

    print(f"{Colors.BLUE}Repository Status (base: {base_path}){Colors.NC}")
    print("-" * 60)

    present = missing = 0
    for repo in sorted(config['repos'], key=lambda r: r['name']):
        # Handle nested paths like platform/src
        target_path = base_path / repo['name'].replace('/', os.sep)
        if target_path.exists():
            print(f"{Colors.GREEN}[OK]{Colors.NC} {repo['name']}")
            present += 1
        else:
            print(f"{Colors.RED}[X]{Colors.NC} {repo['name']} {Colors.YELLOW}(missing){Colors.NC}")
            missing += 1

    print("-" * 60)
    print(f"Present: {Colors.GREEN}{present}{Colors.NC} | Missing: {Colors.RED}{missing}{Colors.NC}")

    files = _get_all_tracked_files()
    if files:
        print(f"\n{Colors.BLUE}Tracked Files:{Colors.NC}")
        print("-" * 60)
        f_present = f_missing = 0
        for f in sorted(files, key=lambda x: x['path']):
            workspace_file = base_path / f['path'].replace('/', os.sep)
            if workspace_file.exists():
                print(f"{Colors.GREEN}[OK]{Colors.NC} {f['path']}")
                f_present += 1
            else:
                print(f"{Colors.RED}[X]{Colors.NC} {f['path']} {Colors.YELLOW}(missing){Colors.NC}")
                f_missing += 1
        print("-" * 60)
        print(f"Present: {Colors.GREEN}{f_present}{Colors.NC} | Missing: {Colors.RED}{f_missing}{Colors.NC}")

    return 0


# ============ PYTHON COMMANDS ============

def get_python_command():
    """Get the Python command for this platform"""
    if get_os_type() == 'windows':
        return ['py', '-3']
    return ['python3']

def get_current_python_version():
    """Get the currently installed Python version"""
    try:
        cmd = get_python_command() + ['--version']
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None

def cmd_python_update(args):
    """Update Python to the latest stable version."""
    os_type = get_os_type()

    print(f'{Colors.BLUE}Checking Python installation...{Colors.NC}')
    current = get_current_python_version()
    if current:
        print(f'  Current: {Colors.CYAN}{current}{Colors.NC}')

    if os_type == 'windows':
        print(f'{Colors.BLUE}Updating Python via winget...{Colors.NC}')
        result = subprocess.run(['winget', 'upgrade', 'Python.Python.3.12'], 
                               capture_output=True, text=True)
        if 'No available upgrade found' in result.stdout or 'No installed package found' in result.stdout:
            if 'No installed package found' in result.stdout:
                print(f'{Colors.YELLOW}Not installed, installing...{Colors.NC}')
                result = subprocess.run(['winget', 'install', 'Python.Python.3.12', 
                                        '--accept-package-agreements', '--accept-source-agreements'])
                if result.returncode == 0:
                    print(f'{Colors.GREEN}Python installed successfully{Colors.NC}')
                    print(f'{Colors.YELLOW}Note: Restart your terminal for changes to take effect{Colors.NC}')
                    return 0
            else:
                print(f'{Colors.GREEN}Python is already up to date{Colors.NC}')
                return 0
        elif result.returncode == 0:
            print(f'{Colors.GREEN}Python updated successfully{Colors.NC}')
            print(f'{Colors.YELLOW}Note: Restart your terminal for changes to take effect{Colors.NC}')
            return 0
        print(f'{Colors.RED}[X]{Colors.NC} Failed to update Python')
        return 1

    elif os_type == 'darwin':
        print(f'{Colors.BLUE}Updating Python via Homebrew...{Colors.NC}')
        result = subprocess.run(['brew', 'upgrade', 'python@3.12'])
        if result.returncode != 0:
            result = subprocess.run(['brew', 'install', 'python@3.12'])
        return 0 if result.returncode == 0 else 1

    else:
        print(f'{Colors.BLUE}Updating Python...{Colors.NC}')
        result = subprocess.run(['which', 'apt'], capture_output=True)
        if result.returncode == 0:
            subprocess.run(['sudo', 'apt', 'update'])
            result = subprocess.run(['sudo', 'apt', 'install', '-y', 'python3.12'])
        else:
            result = subprocess.run(['sudo', 'dnf', 'install', '-y', 'python3.12'])
        return 0 if result.returncode == 0 else 1


def cmd_test(args):
    """Run dev.py unit tests"""
    test_file = SCRIPT_DIR / 'test_dev.py'
    if not test_file.exists():
        print(f"{Colors.RED}[X]{Colors.NC} test_dev.py not found")
        return 1
    
    print(f"{Colors.BLUE}Running tests...{Colors.NC}")
    result = subprocess.run([sys.executable, str(test_file)], cwd=str(SCRIPT_DIR))
    return result.returncode


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
        print(f"     Run: dev ado set-pat")
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
    """Run a git command with ADO PAT authentication."""
    pat = get_ado_pat()
    if not pat:
        print(f"{Colors.RED}[X]{Colors.NC} No ADO PAT configured. Run: dev ado set-pat")
        return 1

    git_args = args.git_args
    if git_args and git_args[0] == '--':
        git_args = git_args[1:]
    if not git_args:
        print(f"Usage: dev ado git <git-command> [args...]")
        print(f"Example: dev ado git pull")
        return 1

    helper_script = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write('#!/bin/sh\n'
                    'cat > /dev/null\n'
                    'printf "password=%s\\n" "$ADO_PAT"\n')
            helper_script = f.name
        os.chmod(helper_script, 0o700)

        env = {**os.environ, 'ADO_PAT': pat}
        result = subprocess.run(
            ['git', '-c', 'credential.helper=',
             '-c', 'credential.helper=store',
             '-c', f'credential.helper={helper_script}'] + git_args,
            env=env
        )
        return result.returncode
    finally:
        if helper_script and os.path.exists(helper_script):
            os.unlink(helper_script)


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

    scan_p = repo_sub.add_parser('scan', help='Scan and add all git repos')
    scan_p.add_argument('path', nargs='?', help='Path to scan')


    # python subcommand
    pyenv_parser = subparsers.add_parser('python', help='Python environment management')
    pyenv_sub = pyenv_parser.add_subparsers(dest='python_command')
    pyenv_sub.add_parser('update', help='Update Python to latest stable version')

    # ado subcommand
    ado_parser = subparsers.add_parser('ado', help='Azure DevOps integration')
    ado_sub = ado_parser.add_subparsers(dest='ado_command')
    
    set_pat_p = ado_sub.add_parser('set-pat', help='Set Azure DevOps PAT')
    set_pat_p.add_argument('pat', nargs='?', help='PAT value (will prompt if not provided)')
    
    ado_sub.add_parser('show-pat', help='Show if PAT is configured')
    ado_sub.add_parser('clear-pat', help='Clear stored PAT')

    git_p = ado_sub.add_parser('git', help='Run git with ADO PAT auth')
    git_p.add_argument('git_args', nargs=argparse.REMAINDER, help='Git command and arguments')

    # Test command
    subparsers.add_parser('test', help='Run dev.py unit tests')

    args = parser.parse_args()

    if args.command == 'test':
        return cmd_test(args)
    elif args.command == 'python':
        if args.python_command == 'update':
            return cmd_python_update(args)
        else:
            pyenv_parser.print_help()
    elif args.command == 'ado':
        cmd_map = {
            'set-pat': cmd_ado_set_pat,
            'show-pat': cmd_ado_show_pat,
            'clear-pat': cmd_ado_clear_pat,
            'git': cmd_ado_git,
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


