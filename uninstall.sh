#!/usr/bin/env bash
# Removes everything install.sh added: the user-level app files, saved settings, the udev rule and the kbdlight group.
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Run as your normal user; sudo is invoked only for the udev/group steps." >&2
  exit 1
fi

rm -f "$HOME/.local/bin/linux-keyboard-rgb" \
  "$HOME/.local/share/applications/linux-keyboard-rgb.desktop" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/linux-keyboard-rgb.svg"
rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/linux-keyboard-rgb"

sudo rm -f /etc/udev/rules.d/99-linux-keyboard-rgb.rules
sudo udevadm control --reload

# Guarded so a partial install or a second run doesn't abort under set -e.
if getent group kbdlight > /dev/null; then
  if id -nG "$USER" | tr ' ' '\n' | grep -qx kbdlight; then
    sudo gpasswd -d "$USER" kbdlight > /dev/null
  fi
  sudo groupdel kbdlight
fi

echo "Uninstalled. The keyboard keeps the last colour saved to its firmware."
echo "The LED files keep their kbdlight permissions until the next reboot."
