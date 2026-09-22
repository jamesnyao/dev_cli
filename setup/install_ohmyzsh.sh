if [[ ! -f "$HOME/.oh-my-zsh/oh-my-zsh.sh" ]]; then
  if [[ -d "$HOME/.oh-my-zsh" ]]; then
    mv "$HOME/.oh-my-zsh" "$HOME/.oh-my-zsh.bak.$(date +%Y%m%d%H%M%S)"
  fi
  KEEP_ZSHRC=yes sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
fi
if [[ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]]; then
  git clone https://github.com/zsh-users/zsh-autosuggestions "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions"
fi

export ZSH="$HOME/.oh-my-zsh"

# A private config can supply a custom theme file (DEV_ZSH_THEME_FILE) and
# name (DEV_ZSH_THEME_NAME); otherwise this falls back to a stock theme.
ZSH_THEME="${DEV_ZSH_THEME_NAME:-robbyrussell}"
if [[ -n "$DEV_ZSH_THEME_FILE" && -f "$DEV_ZSH_THEME_FILE" ]]; then
  cp "$DEV_ZSH_THEME_FILE" "$ZSH/themes/$ZSH_THEME.zsh-theme"
fi
ZSH_THEME_PLATFORM="$DEVCONFIG"
DISABLE_AUTO_TITLE="true"
plugins=(git zsh-autosuggestions)
source $ZSH/oh-my-zsh.sh
title "${DEV_PROMPT_USER:-$USER} $DEVCONFIG"
