PATH="$HOME/.config/agency/CurrentVersion:$PATH"
if ! command -v agency &>/dev/null; then
  curl -sSfL https://aka.ms/InstallTool.sh | sh -s agency
fi
