# Sourced by ~/.bashrc. The platform-specific custom hook runs last.
case $- in
    *i*) ;;
    *) return ;;
esac

export platform="$(uname | tr '[:upper:]' '[:lower:]')"
export PATH="$HOME/dev_cli:$HOME/.local/bin:$PATH"
case "$platform" in
    mingw*|msys*|cygwin*)
        dev() {
            "$HOME/dev_cli/dev.cmd" "$@"
        }
        ;;
esac
dev_python="$(dev python path)" || return
case "$platform" in
    mingw*|msys*|cygwin*) dev_python="$(cygpath -u "$dev_python")" ;;
esac
export PATH="$dev_python:$PATH"
unset dev_python
export DEVCONFIG="${DEVCONFIG:-example-machine}"
DEV="$(dev repo root)" || return
export DEV
if [[ -z "$DEV_PROMPT_USER" ]]; then
    DEV_PROMPT_USER="$(dev config get identity.username)" || return
    export DEV_PROMPT_USER="${DEV_PROMPT_USER:-${USERNAME:-$USER}}"
fi

if command -v zoxide >/dev/null 2>&1; then
    eval "$(zoxide init --cmd cd bash)"
fi

if [[ "$platform" == mingw* || "$platform" == msys* || "$platform" == cygwin* ]]; then
    dev_hook="$HOME/dev_env/env_windows.sh"
elif [[ "$platform" == "darwin" ]]; then
    dev_hook="$HOME/dev_env/env_mac.sh"
else
    dev_hook="$HOME/dev_env/env_linux.sh"
fi
if [[ -f "$dev_hook" ]]; then
    source "$dev_hook"
fi
