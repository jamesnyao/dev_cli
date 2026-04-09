if [[ "$platform" == "linux" ]]; then
  PATH="/usr/local/go/bin:$PATH"
  if ! command -v go &>/dev/null; then
    curl -sSfL https://go.dev/dl/go1.24.2.linux-amd64.tar.gz | sudo tar -C /usr/local -xzf -
  fi
fi
