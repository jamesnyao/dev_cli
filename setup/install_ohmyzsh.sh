if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
  KEEP_ZSHRC=yes sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
fi
if [[ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]]; then
  git clone https://github.com/zsh-users/zsh-autosuggestions "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions"
fi

if grep -qi microsoft /proc/version 2>/dev/null; then
  if [[ "$HOST" == *devbox* ]]; then
    export ZSH_THEME_PLATFORM="devbox"
    export DEVCONFIG="wsl-devbox"
  else
    export ZSH_THEME_PLATFORM="surface"
    export DEVCONFIG="wsl-surface"
  fi
elif [[ "$platform" == "darwin" ]]; then
  export ZSH_THEME_PLATFORM="mac"
  export DEVCONFIG="mac-devbox"
else
  export ZSH_THEME_PLATFORM="$platform"
  export DEVCONFIG="$platform-devbox"
fi

export ZSH="$HOME/.oh-my-zsh"
cp $HOME/.developer.zsh-theme $ZSH/themes/developer.zsh-theme
ZSH_THEME="developer"
DISABLE_AUTO_TITLE="true"
plugins=(git zsh-autosuggestions)
source $ZSH/oh-my-zsh.sh
