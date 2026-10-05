# dev_cli

Set up your development shell, manage repositories, and work with Azure DevOps.
Use it standalone; a private dotfiles repository is optional.

## Install

Run one command. You do not need Python installed.

**Windows PowerShell:**

```powershell
irm https://raw.githubusercontent.com/jamesnyao/dev_cli/main/install.ps1 | iex
```

**macOS or Linux:**

```bash
bash -o pipefail -c 'curl -fsSL https://raw.githubusercontent.com/jamesnyao/dev_cli/main/install.sh | bash'
```

The installer clones `~/dev_cli` (or initializes an existing submodule), then
runs `dev init`. It provisions Python through uv, creates your configuration,
and registers PowerShell, bash, or zsh startup hooks. Existing files are preserved.
On a fresh Mac, finish Apple's Command Line Tools prompt; installation resumes automatically.
Open a new terminal to use `dev`, `python`, and `python3`.

## Customize

Edit `~/dev_config.json`. Set `pythonVersion` to an exact version such as
`3.12.10`, then run `dev init`. This Python becomes the default on PATH;
dev_cli never falls back to another installed Python.

Set `defaultShell` to `"bash"` on Windows to add Git Bash to Windows Terminal,
make it the default profile, and register the Bash startup hooks. The default
`"system"` value preserves the existing Windows Terminal default.

Put custom settings in `~/dev_env/env_windows.ps1` for PowerShell,
`env_windows.sh` for Git Bash, `env_mac.sh` for macOS, or `env_linux.sh` for
Linux (including WSL). These hooks run **last**, after dev_cli sets up Python
and PATH, so you can override either. Rerunning `dev init` preserves your
platform-specific customizations.

PowerShell caches the Python path, workspace root, and identity that it reads at
startup in `~/.dev_temp/dev_cli/profile-cache.json`, so warm shells start without
launching Python. The cache refreshes when `DEVCONFIG` or `dev_config.json`
changes; delete the file to force a refresh. Custom hooks can reuse it with
`Invoke-DevCached repo, root`.

Add a `workspaceRoots` entry for each machine and set `DEVCONFIG` to its key.
See the annotated [configuration defaults](dev_config.json) for other settings.
Keep `.dev_temp/` out of Git; it holds local runtimes, caches, and credentials.

`dev init` prompts for an AI provider, suggesting `ghcopilot` by default.
Press Enter to install GitHub Copilot CLI and the `dev-cli` skill, or choose
`none` to skip AI setup. Choose `claude` to select its separate setup path
(not implemented yet; it reports an error without installing Copilot).
Later runs suggest your saved choice; noninteractive runs use the configured default.
Run `copilot` and sign in yourself. `ai.skills` selects bundled workflow skills
(default `["dev-cli"]`); use `[]` to install none. Personal skills stay untouched.

## AI chat

Run `ai` from Bash, zsh, or PowerShell. The launchers live in `dev_cli`,
which `dev init` adds to PATH, and share the same `dev ai` implementation.
They use `ai.provider` (default `ghcopilot`) and install the CLI and selected
skills if the executable is missing. Sign in with `/login` if needed;
the wrapper does not configure authentication.

Chat always starts in the configured `workspaceRoots[DEVCONFIG]` directory with
**full permissions** by default (`copilot --allow-all`, equivalent to YOLO
mode). The workspace root is also passed through `--add-dir`, granting access
to everything beneath it and loading its `.github` skills and agents as trusted
configuration. All arguments and input are forwarded, and the provider's exit
status is preserved.

```text
ai
ai --resume
ai -p "Explain this repository"
```

Use `copilot` directly when you want its normal permission prompts.
`ai.provider: none` disables chat; Claude chat is not implemented yet and
reports an error rather than falling back to Copilot.

## Sync

```text
dev repo add <local-repository>
dev repo sync
```

Set `bootstrap: true` on the tool repository, or on its private parent when
using a submodule. Sync updates that repository and its submodules **first**,
restarts with the updated code, then processes only the other repositories.
Omit `bootstrap` to disable self-updates.

Sync can commit and push changes in the bootstrap repository. Commit and push
tool submodule changes separately; dirty or unpublished submodules stop sync.
Run `dev <command> -h` for options.

## Develop

Install pylint with `python -m pip install pylint`, then run `dev test`.
The suite covers onboarding, launchers, and sync using local Git repositories.
Run it natively on Windows and Unix after changing shell behavior.

[MIT license](LICENSE).
