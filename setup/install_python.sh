PYTHON_VERSION=$(cat "$SETUP/PYTHON_VERSION")
if ! command -v python3 &>/dev/null; then
  if [[ "$platform" == "linux" ]]; then
    sudo apt-get install -y python${PYTHON_VERSION}
  elif [[ "$platform" == "darwin" ]]; then
    brew install python@${PYTHON_VERSION}
  fi
fi
