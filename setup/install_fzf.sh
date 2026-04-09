PATH="$HOME/.fzf/bin:$PATH"
if ! command -v fzf &>/dev/null; then
  git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
  ~/.fzf/install --all
fi
command -v fzf &>/dev/null && source <(fzf --zsh)
[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh
