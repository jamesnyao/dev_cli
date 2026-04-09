PATH="$HOME/.config/agency/CurrentVersion:$PATH"
[[ -n "$OLD_PATH" && ":${OLD_PATH}:" != *":$HOME/.config/agency/CurrentVersion:"* ]] && export OLD_PATH="$HOME/.config/agency/CurrentVersion:$OLD_PATH"
if ! command -v agency &>/dev/null; then
  curl -sSfL https://aka.ms/InstallTool.sh | sh -s agency
fi
