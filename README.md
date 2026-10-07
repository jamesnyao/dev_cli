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

Bash uses the same single-line prompt style as PowerShell: green `user@host`, a
cyan path, an optional yellow Git branch, and `$` (red after a failed command).
Git Bash uses forward slashes and omits the drive prefix: `D:\dev\project`
appears as `dev/project`, with a drive root shown as `/`. UNC paths retain their
server/share prefix; Linux, WSL, and macOS keep their full Unix paths.
Set `DEV_PROMPT_USER` or `DEV_PROMPT_HOST` in your custom hook to override the
display names. Existing Bash prompt commands are preserved.
Bash launched from your home directory starts in the configured workspace,
using the final `DEV` value after your custom hook runs. An explicitly selected
working directory is left unchanged.

`dev init` installs [ble.sh](https://github.com/akinomyoga/ble.sh) for Bash on
Linux/macOS and for Windows when `defaultShell` is `"bash"`. Installation needs
Bash 4+, curl, tar, xz, and awk; older Bash versions keep their existing editor.
Interactive terminals show gray suggestions from Bash history, like
`zsh-autosuggestions`: Right Arrow or End accepts the whole suggestion, and
Alt+F accepts the next word. Enter alone does not accept the untyped remainder.
Normal Tab completion and Readline-style editing remain available. If fzf is
already on PATH, its completion and Ctrl+R history picker are enabled too.

The editor lives in `~/.local/share/blesh`; rerunning `dev init` preserves an
existing installation. Shell startup never downloads ble.sh. Scripts, redirected
input/output, dumb terminals, and machines without ble.sh keep their existing
behavior. Bash uses its own history, not PowerShell or zsh history.
Put overrides in `~/.blerc` (or `~/.config/blesh/init.sh`, respecting
`XDG_CONFIG_HOME`), which loads after dev_cli's defaults. For example,
`bleopt complete_auto_complete=` disables suggestions.
Existing zsh and PowerShell profiles remain available; no default shell is
changed on Linux/macOS.

PowerShell caches the Python path, workspace root, and identity that it reads at
startup in `~/.dev_temp/dev_cli/profile-cache.json`, so warm shells start without
launching Python. The cache refreshes when `DEVCONFIG` or `dev_config.json`
changes; delete the file to force a refresh. Custom hooks can reuse it with
`Invoke-DevCached repo, root`.

Add a `workspaceRoots` entry for each machine and set `DEVCONFIG` to its key.
See the annotated [configuration defaults](dev_config.json) for other settings.
Keep `.dev_temp/` out of Git; it holds local runtimes, caches, and credentials.

### Named environments

Define `environments` in `~/dev_config.json` to switch the current shell
between toolchains or checkouts:

```jsonc
"environments": {
  "project": {
    "description": "Project toolchain",
    "path": ["$DEV/project/tools"],        // prepended to the shell's base PATH
    "env": {"TOOLS_ROOT": "$DEV/project/tools", "OLD_FLAG": null},  // null unsets
    "cwd": "$DEV/project/src"              // optional
  }
}
```

```text
dev set             # list environments; * marks the active one
dev set project     # apply it to this shell
dev set project --quiet
```

`$DEV` is the machine's workspace root, and `$HOME`, `~`, and other environment
variables expand too. Each switch rebuilds PATH from the PATH captured on the
first switch (`DEV_BASE_PATH`) and unsets the previous environment's variables,
so switching never accumulates entries. `DEV_ENV` names the active environment.
The `dev` function in the dev_cli PowerShell, Bash, and zsh profiles applies the
change; PowerShell caches it with the other startup answers.

`dev init` prompts for an AI provider, suggesting `ghcopilot` by default.
Press Enter to install GitHub Copilot CLI and link the bundled skills, or choose
`none` to skip AI setup. Choose `claude` to select its separate setup path
(not implemented yet; it reports an error without installing Copilot).
Later runs suggest your saved choice; noninteractive runs use the configured default.
Run `copilot` and sign in yourself.

With `ai.skills` (default `true`), `dev init` links `~/.agents/skills` to the
bundled [`skills/`](skills) directory (a junction on Windows, a symlink
elsewhere), so Copilot loads every bundled skill and sees updates after
`git pull` without reinstalling. Set it to `false` to skip the link. An existing
`~/.agents/skills` directory or link is preserved with a warning. Unedited copies
installed by earlier releases in `~/.copilot/skills` are removed; a personal
skill with a bundled skill's name takes precedence and is reported. Keep private
skills in `~/.copilot/skills`.

## AI chat

Run `ai` from Bash, zsh, or PowerShell. The launchers live in `dev_cli`,
which `dev init` adds to PATH, and share the same `dev ai` implementation.
They use `ai.provider` (default `ghcopilot`) and install the CLI and link the
bundled skills if the executable is missing. Sign in with `/login` if needed;
the wrapper does not configure authentication.

Chat always starts in the configured `workspaceRoots[DEVCONFIG]` directory with
**full permissions** by default (`copilot --allow-all`, equivalent to YOLO
mode), in **autopilot** mode (`--mode autopilot`), so Copilot keeps working
until the task is done. Pass `--mode interactive`, `--mode plan`, `--plan`, or
`--autopilot` yourself to choose the mode instead. The workspace root is also
passed through `--add-dir`, granting access to everything beneath it and loading
its `.github` skills and agents as trusted configuration. All arguments and input
are forwarded, and the provider's exit status is preserved.

```text
ai
ai auto
ai --resume
ai -p "Explain this repository"
```

A leading tier name picks the model: `ai auto` passes `--model auto` so Copilot
chooses. Add tiers with `ai.models` (tier name to model name) and set
`ai.defaultModel` to the tier used when none is given; without it, Copilot's own
default applies. An explicit `--model` overrides the default tier; combining it
with a tier name is an error.

```jsonc
"ai": {"models": {"high": "<model-id>"}, "defaultModel": "high"}
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
