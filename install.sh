#!/bin/bash
set -euo pipefail

repository="${DEV_CLI_REPOSITORY:-https://github.com/jamesnyao/dev_cli.git}"
destination="$HOME/dev_cli"

as_root() {
    if [[ "$(id -u)" == 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

if ! git --version >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        brew install git
    elif command -v apt-get >/dev/null 2>&1; then
        as_root apt-get update
        as_root apt-get install -y git
    elif command -v dnf >/dev/null 2>&1; then
        as_root dnf install -y git
    elif [[ "$(uname -s)" == Darwin ]] && command -v xcode-select >/dev/null 2>&1; then
        echo "Complete Apple's Command Line Tools installer; dev_cli will continue afterward."
        xcode-select --install
        for ((attempt = 0; attempt < 360; attempt++)); do
            if xcode-select -p >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
                break
            fi
            sleep 5
        done
        if ! git --version >/dev/null 2>&1; then
            echo "Command Line Tools did not become available within 30 minutes." >&2
            exit 1
        fi
    else
        echo "Install Git with your OS package manager, then rerun this installer." >&2
        exit 1
    fi
fi

if [[ ! -e "$destination/.git" ]]; then
    entry=""
    if [[ -e "$HOME/.git" ]]; then
        entry="$(git -C "$HOME" ls-files --stage -- dev_cli)"
    fi
    if [[ "$entry" == 160000\ * ]]; then
        git -C "$HOME" submodule update --init --recursive -- dev_cli
    elif [[ -d "$destination" && -n "$(ls -A "$destination")" ]] || [[ -f "$destination" ]]; then
        echo "Refusing to overwrite $destination; move it aside or initialize its Git checkout." >&2
        exit 1
    else
        git clone -- "$repository" "$destination"
    fi
fi

if [[ ! -f "$destination/dev" || ! -f "$destination/dev.py" ]]; then
    echo "$destination is not a dev_cli checkout; no files were replaced." >&2
    exit 1
fi

if [[ ! -t 0 && -t 1 && -r /dev/tty ]]; then
    exec bash "$destination/dev" init </dev/tty
fi
exec bash "$destination/dev" init
