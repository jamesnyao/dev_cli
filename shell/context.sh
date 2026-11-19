# Shared bash/zsh startup context; Python remains authoritative for related roots.
_dev_private_root="$HOME"
_dev_startup_dir="$PWD"
_dev_contextual=0
if [[ ! "$_dev_cli_root" -ef "$HOME/dev_cli" || -n "${DEV_CONFIG_OVERRIDE:-}" ]]; then
    _dev_contextual=1
else
    _dev_cwd_root="$(git -C "$PWD" rev-parse --show-toplevel </dev/null 2>/dev/null)"
    if [[ -n "$_dev_cwd_root" && ! "$_dev_cwd_root" -ef "$HOME"
          && ! "$_dev_cwd_root" -ef "$_dev_cli_root" ]]; then
        _dev_cwd_common="$(git -C "$PWD" rev-parse --path-format=absolute --git-common-dir </dev/null 2>/dev/null)"
        for _dev_anchor in "$_dev_cli_root" "$HOME"; do
            _dev_anchor_common="$(git -C "$_dev_anchor" rev-parse --path-format=absolute --git-common-dir </dev/null 2>/dev/null)"
            if [[ -n "$_dev_cwd_common" && "$_dev_cwd_common" == "$_dev_anchor_common" ]]; then
                _dev_contextual=1
                break
            fi
        done
    fi
fi
if (( _dev_contextual )); then
    _dev_private_root="$(_dev_cli config context --field privateRoot)" || return
    case "$platform" in
        mingw*|msys*|cygwin*) _dev_private_root="$(cygpath -u "$_dev_private_root")" || return ;;
    esac
fi
unset _dev_cwd_root _dev_cwd_common _dev_anchor _dev_anchor_common
