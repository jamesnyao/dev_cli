---
name: workspace-layout
description: >-
  Workspace and home directory layout for a dev_cli setup. Use when navigating
  the workspace, resolving `<workspace>`, finding configuration or skills, or
  working with the home repository and its dev_cli submodule.
license: MIT
---

# Workspace and home layout

## Workspace root

Skills refer to the workspace root as `<workspace>`. It differs per machine:
`workspaceRoots` in `~/dev_config.json` maps machine names to paths, and
`DEVCONFIG` selects this machine's entry. Resolve it with `dev repo root` (or
`$DEV` in a configured shell) before running commands; never assume a drive or
username. Tracked repositories are cloned under it by their `path`.

## Home repository

A common setup keeps the home directory itself as a private Git repository
that holds dotfiles, private skills, instructions, and `~/dev_config.json`, with
dev_cli mounted as a submodule at `~/dev_cli`:

```text
~/                          # private home repository
├── dev_config.json         # private configuration override
├── .dev_temp/              # ignored credentials and caches (never print or commit)
├── dev_env/                # private shell hooks
├── .copilot/skills/        # private skills
├── .agents/skills          # link to ~/dev_cli/skills (created by dev init, ignored)
└── dev_cli/                # dev_cli submodule
    ├── dev.py, ai.py, ...  # the tool and its tests
    ├── dev_config.json     # read-only annotated sample
    ├── shell/              # generic shell profiles
    └── skills/             # public skills
```

Files are tracked directly in the home repository; there is no mirror copy.
The two repositories have independent branches, diffs, and commits, so check
`git status` in both before editing. Commit and publish dev_cli changes before
the parent's submodule pointer.

The `repos[]` entry for the home repository uses `pathLinksTo: "~"` and
`bootstrap: true`: the checkout lives in `$HOME`, `<workspace>/home` is only a
link to it, and sync updates it (and its submodules) first. On a fresh machine,
initialize the home repository in `$HOME` and run
`git -C ~ submodule update --init --recursive` before `dev`; never clone it into
a separate `<workspace>/home` or overwrite existing home files to make sync pass.
Do not add dev_cli as a separate entry in `repos[]`.

## Skills

| Location | Contents |
| --- | --- |
| `~/.copilot/skills/` | Private skills; take precedence on name clashes |
| `~/.agents/skills/` | Link to `~/dev_cli/skills/`, the public bundled skills |

Edit public skills in `~/dev_cli/skills/` and private ones in
`~/.copilot/skills/`. Keep the names distinct; put private additions to a
public skill in a separately named skill.

## Key paths

| What | Path |
| --- | --- |
| Workspace root | `dev repo root` |
| Private configuration | `~/dev_config.json` |
| Sample configuration | `~/dev_cli/dev_config.json` |
| Private shell hooks | `~/dev_env/` |
| Credentials and caches | `~/.dev_temp/` |
| Scratch files | `<workspace>/temp/` (see `temp-files`) |
