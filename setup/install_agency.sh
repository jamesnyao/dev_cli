PATH="$HOME/.config/agency/CurrentVersion:$PATH"
[[ -n "$OLD_PATH" && ":${OLD_PATH}:" != *":$HOME/.config/agency/CurrentVersion:"* ]] && export OLD_PATH="$HOME/.config/agency/CurrentVersion:$OLD_PATH"
if ! command -v agency &>/dev/null; then
  # The installer shells out to cmd.exe for Windows auth when run from WSL.
  # If appendWindowsPath is disabled in /etc/wsl.conf, cmd.exe won't be on PATH.
  if [[ -z "$(command -v cmd.exe)" && -x /mnt/c/Windows/System32/cmd.exe ]]; then
    PATH="$PATH:/mnt/c/Windows/System32"
  fi
  curl -sSfL https://aka.ms/InstallTool.sh | sh -s agency
fi
