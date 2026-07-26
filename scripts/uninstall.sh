#!/usr/bin/env bash
set -euo pipefail

VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/tuxflow/venv"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now tuxflow.service 2>/dev/null || true
rm -f \
  "$HOME/.local/bin/tuxflow" \
  "$APPLICATIONS_DIR/io.github.tuxflow.TuxFlow.desktop" \
  "$ICONS_DIR/io.github.tuxflow.TuxFlow.svg" \
  "$SYSTEMD_DIR/tuxflow.service"
rm -rf "$VENV_DIR"
systemctl --user daemon-reload

echo "TuxFlow was uninstalled. Settings, models, and history were kept."
echo "To remove that data too, delete ${XDG_DATA_HOME:-$HOME/.local/share}/tuxflow and ${XDG_CONFIG_HOME:-$HOME/.config}/tuxflow."
