ble-import config/readline || return
bleopt complete_auto_complete=1 complete_auto_complete_opts=syntax-disabled
ble-face -s auto_complete fg=8

if command -v fzf >/dev/null 2>&1; then
    ble-import -d integration/fzf-completion || return
    ble-import -d integration/fzf-key-bindings || return
fi

if [[ -f "$HOME/.blerc" ]]; then
    source "$HOME/.blerc"
elif [[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/blesh/init.sh" ]]; then
    source "${XDG_CONFIG_HOME:-$HOME/.config}/blesh/init.sh"
fi
