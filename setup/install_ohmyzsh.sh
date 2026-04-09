if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
  KEEP_ZSHRC=yes sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
fi
if [[ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]]; then
  git clone https://github.com/zsh-users/zsh-autosuggestions "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions"
fi

export ZSH="$HOME/.oh-my-zsh"
cp $HOME/.developer.zsh-theme $ZSH/themes/developer.zsh-theme
ZSH_THEME="developer"
ZSH_THEME_PLATFORM="$DEVCONFIG"
DISABLE_AUTO_TITLE="true"
plugins=(git zsh-autosuggestions)
source $ZSH/oh-my-zsh.sh
title "developer $DEVCONFIG"
