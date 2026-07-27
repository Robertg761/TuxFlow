#!/usr/bin/env bash
# Remove what ./scripts/install.sh added, on Linux or macOS.
#
# Your settings, downloaded models, and history are kept unless you pass
# --purge, because re-downloading the speech model is slow.

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/common.sh
source "$PROJECT_DIR/scripts/common.sh"

APP_ID="io.github.tuxflow.TuxFlow"
PURGE=0

usage() {
  cat <<'USAGE'
Usage: ./scripts/uninstall.sh [options]

  --purge      Also delete settings, downloaded models, and history
  -h, --help   Show this message
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
  shift
done

OS="$(detect_os)"

stop_linux_service() {
  have systemctl || return 0
  systemctl --user disable --now tuxflow.service >/dev/null 2>&1 || true
  systemctl --user daemon-reload >/dev/null 2>&1 || true
}

stop_macos_agent() {
  launchctl bootout "gui/$UID/$APP_ID" >/dev/null 2>&1 ||
    launchctl unload -w "$HOME/Library/LaunchAgents/$APP_ID.plist" >/dev/null 2>&1 || true
}

log "Stopping TuxFlow"
if [[ "$OS" == "macos" ]]; then
  stop_macos_agent
else
  stop_linux_service
fi
pkill -f 'tuxflow (daemon|app)' >/dev/null 2>&1 || true

log "Removing the installed files"
rm -f "$HOME/.local/bin/tuxflow"
rm -rf "$(venv_dir)"

if [[ "$OS" == "macos" ]]; then
  rm -f "$HOME/Library/LaunchAgents/$APP_ID.plist"
  rm -rf "$HOME/Applications/TuxFlow.app"
  rm -f "$HOME/Library/Logs/tuxflow.log"
else
  rm -f \
    "$(data_home)/applications/$APP_ID.desktop" \
    "$(data_home)/icons/hicolor/scalable/apps/$APP_ID.svg" \
    "$(config_home)/systemd/user/tuxflow.service"
  update-desktop-database "$(data_home)/applications" >/dev/null 2>&1 || true

  if [[ -f /etc/udev/rules.d/99-tuxflow-uinput.rules ]]; then
    info "The udev rule for ydotool is still installed. Remove it with:"
    info "sudo rm /etc/udev/rules.d/99-tuxflow-uinput.rules && sudo udevadm control --reload-rules"
  fi
fi

# The venv directory is gone; drop its parent too if nothing else lives there.
rmdir "$(data_home)/tuxflow" >/dev/null 2>&1 || true

if [[ "$PURGE" -eq 1 ]]; then
  log "Deleting settings, models, and history"
  rm -rf "$(data_home)/tuxflow" "$(config_home)/tuxflow" "$(cache_home)/tuxflow"
  echo
  log "TuxFlow and all of its data were removed."
else
  echo
  log "TuxFlow was uninstalled. Settings, models, and history were kept."
  info "Delete those too with: ./scripts/uninstall.sh --purge"
  info "Or by hand: $(data_home)/tuxflow, $(config_home)/tuxflow, $(cache_home)/tuxflow"
fi
