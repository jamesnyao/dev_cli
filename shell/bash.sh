# Sourced by ~/.bashrc.
case $- in
    *i*) ;;
    *) return ;;
esac
# Login profiles may source ~/.bashrc more than once.
[[ -n "$_dev_cli_bash_loaded" ]] && return
_dev_cli_bash_loaded=1

export platform="$(uname | tr '[:upper:]' '[:lower:]')"
export PATH="$HOME/dev_cli:$HOME/.local/bin:$PATH"
case "$platform" in
    mingw*|msys*|cygwin*)
        _dev_cli() {
            "$HOME/dev_cli/dev.cmd" "$@"
        }
        ;;
    *)
        _dev_cli() {
            command dev "$@"
        }
        ;;
esac
# `dev set <name>` prints shell code that must run in this shell.
dev() {
    if [[ "$1" == set && $# -ge 2 && "$2" != -* ]]; then
        local dev_script
        dev_script="$(_dev_cli "$@" --shell bash)" || return
        eval "$dev_script"
    else
        _dev_cli "$@"
    fi
}
export DEVCONFIG="${DEVCONFIG:-example-machine}"

# Startup answers from `dev` are cached per DEVCONFIG until a config file
# changes, so warm shells start without launching Python. Hooks can use it too.
dev_cached() {
    local key="${DEVCONFIG}_$*"
    local cache="$HOME/.dev_temp/dev_cli/profile-cache/${key//[^A-Za-z0-9._-]/_}"
    if [[ -f "$cache" && ! "$HOME/dev_config.json" -nt "$cache"
          && ! "$HOME/dev_cli/dev_config.json" -nt "$cache" ]]; then
        printf '%s\n' "$(<"$cache")"
        return
    fi
    local value
    value="$(dev "$@")" || return
    mkdir -p "${cache%/*}" && printf '%s\n' "$value" > "$cache"
    printf '%s\n' "$value"
}
_dev_python_dir() {
    local dir
    dir="$(dev_cached python path)" || return
    case "$platform" in
        mingw*|msys*|cygwin*) dir="$(cygpath -u "$dir")" ;;
    esac
    printf '%s\n' "$dir"
}
dev_python="$(_dev_python_dir)" || return
if [[ ! -d "$dev_python" ]]; then
    # A cached path from a removed Python install; refresh every cached answer.
    rm -rf "$HOME/.dev_temp/dev_cli/profile-cache"
    dev_python="$(_dev_python_dir)" || return
fi
export PATH="$dev_python:$PATH"
unset dev_python
unset -f _dev_python_dir
DEV="$(dev_cached repo root)" || return
export DEV
if [[ -z "$DEV_PROMPT_USER" ]]; then
    DEV_PROMPT_USER="$(dev_cached config get identity.username)" || return
    export DEV_PROMPT_USER="${DEV_PROMPT_USER:-${USERNAME:-$USER}}"
fi

if command -v zoxide >/dev/null 2>&1; then
    eval "$(zoxide init --cmd cd bash)"
fi

_dev_prompt()
{
    local last_status=$?
    _dev_prompt_path="$PWD"
    case "$platform" in
        mingw*|msys*|cygwin*)
            _dev_prompt_path="$(cygpath -m "$PWD")" || return
            _dev_prompt_path="${_dev_prompt_path#?:/}"
            _dev_prompt_path="${_dev_prompt_path:-/}"
            ;;
    esac
    _dev_prompt_branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)"
    # Expand data after Bash decodes prompt escapes, keeping paths and branch names literal.
    PS1='\[\e[32m\]${DEV_PROMPT_USER:-\u}@${DEV_PROMPT_HOST:-\h}\[\e[0m\] \[\e[96m\]${_dev_prompt_path}\[\e[0m\]'
    if [[ -n "$_dev_prompt_branch" ]]; then
        PS1+=' \[\e[33m\](${_dev_prompt_branch})\[\e[0m\]'
    fi
    if (( last_status == 0 )); then
        PS1+=' \[\e[0m\]$ '
    else
        PS1+=' \[\e[31m\]$ \[\e[0m\]'
    fi
    return "$last_status"
}

if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
    PROMPT_COMMAND=(_dev_prompt "${PROMPT_COMMAND[@]}")
else
    PROMPT_COMMAND="_dev_prompt${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
fi

if [[ -z ${BLE_VERSION-} && ${BASH_VERSINFO[0]} -ge 4
      && -t 0 && -t 1 && -t 2 && ${TERM:-dumb} != dumb
      && -f "$HOME/.local/share/blesh/ble.sh" ]]; then
    export USER="${USER:-${USERNAME:-$(id -un)}}"
    source -- "$HOME/.local/share/blesh/ble.sh" --attach=none \
        --rcfile "$HOME/dev_cli/shell/blerc.sh" || return
fi

if [[ "$platform" == mingw* || "$platform" == msys* || "$platform" == cygwin* ]]; then
    dev_hook="$HOME/dev_env/env_windows.sh"
elif [[ "$platform" == "darwin" ]]; then
    dev_hook="$HOME/dev_env/env_mac.sh"
else
    dev_hook="$HOME/dev_env/env_linux.sh"
fi
if [[ -f "$dev_hook" ]]; then
    source "$dev_hook" || return
fi
if [[ "$PWD" -ef "$HOME" ]]; then
    builtin cd -- "$DEV" || return
fi
if [[ ${BLE_VERSION-} && -t 0 && -t 1 && -t 2 && ${TERM:-dumb} != dumb ]]; then
    ble-attach
fi
