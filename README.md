# dev_cli

A Python development workflow CLI for synchronizing repositories, setting up
shell profiles, and working with Azure DevOps pull requests.

Use it on its own, or as the shared tooling layer under a private dotfiles
repository. The tool owns generic behavior and sample defaults; the parent
owns machine identity, real repository lists, and custom shell hooks.

## Quick start

Install Python 3 and Git. Clone this repository to `~/dev_cli`, copy the
sample configuration one directory up, and edit that private copy. Replace
`<clone-url>` with this repository's clone URL.

### macOS and Linux

```bash
git clone "<clone-url>" "$HOME/dev_cli"
cp -n "$HOME/dev_cli/dev_config.json" "$HOME/dev_config.json"
```

Edit `~/dev_config.json`, then initialize the shell redirects:

```bash
python3 "$HOME/dev_cli/dev.py" init
```

### Windows

In PowerShell:

```powershell
git clone "<clone-url>" "$HOME\dev_cli"
if (-not (Test-Path "$HOME\dev_config.json")) {
  Copy-Item "$HOME\dev_cli\dev_config.json" "$HOME\dev_config.json"
}
notepad "$HOME\dev_config.json"
py "$HOME\dev_cli\dev.py" init
```

### First run

Open a new terminal, then:

```shell
dev repo root
dev repo list
```

The sample is usable without editing: the workspace is `$HOME`, the sole
repository is this `dev_cli` checkout at `~/dev_cli`, and your username
comes from the environment. Its existing Git origin supplies the remote;
no account-specific URL or placeholder email address is baked into the sample.

Keep an existing `~/dev_config.json` rather than overwriting it. `dev init`
preserves existing profile content and adds redirects. The shell profiles can
install additional tools, so review `shell/` and `setup/` before enabling them.

The CLI and unit tests use Python's standard library. You can also invoke
`python3 ~/dev_cli/dev.py` directly without installing the shell profiles;
set `DEVCONFIG=example-machine` when running workspace commands this way.

## Configuration

`dev_config.json` inside this repository is a read-only sample. Real settings
belong in `../dev_config.json`, or the file named by `DEV_CONFIG_OVERRIDE`.
Override dictionary keys merge shallowly; the `repos` array replaces the sample
list completely. CLI configuration writes target only the override.

Start with these settings and add your own repositories as needed:

```jsonc
{
  "workspaceRoots": {
    "example-machine": "$HOME"
  },
  "repos": [
    {
      "path": "dev_cli",
      "pathLinksTo": "$HOME/dev_cli",
      "bootstrap": true
    }
  ],
  "identity": {
    "username": ""
  }
}
```

`$HOME` expands to the current user's home on every platform, including Windows.
To choose another workspace, change its value to an existing directory.
Keep the `example-machine` key for the simplest setup. For multiple machines,
add more `workspaceRoots` entries and set `DEVCONFIG` to the appropriate key.
An explicitly exported `DEVCONFIG` is preserved.

| Setting | Default when unset or blank |
| --- | --- |
| `identity.username` | `USERNAME`, then `USER`, then the system login for CLI commands |
| `identity.branchPrefix` | `user/<resolved-username>/` |
| `identity.creatorEmail` | Git's global `user.email` |

Set `identity.username` to override the environment-derived username. A custom
`identity.branchPrefix` takes precedence over the generated branch prefix.
Git author name and email remain your Git settings; the CLI does not invent an
email address or modify those settings during onboarding.

Use `dev repo add <local-repo-path>` to add existing Git checkouts to `repos`.
It records the repository path and origin; run `dev repo add -h` for the
repository-entry options. `bootstrap: true` is supported on at most one entry
to update that checkout before other repositories. No bootstrap entry means no
automatic tool update. The sample uses the tool checkout as its bootstrap.

The loader accepts strict JSON and JSONC comments. `dev` writes only the private
override and rewrites it as standard JSON, so keep explanatory comments in the
sample or another file not managed by the CLI.

Only Git repositories are managed. Loose files and ordinary directories belong
in your dotfiles repository; they are not copied or mirrored separately.

Use `dev config get <dotted.key>` to read a setting. Local credentials and
caches live in `.dev_temp/` alongside the override, never in the sample.
Keep `.dev_temp/` ignored if you version your private configuration.

## Private overlays

An optional parent repository can keep real settings and custom tools private
while reusing this repository as a submodule:

```text
~/
├── dev_config.json             # Your configuration, outside the tool repo
├── work_scripts/
│   ├── hooks.sh                # Optional zsh customization
│   └── hooks.ps1               # Optional PowerShell customization
└── dev_cli/                # This repository
    ├── dev_config.json         # Shared annotated sample, never rewritten
    ├── shell/
    └── setup/
```

Private hooks run last and can override generic shell settings. Shell setup
exposes `DEV_PROMPT_USER` for custom prompts; an explicit value or private hook
can override the configured username. The default zsh theme may still display
the system login. Python launcher shims can exclude a toolchain path substring
through `DEV_PYTHON_SKIP`.

## Synchronization and submodules

The tool can run standalone or as a `dev_cli` submodule of a private
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

## Run the tests

From the root of this repository, run the full local suite:

```bash
python3 test_dev.py
```

On Windows:

```powershell
py test_dev.py
```

The suite uses Python's standard-library `unittest` runner. Git must be on
`PATH`: repository and submodule tests create temporary local repositories,
including bare remotes, without needing GitHub credentials.

To run one class or one test while developing:

```bash
python3 test_dev.py TestConfig
python3 test_dev.py TestConfig.test_save_never_writes_sample_file
```

Run the full suite before submitting changes. Tests cover sample/override
configuration, repository sync, detached submodules, first initialization,
unpublished work protection, and shell startup. Skips are reported for tests
that do not apply to the current platform or require an unavailable shell.

Only Python 3 and Git are required for the core suite. The zsh startup tests
also need `zsh` and are skipped when it is unavailable. Optional shell tools
such as `fzf` and `zoxide` are not test prerequisites. A passing macOS run does
not validate native Windows behavior.

`dev test` runs the unit suite and then pylint. It requires pylint in the
Python environment used by `dev`; it does not silently skip linting when pylint
is missing. The direct `test_dev.py` commands above do not require pylint.

## License

[MIT](LICENSE).
