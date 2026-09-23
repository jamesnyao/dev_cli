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

Edit the user's override, normally `~/dev_config.json`; never change the
repository's bundled defaults to store personal settings. `DEV_CONFIG_OVERRIDE`
can select another override. Use `dev config get <dotted.key>` to inspect merged
settings without printing credentials or the entire configuration.

- `pythonVersion` selects an exact managed Python version.
  Run `dev python update` after changing it; `dev python path` prints its directory.
- `workspaceRoots` maps machine names to workspace paths. `DEVCONFIG` selects
  the machine; `dev repo root` reports the resulting workspace.
- `ai.provider` defaults to `"none"`. Set it to `"ghcopilot"` and run `dev init`
  only when the user wants GitHub Copilot CLI installed. `ai.skills` selects
  bundled skills, defaults to `["dev-cli"]`, and accepts `[]` to skip skills.
  Only `dev-cli` is currently bundled; personal skill libraries stay untouched.
  Authentication remains user-controlled: run `copilot`, then `/login` if needed.
- Custom settings belong in `~/dev_env/env_windows.ps1` on Windows,
  `~/dev_env/env_mac.sh` on macOS, or `~/dev_env/env_linux.sh` on Linux, including
  WSL. The platform-specific hook runs last and can override Python and PATH.
  `dev init` preserves legacy `env.ps1`/`env.sh` through compatibility adapters;
  startup also falls back to the legacy hook before initialization.

Shared CLI guidance belongs in this bundled skill, not duplicated private
overrides. Keep private configuration, custom hooks, and personal/company skill
libraries separate; never copy their contents into the reusable dev_cli repository.
Do not read or disclose credential files under `.dev_temp/`, or replace an
existing customized `dev-cli` skill.

## Repository management and updates

Use `dev repo list`, `dev repo status`, and `dev repo root` to inspect the setup.
Use `dev repo add <local-repository>` to track an existing Git repository;
inspect `dev repo add -h` for remote and platform options.

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

## PR workflows and validation

The `dev pr` commands target Azure DevOps. Inspect `dev pr -h` and the relevant
subcommand help before using `create`, `desc`, `diff`, or `comments`. Confirm the
repository and branch. Read diffs before describing changes; do not publish,
push, post comments, or create a PR without the user's authorization.

After modifying dev_cli, run `dev test` from that checkout. It runs the existing
unit tests and pylint. Validate Windows and Linux (including WSL) independently
when changing shared launchers or shell integration. Never use real repository
synchronization, AI installation, or authentication as an automated test fixture.
