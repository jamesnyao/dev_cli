# Dev CLI

A Python development workflow CLI for synchronizing repositories, setting up
shell profiles, and working with Azure DevOps pull requests.

## Getting started

Clone this repository as `~/dev_scripts`, then run:

```bash
export PATH="$HOME/dev_scripts:$PATH"
export DEVCONFIG=example-machine
dev config get workspaceRoots
dev repo list
```

On PowerShell:

```powershell
$env:PATH = "$HOME\dev_scripts;$env:PATH"
$env:DEVCONFIG = "example-machine"
dev config get workspaceRoots
dev repo list
```

Python 3 and Git are required. The CLI and unit tests use the Python standard library.
Replace the example repository with your own configuration before running
`dev repo sync`; the sample URL is illustrative, not a real repository.

`dev init` adds shell redirects without overwriting existing profile content.
The shell scripts bootstrap additional tools and optionally source private
`~/work_scripts/hooks.sh` or `hooks.ps1` last. The Python launcher shims can
exclude a toolchain path substring via `DEV_PYTHON_SKIP`.

## Configuration

`dev_config.json` inside this repository is a read-only sample. Real settings
belong in `../dev_config.json`, or the file named by `DEV_CONFIG_OVERRIDE`.
Override dictionary keys merge shallowly; arrays such as `repos` and `files`
replace the sample arrays. CLI configuration writes target only the override.

Set `DEVCONFIG` to a key in `workspaceRoots`. For example, a private override:

```json
{
  "workspaceRoots": {
    "my-machine": "$HOME/projects"
  },
  "repos": [],
  "files": []
}
```

Shell startup defaults to `example-machine` and reads `DEV` from the merged
configuration rather than hardcoding a workspace directory. An explicitly
exported `DEVCONFIG` is preserved. Private hooks can select a different machine
and apply work-specific environment settings after the generic setup.

Use `dev config get <dotted.key>` to read a setting. Local credentials and
caches live in `.dev_temp/` alongside the override, never in the sample.
Keep `.dev_temp/` ignored if you version your private configuration.

## Synchronization and submodules

The tool can run standalone or as a `dev_scripts` submodule of a private
configuration repository. In submodule mode, `dev repo sync` updates the
parent repository regardless of the workspace root or configured `home` entry.
It uses the current parent branch's upstream, initializes submodules, updates
their configured remote branches, and commits and pushes parent changes.

Commit and push tool changes separately before syncing the parent.
Dirty submodules and unpublished submodule commits stop sync rather than
publishing a parent pointer other machines cannot fetch. Divergent submodule
history may require resolving and pushing a merge before retrying.

For the first migration from a plain directory, or a fresh parent clone:

```bash
git -C "$HOME" submodule update --init --recursive
```

Authenticate to both repositories while the tool repository is private.
Subsequent `dev repo sync` runs initialize and update submodules automatically.

## Development

```bash
python3 test_dev.py
```

`dev test` additionally runs pylint when installed. Tests cover standalone
configuration and real local parent/submodule remotes, including detached
checkouts, first initialization, and protection of unpublished work.
