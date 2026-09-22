# dev_cli

A Python CLI for synchronizing repositories, setting up shell profiles, and
working with Azure DevOps pull requests.

Use it standalone, or as a submodule under a private dotfiles repo: this repo
owns generic behavior and sample defaults; the parent owns machine identity,
real repository lists, and custom shell hooks.

## Quick start

```bash
git clone "<clone-url>" "$HOME/dev_cli"
"$HOME/dev_cli/dev" init
```

Windows (PowerShell):

```powershell
git clone "<clone-url>" "$HOME\dev_cli"
& "$HOME\dev_cli\dev.ps1" init
```

`dev init` prints its own next steps. It's idempotent and self-healing — safe
to rerun any time, and it restores anything deleted (profile redirect,
override config, hooks stub) without touching existing customizations. Or
invoke `python3 ~/dev_cli/dev.py` directly without shell profiles, setting
`DEVCONFIG=example-machine`.

If Python isn't installed, the launchers print a message; run
`dev python update` (bash) or `.\dev.ps1 python update` (PowerShell) to
bootstrap it via your package manager (apt/dnf/brew or winget), then retry.

## Configuration

`dev init` also creates `~/dev_config.json` from the sample (prompting for a
username if interactive and unset) and warns if a `python3` earlier on `PATH`
would shadow the shim (see `DEV_PYTHON_SKIP` below).

`dev_config.json` in this repo is a read-only sample; real settings go in
`../dev_config.json` (or the file named by `DEV_CONFIG_OVERRIDE`). Dictionary
keys merge shallowly, except `repos`, which replaces the sample list
completely — CLI config writes target only the override file, which never
carries comments since `dev` rewrites it as standard JSON.

The sample is annotated with every supported setting and its default. Add
repos with `dev repo add <path>` (`-h` for options); read any setting with
`dev config get <dotted.key>`.

Only Git repositories are managed — loose files and directories belong in
your dotfiles repo directly. Local credentials/caches live in `.dev_temp/`
next to the override; keep it gitignored.

## Private overlays

`dev init` creates `~/dev_env/env.sh` (or `env.ps1` on Windows) as a stub
if one doesn't exist — edit it for custom PATH/env/prompt setup; it's sourced
last and never overwritten.

```text
~/
├── dev_config.json         # Your configuration, outside the tool repo
├── dev_env/
│   ├── env.sh               # Optional zsh customization
│   └── env.ps1              # Optional PowerShell customization
└── dev_cli/                 # This repository
    ├── dev_config.json       # Shared annotated sample, never rewritten
    ├── shell/
    └── setup/
```

Private hooks run last and can override generic shell settings. Shell setup
exposes `DEV_PROMPT_USER` for custom prompts. Python launcher shims can
exclude a toolchain path substring via `DEV_PYTHON_SKIP`.

## Synchronization and submodules

As a submodule, `dev repo sync` also updates the parent repo: it uses the
parent branch's upstream, initializes/updates submodules, and commits and
pushes parent changes. Dirty or unpublished submodules stop the sync rather
than publishing a pointer other machines can't fetch.

Commit and push tool changes before syncing the parent.

First migration or fresh parent clone:

```bash
git -C "$HOME" submodule update --init --recursive
```

Authenticate to both repos while this repo is private.

## Tests

```bash
python3 test_dev.py           # full suite
python3 test_dev.py TestConfig
python3 test_dev.py TestConfig.test_save_never_writes_sample_file
```

Requires Python 3 and Git only; zsh startup tests need `zsh` and are skipped
otherwise. Bash launcher tests run on Unix; batch and PowerShell launcher
tests run on Windows. Run the suite natively on each platform after launcher
changes. `dev test` runs the suite plus pylint (pylint required for that path only).

## License

[MIT](LICENSE).
