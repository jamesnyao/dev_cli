PATH="$HOME/.local/bin:$PATH"
[[ -n "$OLD_PATH" && ":${OLD_PATH}:" != *":$HOME/.local/bin:"* ]] && export OLD_PATH="$HOME/.local/bin:$OLD_PATH"
if ! command -v zoxide &>/dev/null; then
  curl -sSfL https://raw.githubusercontent.com/ajeetdsouza/zoxide/main/install.sh | sh
fi
command -v zoxide &>/dev/null && eval "$(zoxide init --cmd cd zsh)"
