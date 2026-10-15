#!/usr/bin/env bash
# Installs linux-keyboard-rgb for the current user and the udev rule granting keyboard LED write access.
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Run as your normal user; sudo is invoked only for the udev/group steps." >&2
  exit 1
fi

src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="$HOME/.local/bin"
app_dir="$HOME/.local/share/applications"
icon_dir="$HOME/.local/share/icons/hicolor/scalable/apps"

install -Dm755 "$src_dir/linux-keyboard-rgb.py" "$bin_dir/linux-keyboard-rgb"
install -Dm644 "$src_dir/linux-keyboard-rgb.svg" "$icon_dir/linux-keyboard-rgb.svg"

# .desktop files don't expand ~ or $HOME, so write the absolute path.
mkdir -p "$app_dir"
cat > "$app_dir/linux-keyboard-rgb.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Keyboard RGB
Comment=Change keyboard backlight colour and effect
Exec=$bin_dir/linux-keyboard-rgb
Icon=linux-keyboard-rgb
# Lets GNOME match the running window (Wayland app_id) to this launcher and its icon.
StartupWMClass=io.github.fayazsadiq1997_sys.LinuxKeyboardRgb
Categories=Settings;HardwareSettings;
EOF

sudo groupadd -f kbdlight
sudo usermod -aG kbdlight "$USER"
sudo install -Dm644 "$src_dir/99-linux-keyboard-rgb.rules" /etc/udev/rules.d/99-linux-keyboard-rgb.rules
sudo udevadm control --reload
# Re-run the rule now so a reboot isn't needed.
sudo udevadm trigger --action=add --subsystem-match=leds --sysname-match='asus::kbd_backlight' --settle

echo "Installed. Reboot so the kbdlight group membership takes effect (logging out may not be enough)."
