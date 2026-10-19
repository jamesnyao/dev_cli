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
mode), in **interactive** mode (`--mode interactive`), including when continuing
a session with `ai -c`. Pass `--mode autopilot`, `--mode plan`, `--plan`, or
`--autopilot` yourself to choose the mode instead. The workspace root is also
passed through `--add-dir`, granting access to everything beneath it and loading
its `.github` skills and agents as trusted configuration. A leading `-c` (after
any model tier) is shorthand for `--continue`; all other arguments and input
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
default applies. `ai.continueModel` optionally names a different tier for a
leading `-c` or `--continue`; it falls back to `ai.defaultModel`. An explicit
tier or `--model` overrides both; combining a tier name with `--model` is an
error.

```jsonc
"ai": {
  "models": {"fast": "<model-id>", "high": "<model-id>"},
  "defaultModel": "fast",
  "continueModel": "high"
}
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

## Pull requests

`dev pr` targets Azure DevOps. Commands accept `--id <PR_ID>`, or infer the
PR from the current repository and branch (`--repo` and `--branch` override these).

```text
dev pr comments --id 12345 --hash
dev pr comments --id 12345 --history --last 10
dev pr policy --id 12345
dev pr tag --id 12345 --json
dev pr tag --id 12345 --add working
dev pr tag --id 12345 --expect working --add reviewed --remove working
```

`comments --hash` prints only `threadId/commentId sha256` rows for live comments
in unresolved threads, sorted by those IDs. Each hash covers canonical JSON for
the full comment plus parent thread metadata (excluding its comments array).
JSON key order does not affect it; edits, status changes, and moved anchors do.
Resolved/deleted comments and system-activity threads are omitted. Every call
fetches fresh state and rejects posting options. Empty output means no matching
comments; read failures return nonzero.

Compare the whole ID-to-hash map to detect additions, changes, and removals.
Use `comments --active` to read changed content and scan hashes again before
accepting the reviewed snapshot. Do not permanently ignore resolved IDs: a
reopened thread reappears. Never replace a saved map after an unsuccessful scan.

`comments --history` shows the latest 10 successful comment reads, newest first;
`--last <N>` changes the count. Each entry contains UTC time, the ADO-reported PR
source commit, unresolved-comment hashes, and the automatic
`COPILOT_AGENT_SESSION_ID` when available (otherwise empty). No comment bodies
are stored, and there are no session or WIP flags.
All successful read modes record snapshots; posting, failures, and history
lookups do not. Each PR's local history file under the configuration directory's
`.dev_temp/pr-comment-history` retains at most 1,000 entries. Appends are locked
and atomically published. History uses normal PR resolution, but does not fetch
comments again. Historical observations are not proof that feedback was handled.

`policy` prints one Azure Pipelines run URL per line for queued, running, rejected,
or broken PR policy evaluations with an associated build ID. Links are deduplicated.
Successful, non-applicable, disabled, deleted, and unrun checks are omitted.
No matching runs means no output; read failures report an error and return nonzero.
An empty listing is not proof that the PR can merge or its draft checks ran.
There are no extra output modes, build-detail requests, or writes.

Set `pr.policyExclude` in your private `dev_config.json` to omit checks by name.
Patterns match case-insensitively against names in the evaluation: its display
name, status-check name (with or without its genre), or policy type name.
Exact names match only that name; `*` and `?` explicitly enable wildcards.
The default is an empty list.

Draft PR build validations may require manual queueing. After confirming the
pipeline, PR revision, and parameters, use the existing
`az repos pr policy queue --id <PR_ID> --evaluation-id <ID> --org <ORG_URL>`.
Inspect the resulting evaluation and build to verify the correct PR merge
revision was validated; an unrelated branch build is not policy evidence.
Listing policies never queues, retries, activates a PR, or changes policy settings.

`tag` lists names, adds one named tag, removes one named tag, or does both.
Unrelated tags are preserved, repeated adds/removals are idempotent, and changes
are read back for verification. `--json` returns an array of names.
`--expect <NAME>` requires that tag before writing and stops on unexpected tag
changes before removal or during final verification.
It is a cooperative precondition, not an atomic lock: consumers must re-read
tags before each subsequent action. Failed reads and unmet preconditions return
a nonzero exit code; they must never be interpreted as permission to continue.

## Develop

Install pylint with `python -m pip install pylint`, then run `dev test`.
This discovers all `test_*.py` suites and lints the production Python modules,
including Windows Terminal integration. The tests cover onboarding, launchers,
and sync using local Git repositories. Run them natively on Windows and Unix
after changing shell behavior.

Tests that need repository-local temporary files use ignored `.dev_temp/`
subdirectories and clean up on completion, so an interrupted run does not add
untracked fixture directories to the repository root.

[MIT license](LICENSE).
