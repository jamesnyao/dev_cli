PATH="$HOME/.local/bin:$PATH"
if ! command -v zoxide &>/dev/null; then
  curl -sSfL https://raw.githubusercontent.com/ajeetdsouza/zoxide/main/install.sh | sh
fi
command -v zoxide &>/dev/null && eval "$(zoxide init --cmd cd zsh)"
