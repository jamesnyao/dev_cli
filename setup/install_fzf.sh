PATH="$HOME/.fzf/bin:$PATH"
[[ -n "$OLD_PATH" && ":${OLD_PATH}:" != *":$HOME/.fzf/bin:"* ]] && export OLD_PATH="$HOME/.fzf/bin:$OLD_PATH"
if ! command -v fzf &>/dev/null; then
  git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
  ~/.fzf/install --all
fi
command -v fzf &>/dev/null && source <(fzf --zsh)
