---
name: dev-cli
description: Use dev_cli for shell setup, Python and AI configuration, Git repository management, or PR workflows.
license: MIT
---

# dev_cli

Use the installed `dev` command for shell setup, repository management, and
Azure DevOps PR workflows. Start with `dev -h` or `dev <command> -h` to confirm
the available commands and flags. Do not assume the user's workspace, identity,
hosting provider, or private configuration.

## Configuration and onboarding

`dev init` provisions the exact configured Python version, registers shell
profiles, creates missing custom environment hooks, and applies optional AI
setup. Rerunning it preserves existing configuration and custom hooks.

For Bash, `dev init` provisions ble.sh on Linux/macOS and on Windows when
`defaultShell` is `"bash"`. Bash 4+ terminal sessions get gray, history-only
autosuggestions: Right Arrow/End accepts all, Alt+F accepts a word. An installed
fzf also gets completion and Ctrl+R history bindings. Scripts, redirected and
dumb terminals, older Bash, and missing ble.sh keep their existing editor.
ble.sh is never downloaded during shell startup. User `~/.blerc` or XDG blesh settings
override the defaults. Existing zsh setups and non-Windows defaults are unchanged.

Edit the user's override, normally `~/dev_config.json`; never change the
repository's bundled defaults to store personal settings. `DEV_CONFIG_OVERRIDE`
can select another override. Use `dev config get <dotted.key>` to inspect merged
settings without printing credentials or the entire configuration.

- `pythonVersion` selects an exact managed Python version.
  Run `dev python update` after changing it; `dev python path` prints its directory.
- `workspaceRoots` maps machine names to workspace paths. `DEVCONFIG` selects
  the machine; `dev repo root` reports the resulting workspace.
- `environments` defines named shell setups: `path` (directories prepended to
  the base PATH), `env` (variables to set; `null` unsets), optional `cwd`, and
  `description`. `$DEV` expands to the workspace root. `dev set` lists them and
  `dev set <name>` applies one to the current shell through the profile's `dev`
  function; a bare subprocess cannot change the caller's shell.
- `dev init` prompts for `ai.provider`, suggesting `"ghcopilot"` by default
  or the saved choice on later runs. Choose `"none"` to skip AI setup.
  `"claude"` has a separate, not-yet-implemented handler that reports an error
  without installing Copilot. Noninteractive runs use the configured default.
  `ai.skills` (default `true`) links `~/.agents/skills` to the bundled `skills/`
  directory so every bundled skill loads and tracks the checkout; `false` skips it.
  Personal skills in `~/.copilot/skills` stay untouched and win on name clashes.
  Authentication remains user-controlled: run `copilot`, then `/login` if needed.
- Custom settings belong in `~/dev_env/env_windows.ps1` on Windows,
  `~/dev_env/env_mac.sh` on macOS, or `~/dev_env/env_linux.sh` on Linux, including
  WSL. The platform-specific hook runs last and can override Python and PATH.

Shared CLI guidance belongs in this bundled skill, not duplicated private
overrides. Keep private configuration, custom hooks, and personal/company skill
libraries separate; never copy their contents into the reusable dev_cli repository.
Do not read or disclose credential files under `.dev_temp/`, or replace an
existing `~/.agents/skills` directory or a personal skill.

Global flags: `--version` (`-V`) and `--no-color`. Colors also honor
`NO_COLOR`/`FORCE_COLOR` and turn off when output is not a terminal.

## Repository management and updates

```bash
dev repo list             # tracked repositories
dev repo status           # which exist on this machine
dev repo root             # workspace root for this machine
dev repo add <path>       # track an existing Git repository
dev repo remove <name>    # stop tracking
dev repo sync [-f]        # self-update, then clone/update every repository
dev repo old [--delete]   # list or prune stale branches
```

Repository entries in `repos[]` use `path` (clone location under the workspace
root), `remoteUrl`, `skipOn` (machine names; filled in when a clone is declined),
`syncCommand` (run from the parent directory after a branch switch),
`pathLinksTo` (the workspace path is a link to this location), `defaultBranch`
(sync this branch instead of `origin/HEAD` when it exists on origin), and
`bootstrap`. Only Git repositories can be tracked. Dictionary keys in the
override merge shallowly with the sample; arrays replace it wholesale.

**`dev repo sync` can commit and push.** It is not a read-only status check.
Get explicit authorization before running it; do not run it merely to validate
configuration changes. Use local fixture tests for validation instead.

At most one repository may set `bootstrap: true`. Sync updates that repository
and its submodules first, restarts using the updated tool, then processes the
other repositories. This is the bootstrap/self-update path, not a separate
`dev selfupdate` command. Omit `bootstrap` to disable it.

For a standalone installation, the bootstrap entry can identify the tool clone.
For a submodule installation, it can identify the parent repository. Commit and
publish submodule changes separately before an authorized parent sync. Preserve
dirty work and stop on unresolved divergence; never reset or discard user edits.

Repository updates are non-destructive:

- A clean default branch is reset to `origin/<default>`. On a feature branch only
  the local default ref is fast-forwarded; a diverged default is left with a warning.
- Sync offers to switch stale feature branches back to the default (`[Y/n]`) and
  to clone missing repositories (`[y/N]`). `-f` answers yes to both, so it clones
  every missing repository; entries in `skipOn` stay skipped.
- Before a branch switch, sync clears `skip-worktree`/`assume-unchanged` bits and
  stashes uncommitted work as `dev-sync <branch> <timestamp>`.
- Uncommitted changes on a default branch whose files are older than 14 days are
  stashed the same way and the branch is reset. Line-ending normalization
  artifacts are ignored and never counted as edits.
- Recover any stash with `git stash list` and `git stash pop`. Failed updates or
  clones make the exit status nonzero; intentional skips do not.

## PR workflows and validation

The `dev pr` commands target Azure DevOps. Inspect `dev pr -h` and the relevant
subcommand help before using `create`, `desc`, `diff`, or `comments`. Confirm the
repository and branch. Read diffs before describing changes; do not publish,
push, post comments, or create a PR without the user's authorization.

Each accepts `--id <PR_ID>` or infers the PR from the current repository and
branch. `dev pr create` opens a draft unless `--draft false` is passed, and
`dev pr comments --reply <threadId> --resolve "<msg>"` replies and resolves in
one call.

`dev ado git <args>` runs Git with a short-lived Azure CLI bearer token, and
`dev ado token` prints a cached one; neither needs a PAT. `dev ado set-pat`,
`show-pat`, and `clear-pat` manage a PAT for REST calls. Tokens and PATs live in
the ignored `~/.dev_temp/`; never print or commit them.

After modifying dev_cli, run `dev test` from that checkout. It runs the existing
unit tests and pylint. Validate Windows and Linux (including WSL) independently
when changing shared launchers or shell integration. Never use real repository
synchronization, AI installation, or authentication as an automated test fixture.
