#!/usr/bin/env bash
set -euo pipefail

case "$OSTYPE" in
    msys*|cygwin*) export PATH="/usr/bin:/mingw64/bin:$PATH" ;;
esac

if (( BASH_VERSINFO[0] < 4 )); then
    printf 'Bash autosuggestions require Bash 4 or newer; keeping the existing line editor.\n'
    exit 0
fi

destination="$HOME/.local/share/blesh"
[[ -f "$destination/ble.sh" ]] && exit 0
if [[ -e "$destination" ]]; then
    printf 'Incomplete ble.sh installation at %s; move it aside and rerun dev init.\n' "$destination" >&2
    exit 1
fi

for tool in curl tar xz awk; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Bash autosuggestions setup requires %s; install it and rerun dev init.\n' "$tool" >&2
        exit 1
    fi
done

mkdir -p "${destination%/*}"
work="$(mktemp -d "${destination%/*}/.blesh-install.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
curl --fail --location --silent --show-error --connect-timeout 15 --max-time 120 \
    https://github.com/akinomyoga/ble.sh/releases/download/nightly/ble-nightly.tar.xz \
    -o "$work/ble.tar.xz"
tar -xJf "$work/ble.tar.xz" -C "$work"
export USER="${USER:-${USERNAME:-$(id -un)}}"
bash "$work/ble-nightly/ble.sh" --install "$work/install"
if [[ ! -f "$work/install/blesh/ble.sh" ]]; then
    printf 'ble.sh installer did not produce a usable installation.\n' >&2
    exit 1
fi
mv "$work/install/blesh" "$destination"
printf 'Installed Bash autosuggestions in %s\n' "$destination"
