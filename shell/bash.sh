# Generic bash bootstrap. Sourced from ~/.bashrc (written by `dev init`).
# Installs zsh + oh-my-zsh if missing, then hands off to it -- bash itself
# is never used interactively here, so nothing below `exec zsh` matters.

case $- in
    *i*) ;;
      *) return;;
esac

if [ -t 1 ] && command -v apt-get >/dev/null 2>&1; then
  if ! command -v zsh >/dev/null 2>&1; then
    echo "Installing zsh..."
    sudo apt-get update -qq && sudo apt-get install -y -qq zsh
  fi
  if command -v zsh >/dev/null 2>&1; then
    if [ ! -d "$HOME/.oh-my-zsh" ]; then
      echo "Installing oh-my-zsh..."
      sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
      git -C "$HOME" checkout .zshrc
    fi
    if [ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]; then
      echo "Installing zsh-autosuggestions..."
      git clone --depth 1 https://github.com/zsh-users/zsh-autosuggestions "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions"
    fi
    exec zsh
  fi
fi
