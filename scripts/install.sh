#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/tuxflow/venv"
LOCAL_BIN="$HOME/.local/bin"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

install_system_packages() {
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y \
      python3 python3-gobject gtk4 libadwaita pipewire-utils wl-clipboard libnotify ydotool
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y \
      python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
      pipewire-bin wl-clipboard libnotify-bin
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed \
      python python-gobject gtk4 libadwaita pipewire wl-clipboard libnotify ydotool
  else
    echo "Unsupported package manager. Install GTK 4, Libadwaita, PyGObject, PipeWire, and wl-clipboard first." >&2
    exit 1
  fi
}

if [[ "${1:-}" != "--skip-system-packages" ]]; then
  install_system_packages
fi

mkdir -p "$LOCAL_BIN" "$APPLICATIONS_DIR" "$ICONS_DIR" "$SYSTEMD_DIR"
python3 -m venv --system-site-packages "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install "$PROJECT_DIR[speech]"
ln -sfn "$VENV_DIR/bin/tuxflow" "$LOCAL_BIN/tuxflow"

install -m 0644 "$PROJECT_DIR/data/io.github.tuxflow.TuxFlow.desktop" "$APPLICATIONS_DIR/"
install -m 0644 "$PROJECT_DIR/data/io.github.tuxflow.TuxFlow.svg" "$ICONS_DIR/"
install -m 0644 "$PROJECT_DIR/data/tuxflow.service" "$SYSTEMD_DIR/"

systemctl --user daemon-reload
systemctl --user enable --now tuxflow.service
update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true
gtk-update-icon-cache "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true

echo
echo "TuxFlow is installed."
echo "Open it from your application launcher, or run: tuxflow app"
echo "The first launch asks you to approve a global shortcut."
