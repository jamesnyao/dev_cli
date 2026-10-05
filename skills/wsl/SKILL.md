---
name: wsl
description: >-
  Running commands in WSL from Windows with a dev_cli setup. Use when invoking
  WSL commands, validating Linux behavior from a Windows host, or setting up
  Git and Azure DevOps authentication inside WSL.
license: MIT
---

# WSL

When a Windows host runs a WSL command, the Linux shell profile is not sourced
by default, so `$DEV`, `$DEVCONFIG`, PATH entries, and shell functions such as
`dev set` are missing.

## Separate enlistment

WSL has its **own** home repository, `~/dev_cli` checkout, and native workspace.
Resolve the workspace with `dev repo root`; `DEVCONFIG` selects the matching
`workspaceRoots` entry in `~/dev_config.json`. Never assume a username, drive, or
the Windows workspace. Windows and WSL checkouts have independent branches,
edits, and build outputs.

- Keep Linux work in the WSL enlistment and Windows work on Windows. Do not use
  WSL to operate on the Windows workspace through `/mnt/<drive>`.
- `/mnt/*` paths are Windows filesystems: slow and prone to line-ending and
  permission issues. Never build, test, or do heavy IO there. Small one-off
  copies and calling Windows executables are exceptions.
- `dev repo sync` inside WSL is independent of Windows and can commit and push.
  Run it only when publishing is authorized. For a pull-only update, preserve
  local work, fast-forward the home repository, then update its submodules.

## Running commands

**Always** run WSL commands through a login interactive shell so the profile
loads, even for simple utilities; consistency avoids surprises when a command
later depends on the environment. Use the shell `dev init` registered (zsh shown):

```powershell
wsl -e zsh -ilc 'echo $DEV'
```

Never use bare `wsl --` or `wsl -e bash -c` for work that needs the
environment; they skip the profile.

PowerShell here-strings piped into WSL carry CRLF line endings. For multi-line
scripts, write a file with LF endings and run it instead.

## Line endings

Files edited on Windows may have CRLF endings that break shell scripts in WSL.
After copying a file, check it and convert it if needed:

```powershell
wsl -e zsh -ilc 'file "$HOME/path/script.sh"'
wsl -e zsh -ilc 'sed -i "s/\r$//" "$HOME/path/script.sh"'
```

## Git authentication

WSL does not inherit Windows SSH configuration or credentials. Prefer native
SSH configuration in WSL. If Windows already has the required SSH aliases, a
repository-local `core.sshCommand` can use Windows OpenSSH without copying keys:

```powershell
wsl -e zsh -ilc 'git -C "$HOME" config core.sshCommand "/mnt/c/Windows/System32/OpenSSH/ssh.exe -o BatchMode=yes"'
```

Relative submodule URLs inherit the parent's remote. On first initialization,
pass the command with `git -c core.sshCommand=... submodule update --init --recursive`
so the clone receives it, then configure the initialized submodule for later
fetches. Do not change global Git transport settings or copy keys into a repository.

For Azure DevOps repositories, use `dev ado git <args>`; it injects a bearer
token from `az account get-access-token`, so it needs `az login` inside WSL but
no PAT.

## Profiles and validation

Before replacing existing profiles, preserve `.zshrc`/`.bashrc` customizations
and port them into the private Linux hook, keeping the profile redirects thin.
Use `$HOME` for per-user paths, never another machine's login name.

Run tests against the native WSL checkout, not the Windows files:

```powershell
wsl -e zsh -ilc 'cd ~/dev_cli && dev test'
```
