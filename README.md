# dev_cli

A Python CLI for synchronizing repositories, setting up shell profiles, and
working with Azure DevOps pull requests.

Use it standalone, or as a submodule under a private dotfiles repo: this repo
owns generic behavior and sample defaults; the parent owns machine identity,
real repository lists, and custom shell hooks.

## Quick start

```bash
git clone "<clone-url>" "$HOME/dev_cli"
python3 "$HOME/dev_cli/dev.py" init
# edit ~/dev_config.json
```

Windows (PowerShell):

```powershell
git clone "<clone-url>" "$HOME\dev_cli"
py "$HOME\dev_cli\dev.py" init
notepad "$HOME\dev_config.json"
```

Open a new terminal and run `dev repo root` / `dev repo list`. The sample is
usable as-is: workspace root is `$HOME`, the only repo is this `dev_cli`
checkout, and identity is derived from the environment.

`dev init` sets up shell profile redirects, creates `~/dev_config.json` from
the sample if it doesn't exist yet (prompting for a username if the terminal
is interactive and none is configured), and warns if a python earlier on
`PATH` would shadow the `python3` shim (see `DEV_PYTHON_SKIP` below). It's
idempotent and preserves existing profile content and an existing override
config. You can also invoke `python3 ~/dev_cli/dev.py` directly without shell
profiles; set `DEVCONFIG=example-machine` when doing so.

## Configuration

`dev_config.json` in this repo is a read-only sample. Real settings go in
`../dev_config.json` (or the file named by `DEV_CONFIG_OVERRIDE`). Override
keys merge shallowly, except `repos`, which replaces the sample list
completely. CLI config writes target only the override file.

`$HOME` expands on every platform, including Windows. Add more
`workspaceRoots` entries for multiple machines and set `DEVCONFIG` to the
matching key.

| Setting | Default when unset or blank |
| --- | --- |
| `identity.username` | `USERNAME`, then `USER`, then the system login |
| `identity.branchPrefix` | `user/<resolved-username>/` |
| `identity.creatorEmail` | Git's global `user.email` |

The CLI never invents or modifies your Git author name/email.

Use `dev repo add <local-repo-path>` to add Git checkouts to `repos`; run
`dev repo add -h` for the available options. At most one entry may set
`bootstrap: true`, which updates that checkout before syncing the rest.

The loader accepts JSON with `//` and `/* */` comments. `dev` only writes and
rewrites the private override (as standard JSON), so keep comments in the
sample or elsewhere.

Only Git repositories are managed — loose files and directories belong in
your dotfiles repo directly.

Use `dev config get <dotted.key>` to read a setting. Local credentials/caches
live in `.dev_temp/` next to the override; keep it gitignored.

## Private overlays

```text
~/
├── dev_config.json         # Your configuration, outside the tool repo
├── work_scripts/
│   ├── hooks.sh             # Optional zsh customization
│   └── hooks.ps1            # Optional PowerShell customization
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
otherwise. `dev test` runs the suite plus pylint (pylint required for that
path only).

## License

[MIT](LICENSE).
