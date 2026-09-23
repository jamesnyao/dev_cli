#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$HOME/.dev_temp/dev_cli"
UV="$ROOT/uv/uv"

if [[ "${1:-}" != --install-uv-only && "${1:-}" != --refresh-uv ]]; then
    # Only the repository-controlled default is read here. Python merges JSONC overrides.
    version="$(sed -nE 's/^[[:space:]]*"pythonVersion"[[:space:]]*:[[:space:]]*"(3\.[0-9]+\.[0-9]+)"[[:space:]]*,?[[:space:]]*(\/\/.*)?$/\1/p' "$SCRIPT_DIR/dev_config.json")"
    if [[ ! "$version" =~ ^3\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
        echo "dev_config.json must contain one exact pythonVersion default on its own line" >&2
        exit 1
    fi
    cached="$ROOT/venvs/$version/bin/python"
    if [[ -x "$cached" ]]; then
        exec "$cached" -I "$SCRIPT_DIR/runtime.py" "$@"
    fi
fi

# Only uv's child environment is isolated; the user's shell is never modified.
uv_command() (
    for name in $(compgen -e); do
        case "$name" in
            UV_*|PYTHON*|INSTALLER_*|VIRTUAL_ENV|CONDA_PREFIX|__PYVENV_LAUNCHER__) unset "$name" ;;
        esac
    done
    export UV_UNMANAGED_INSTALL="$ROOT/uv" UV_NO_MODIFY_PATH=1 UV_NO_CONFIG=1
    export UV_MANAGED_PYTHON=1 UV_PYTHON_INSTALL_DIR="$ROOT/python"
    export UV_PYTHON_BIN_DIR="$ROOT/python-bin" UV_PYTHON_INSTALL_BIN=0 UV_PYTHON_INSTALL_REGISTRY=0
    export UV_CACHE_DIR="$ROOT/cache" UV_TOOL_DIR="$ROOT/tools" UV_TOOL_BIN_DIR="$ROOT/tool-bin"
    export UV_LINK_MODE=copy TMPDIR="$ROOT/tmp" TMP="$ROOT/tmp" TEMP="$ROOT/tmp"
    "$@"
)

install_uv() (
    installer="$ROOT/tmp/install-uv-$$.sh"
    trap 'rm -f "$installer"' EXIT
    curl --fail --silent --show-error --location https://astral.sh/uv/install.sh --output "$installer"
    uv_command /bin/sh "$installer" >&2
    if [[ ! -x "$UV" ]]; then
        echo "uv installer did not create $UV" >&2
        exit 1
    fi
    rm -f "$installer"
    trap - EXIT
)

mkdir -p "$ROOT/tmp"
if [[ ! -x "$UV" || "${1:-}" == --refresh-uv ]]; then
    install_uv
fi
if [[ "${1:-}" == --install-uv-only || "${1:-}" == --refresh-uv ]]; then
    exit 0
fi

if ! uv_command "$UV" --no-config --no-progress python install --no-bin --no-registry "$version" >&2; then
    echo "Python $version installation failed; refreshing private uv and retrying once." >&2
    install_uv
    uv_command "$UV" --no-config --no-progress python install --no-bin --no-registry "$version" >&2
fi
interpreter="$(uv_command "$UV" --no-config --no-progress python find --system --managed-python --no-python-downloads "$version")"
if [[ ! -x "$interpreter" || "$interpreter" != "$ROOT/python/"* ]]; then
    echo "uv did not return a private managed Python: $interpreter" >&2
    exit 1
fi
exec "$interpreter" -I "$SCRIPT_DIR/runtime.py" "$@"
