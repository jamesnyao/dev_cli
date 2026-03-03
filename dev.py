#!/usr/bin/env python3
"""
Dev CLI - Cross-platform development workflow tool
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import shutil

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
    raise ValueError('DEV environment variable is missing. Set it to your workspace root (e.g. D:\\dev or /workspace).')

def run_git(repo_path, *args):
    """Run git command and return output"""
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_path)] + list(args),
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception:
        return False, ''

def _is_ado_url(url):
    """Check if a URL points to Azure DevOps"""
    return 'dev.azure.com' in url or 'visualstudio.com' in url

def _embed_pat_in_url(url, pat):
    """Embed an ADO PAT into an Azure DevOps git URL"""
    parsed = urlparse(url)
    netloc = f"pat:{pat}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))

def get_remote_url(repo_path):
    success, url = run_git(repo_path, 'remote', 'get-url', 'origin')
    return url if success else ''

def get_current_branch(repo_path):
    success, branch = run_git(repo_path, 'rev-parse', '--abbrev-ref', 'HEAD')
    return branch if success else None

def get_default_branch(repo_path):
    # Try to get from origin/HEAD
    success, ref = run_git(repo_path, 'symbolic-ref', 'refs/remotes/origin/HEAD')
    if success and ref:
        return ref.replace('refs/remotes/origin/', '')

    # Fallback to common defaults
    for branch in ['main', 'master']:
        success, _ = run_git(repo_path, 'show-ref', '--verify', '--quiet', f'refs/remotes/origin/{branch}')
        if success:
            return branch
    return None

def get_branch_age_days(repo_path):
    """Get age of last commit on current branch in days"""
    success, timestamp = run_git(repo_path, 'log', '-1', '--format=%ct')
    if not success or not timestamp:
        return 0
    try:
        commit_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        age = datetime.now(timezone.utc) - commit_time
        return age.days
    except Exception:
        return 0

def check_stale_branch(repo_path, name):
    """Check if branch is stale and offer to switch to default"""
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
        print(f"  {Colors.BLUE}Switching to {default}...{Colors.NC}")
        run_git(repo_path, 'fetch', 'origin')
        run_git(repo_path, 'checkout', '-f', default)
        run_git(repo_path, 'reset', '--hard', f'origin/{default}')
        print(f"  {Colors.GREEN}[OK] Switched to {default}{Colors.NC}")

def compute_repo_name(repo_path, base_path=None):
    """Compute a repo name from path, using parent/name format for gclient enlistments."""
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

def cmd_repo_add(args):
    """Add a repository to tracking"""
    repo_path = Path(args.path).resolve()

    if not repo_path.exists():
        print(f"{Colors.RED}[X]{Colors.NC} Directory does not exist: {repo_path}")
        return 1

    git_dir = repo_path / '.git'
    if not git_dir.exists():
        print(f"{Colors.RED}[X]{Colors.NC} Not a git repository: {repo_path}")
        return 1

    remote_url = get_remote_url(repo_path)
    if not remote_url:
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} No 'origin' remote found")

    config = load_config()
    base_path = get_base_path()
    repo_name = compute_repo_name(repo_path, base_path)

    # Remove existing entry if present
    config['repos'] = [r for r in config['repos'] if r['name'] != repo_name]

    config['repos'].append({
        'name': repo_name,
        'remoteUrl': remote_url,
    })

    save_config(config)
    print(f"{Colors.GREEN}Added repository: {repo_name}{Colors.NC}")
    print(f"  Remote: {Colors.CYAN}{remote_url}{Colors.NC}")
    print(f"  Path: {repo_path}")
    return 0

def cmd_repo_remove(args):
    """Remove a repository from tracking"""
    config = load_config()
    original_count = len(config['repos'])
    config['repos'] = [r for r in config['repos'] if r['name'] != args.name]

    if len(config['repos']) == original_count:
        print(f"{Colors.RED}[X]{Colors.NC} Repository '{args.name}' is not tracked")
        return 1

    save_config(config)
    print(f"{Colors.GREEN}Removed repository: {args.name}{Colors.NC}")
    return 0

def cmd_repo_skip(args):
    """Skip a repository on this machine during sync"""
    devconfig = os.getenv('DEVCONFIG', '')
    if not devconfig:
        print(f"{Colors.RED}[X]{Colors.NC} DEVCONFIG environment variable is not set")
        return 1

    config = load_config()
    repo = next((r for r in config['repos'] if r['name'] == args.name), None)
    if not repo:
        print(f"{Colors.RED}[X]{Colors.NC} Repository '{args.name}' is not tracked")
        return 1

    skip_list = repo.get('skipOn', [])
    if devconfig in skip_list:
        print(f"{Colors.YELLOW}[OK]{Colors.NC} {args.name} is already skipped on '{devconfig}'")
        return 0

    if 'skipOn' not in repo:
        repo['skipOn'] = []
    repo['skipOn'].append(devconfig)
    save_config(config)
    print(f"{Colors.GREEN}[OK]{Colors.NC} {args.name} will be skipped on '{devconfig}'")
    return 0

def cmd_repo_json(args):
    """Print the path to repos.json"""
    print(CONFIG_FILE)
    return 0

def sync_rcfiles():
    """Sync rcfiles (dev_scripts config) via commit-fetch-rebase-push."""
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
    """Clone missing repositories and check for stale branches"""
    config = load_config()
    base_path = Path(get_base_path())

    copilot_updated_from_workspace = merge_copilot_instructions_to_repoconfig(base_path)
    claude_updated_from_workspace = merge_claude_instructions_to_repoconfig(base_path)

    sync_rcfiles()

    copilot_updated_from_remote = apply_copilot_instructions_to_workspace(base_path)
    claude_updated_from_remote = apply_claude_instructions_to_workspace(base_path)

    if copilot_updated_from_workspace:
        print(f"{Colors.GREEN}[OK]{Colors.NC} Copilot instructions updated from workspace")
    elif copilot_updated_from_remote:
        print(f"{Colors.GREEN}[OK]{Colors.NC} Copilot instructions updated from remote")
    else:
        print(f"{Colors.GREEN}[OK]{Colors.NC} Copilot instructions up to date")

    if claude_updated_from_workspace:
        print(f"{Colors.GREEN}[OK]{Colors.NC} Claude instructions updated from workspace")
    elif claude_updated_from_remote:
        print(f"{Colors.GREEN}[OK]{Colors.NC} Claude instructions updated from remote")
    else:
        print(f"{Colors.GREEN}[OK]{Colors.NC} Claude instructions up to date")

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

        devconfig = os.getenv('DEVCONFIG', '')
        skip_list = repo.get('skipOn', [])
        if devconfig and devconfig in skip_list:
            print(f"{Colors.GREEN}[SKIP]{Colors.NC} {name}")
            skipped += 1
            continue

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
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
        result = subprocess.run(['git', 'clone', url, str(target_path)], env=env)
        if result.returncode != 0 and _is_ado_url(url):
            pat = get_ado_pat()
            if pat:
                print(f"{Colors.YELLOW}  Retrying with ADO PAT...{Colors.NC}")
                if target_path.exists():
                    shutil.rmtree(target_path)
                auth_url = _embed_pat_in_url(url, pat)
                result = subprocess.run(['git', 'clone', auth_url, str(target_path)], env=env)
        if result.returncode == 0:
            subprocess.run(['git', '-C', str(target_path), 'remote', 'set-url', 'origin', url],
                           capture_output=True)
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
    """Check for real git conflict markers (not in code blocks or inline code)."""
    import re
    lines = content.split('\n')
    in_code_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        if '`' in line:
            continue

        if re.match(r'^<{7}\s', line) or re.match(r'^={7}\s*$', line) or re.match(r'^>{7}\s', line):
            return True

    return False


def merge_copilot_instructions_to_repoconfig(base_path):
    """Copy workspace copilot instructions to repoconfig (before rcfiles push)."""
    copilot_src = CONFIG_DIR / 'copilot-instructions.md'
    copilot_dest = base_path / '.github' / 'copilot-instructions.md'

    if not copilot_dest.exists():
        return False

    dest_content = copilot_dest.read_text(encoding='utf-8')

    if has_real_conflict_markers(dest_content):
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} Workspace copilot-instructions.md has conflict markers")
        print("  Please resolve them first, then run 'dev repo sync' again")
        return False

    src_content = copilot_src.read_text(encoding='utf-8') if copilot_src.exists() else ''

    if src_content.replace('\r\n', '\n') == dest_content.replace('\r\n', '\n'):
        return False

    copilot_src.write_text(dest_content, encoding='utf-8')
    return True


def apply_copilot_instructions_to_workspace(base_path):
    """Apply repoconfig copilot instructions to workspace (after rcfiles pull)."""
    copilot_src = CONFIG_DIR / 'copilot-instructions.md'
    copilot_dest = base_path / '.github' / 'copilot-instructions.md'

    copilot_dest.parent.mkdir(parents=True, exist_ok=True)

    if not copilot_src.exists():
        return False

    src_content = copilot_src.read_text(encoding='utf-8')

    if has_real_conflict_markers(src_content):
        print(f"{Colors.YELLOW}[CONFLICT]{Colors.NC} Copilot instructions have merge conflicts in repoconfig")
        print(f"  File: {copilot_src}")
        print("  Please resolve, then run 'dev repo sync' again")
        return False

    dest_content = copilot_dest.read_text(encoding='utf-8') if copilot_dest.exists() else ''

    if src_content.replace('\r\n', '\n') == dest_content.replace('\r\n', '\n'):
        return False

    copilot_dest.write_text(src_content, encoding='utf-8')
    return True


def merge_claude_instructions_to_repoconfig(base_path):
    """Copy workspace Claude instructions to repoconfig (before rcfiles push)."""
    claude_src = CONFIG_DIR / 'CLAUDE.md'
    claude_dest = base_path / '.claude' / 'CLAUDE.md'

    if not claude_dest.exists():
        return False

    dest_content = claude_dest.read_text(encoding='utf-8')

    if has_real_conflict_markers(dest_content):
        print(f"{Colors.YELLOW}[WARN]{Colors.NC} Workspace CLAUDE.md has conflict markers")
        print("  Please resolve them first, then run 'dev repo sync' again")
        return False

    src_content = claude_src.read_text(encoding='utf-8') if claude_src.exists() else ''

    if src_content.replace('\r\n', '\n') == dest_content.replace('\r\n', '\n'):
        return False

    claude_src.write_text(dest_content, encoding='utf-8')
    return True


def apply_claude_instructions_to_workspace(base_path):
    """Apply repoconfig Claude instructions to workspace (after rcfiles pull)."""
    claude_src = CONFIG_DIR / 'CLAUDE.md'
    claude_dest = base_path / '.claude' / 'CLAUDE.md'

    claude_dest.parent.mkdir(parents=True, exist_ok=True)

    if not claude_src.exists():
        return False

    src_content = claude_src.read_text(encoding='utf-8')

    if has_real_conflict_markers(src_content):
        print(f"{Colors.YELLOW}[CONFLICT]{Colors.NC} Claude instructions have merge conflicts in repoconfig")
        print(f"  File: {claude_src}")
        print("  Please resolve, then run 'dev repo sync' again")
        return False

    dest_content = claude_dest.read_text(encoding='utf-8') if claude_dest.exists() else ''

    if src_content.replace('\r\n', '\n') == dest_content.replace('\r\n', '\n'):
        return False

    claude_dest.write_text(src_content, encoding='utf-8')
    return True


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

def cmd_python_list(args):
    """List installed Python versions"""
    os_type = get_os_type()
    found = []

    if os_type == 'windows':
        # Use py launcher to list versions
        try:
            result = subprocess.run(['py', '--list'], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                print(result.stdout.strip())
                return 0
        except FileNotFoundError:
            pass
        # Fallback: check common names
        for name in ['python3', 'python']:
            path = shutil.which(name)
            if path:
                try:
                    r = subprocess.run([path, '--version'], capture_output=True, text=True)
                    if r.returncode == 0:
                        found.append((r.stdout.strip(), path))
                except Exception:
                    pass
    else:
        # Find all python3* executables on PATH
        seen = set()
        for directory in os.environ.get('PATH', '').split(os.pathsep):
            try:
                entries = os.listdir(directory)
            except OSError:
                continue
            for entry in sorted(entries):
                if not (entry == 'python3' or (entry.startswith('python3.') and not entry.endswith('-config'))):
                    continue
                full = os.path.join(directory, entry)
                if not os.path.isfile(full) or not os.access(full, os.X_OK):
                    continue
                real = os.path.realpath(full)
                if real in seen:
                    continue
                seen.add(real)
                try:
                    r = subprocess.run([full, '--version'], capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        found.append((r.stdout.strip(), full))
                except Exception:
                    pass

    if not found:
        print(f"{Colors.YELLOW}No Python installations found{Colors.NC}")
        return 1

    for version, path in found:
        print(f"  {Colors.CYAN}{version}{Colors.NC}  {path}")
    return 0


def cmd_python_update(args):
    """Update Python to the latest stable version"""
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
    """Set the Azure DevOps PAT"""
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
    # Set file permissions to owner-only on Unix
    if sys.platform != 'win32':
        os.chmod(ADO_PAT_FILE, 0o600)

    print(f"{Colors.GREEN}[OK] ADO PAT saved to {ADO_PAT_FILE}{Colors.NC}")
    print(f"     {Colors.YELLOW}Note: Keep this file secure and do not commit it.{Colors.NC}")
    return 0

def cmd_ado_show_pat(args):
    """Show if ADO PAT is configured (not the actual value)"""
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
    """Clear the stored ADO PAT"""
    if ADO_PAT_FILE.exists():
        ADO_PAT_FILE.unlink()
        print(f"{Colors.GREEN}[OK] ADO PAT cleared{Colors.NC}")
    else:
        print(f"{Colors.YELLOW}No ADO PAT was configured{Colors.NC}")
    return 0


def main():
    parser = argparse.ArgumentParser(description='Dev CLI - Development workflow tool')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # repo subcommand
    repo_parser = subparsers.add_parser('repo', help='Manage tracked repositories')
    repo_sub = repo_parser.add_subparsers(dest='repo_command')

    add_p = repo_sub.add_parser('add', help='Add a repository to tracking')
    add_p.add_argument('path', help='Path to the repository')

    remove_p = repo_sub.add_parser('remove', help='Remove a repository from tracking')
    remove_p.add_argument('name', help='Name of the repository')

    repo_sub.add_parser('sync', help='Clone missing repositories')

    skip_p = repo_sub.add_parser('skip', help='Skip a repo on this machine during sync')
    skip_p.add_argument('name', help='Name of the repository to skip')

    repo_sub.add_parser('json', help='Print path to repos.json')

    # python subcommand
    pyenv_parser = subparsers.add_parser('python', help='Python environment management')
    pyenv_sub = pyenv_parser.add_subparsers(dest='python_command')
    pyenv_sub.add_parser('list', help='List installed Python versions')
    pyenv_sub.add_parser('update', help='Update Python to latest stable version')

    # ado subcommand
    ado_parser = subparsers.add_parser('ado', help='Azure DevOps integration')
    ado_sub = ado_parser.add_subparsers(dest='ado_command')

    set_pat_p = ado_sub.add_parser('set-pat', help='Set Azure DevOps PAT')
    set_pat_p.add_argument('pat', nargs='?', help='PAT value (will prompt if not provided)')

    ado_sub.add_parser('show-pat', help='Show if PAT is configured')
    ado_sub.add_parser('clear-pat', help='Clear stored PAT')

    # Test command
    subparsers.add_parser('test', help='Run dev.py unit tests')

    args = parser.parse_args()

    if args.command == 'test':
        return cmd_test(args)
    elif args.command == 'python':
        if args.python_command == 'update':
            return cmd_python_update(args)
        elif args.python_command == 'list':
            return cmd_python_list(args)
        else:
            pyenv_parser.print_help()
    elif args.command == 'ado':
        cmd_map = {
            'set-pat': cmd_ado_set_pat,
            'show-pat': cmd_ado_show_pat,
            'clear-pat': cmd_ado_clear_pat,
        }
        if args.ado_command in cmd_map:
            return cmd_map[args.ado_command](args)
        else:
            ado_parser.print_help()
    elif args.command == 'repo':
        cmd_map = {
            'add': cmd_repo_add,
            'remove': cmd_repo_remove,
            'sync': cmd_repo_sync,
            'skip': cmd_repo_skip,
            'json': cmd_repo_json,
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
