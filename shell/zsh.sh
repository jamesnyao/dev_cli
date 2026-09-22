# Generic zsh environment. Sourced from ~/.zshrc (written by `dev init`).
# Work-specific setup (enlistment PATHs, internal tools, custom prompt
# theme) lives in a private hook sourced at the very end, if present.

export platform=$(uname | tr '[:upper:]' '[:lower:]')

# Detect WSL and set a display name for the prompt
if [[ "$platform" == "linux" ]] && grep -qi microsoft /proc/version 2>/dev/null; then
  export ZSH_THEME_PLATFORM="wsl"
else
  export ZSH_THEME_PLATFORM="$platform"
fi

export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="${DEV_ZSH_THEME_NAME:-robbyrussell}"

zstyle ':omz:update' mode auto
DISABLE_AUTO_TITLE="true"

plugins=(git zsh-autosuggestions colored-man-pages colorize)
if [[ "$platform" == "darwin" ]]; then
  plugins+=(brew)
fi

[[ -f "$ZSH/oh-my-zsh.sh" ]] && source "$ZSH/oh-my-zsh.sh"

# Linux-specific PATH
if [[ "$platform" == "linux" ]]; then
  PATH="$HOME/.local/bin:/usr/local/go/bin:$PATH"
fi

# Dev CLI
export PATH="$HOME/dev_cli:$PATH"
export DEVCONFIG="${DEVCONFIG:-example-machine}"
DEV="$(dev repo root)" || return
export DEV
if [[ -z "$DEV_PROMPT_USER" ]]; then
  DEV_PROMPT_USER="$(dev config get identity.username)" || return
  export DEV_PROMPT_USER="${DEV_PROMPT_USER:-${USERNAME:-$USER}}"
fi

# zoxide (replaces cd)
if command -v zoxide &>/dev/null; then
  eval "$(zoxide init --cmd cd zsh)"
elif [[ "$platform" == "linux" ]]; then
  curl -sSfL https://raw.githubusercontent.com/ajeetdsouza/zoxide/main/install.sh | sh
  eval "$(zoxide init --cmd cd zsh)"
fi

# fzf (fuzzy matching)
if [[ "$platform" == "linux" ]]; then
  PATH="$HOME/.fzf/bin/:$PATH"
  if ! command -v fzf &>/dev/null; then
    git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
    ~/.fzf/install
  fi
fi
[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh
command -v fzf &>/dev/null && source <(fzf --zsh) 2>/dev/null

[[ -n "$DEV" ]] && cd "$DEV"

# Private, work-specific setup -- runs last so it can override anything above.
if [[ -f "$HOME/dev_env/env.sh" ]]; then
  source "$HOME/dev_env/env.sh"
fi
