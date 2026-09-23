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

Put custom settings in `~/dev_env/env_windows.ps1`, `env_mac.sh`, or
`env_linux.sh` (also WSL). These hooks run **last**, after dev_cli sets up
Python and PATH, so you can override either. Rerunning `dev init` preserves
customizations, including legacy `env.ps1`/`env.sh` hooks.

Add a `workspaceRoots` entry for each machine and set `DEVCONFIG` to its key.
See the annotated [configuration defaults](dev_config.json) for other settings.
Keep `.dev_temp/` out of Git; it holds local runtimes, caches, and credentials.

To install GitHub Copilot CLI and the bundled `dev-cli` skill, set
`"ai": {"provider": "ghcopilot"}` and rerun `dev init`. Then run `copilot`
and sign in. The default, `"none"`, leaves AI setup alone. Existing custom
skills and personal/work skills stay yours. `ai.skills` selects bundled
workflow skills (default `["dev-cli"]`); use `[]` to install none.

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
