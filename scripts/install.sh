#!/usr/bin/env bash
# Install TuxFlow for the current user on Linux or macOS.
#
# Nothing is installed system-wide except the desktop packages your package
# manager provides. TuxFlow itself lives in a virtual environment under your
# data directory and can be removed with ./scripts/uninstall.sh.

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/common.sh
source "$PROJECT_DIR/scripts/common.sh"

APP_ID="io.github.tuxflow.TuxFlow"
VENV_DIR="$(venv_dir)"
LOCAL_BIN="$HOME/.local/bin"
TUXFLOW_BIN="$VENV_DIR/bin/tuxflow"

SKIP_SYSTEM_PACKAGES=0
INSTALL_SERVICE=1
WITH_UINPUT=0
INSTALL_CMD=()

usage() {
  cat <<'USAGE'
Usage: ./scripts/install.sh [options]

  --skip-system-packages  Do not install desktop packages with dnf/apt/pacman/zypper/brew
  --no-service            Install TuxFlow but do not start it at login
  --with-uinput           Linux only: allow ydotool to type, so paste is automatic
  -h, --help              Show this message

Set TUXFLOW_PYTHON to pin the Python interpreter used for the environment.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-system-packages) SKIP_SYSTEM_PACKAGES=1 ;;
    --no-service) INSTALL_SERVICE=0 ;;
    --with-uinput) WITH_UINPUT=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
  shift
done

OS="$(detect_os)"
[[ "$OS" != "unsupported" ]] ||
  die "TuxFlow supports Linux and macOS. This machine reports $(uname -s)."

# --------------------------------------------------------------------------- #
# System packages
# --------------------------------------------------------------------------- #

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

can_elevate() {
  [[ "$(id -u)" -eq 0 ]] || have sudo
}

# Install the list, then retry one package at a time so a single unavailable
# name never aborts the whole install — distributions rename these constantly.
install_packages() {
  local package
  if as_root "${INSTALL_CMD[@]}" "$@"; then
    return 0
  fi
  warn "Installing the desktop packages together failed; retrying one at a time"
  for package in "$@"; do
    if ! as_root "${INSTALL_CMD[@]}" "$package"; then
      warn "Skipped $package — install it yourself if something turns out to be missing"
    fi
  done
}

install_linux_packages() {
  local packages
  if have dnf; then
    INSTALL_CMD=(dnf install -y)
    packages=(python3 python3-gobject gtk4 libadwaita pipewire-utils wl-clipboard
      libnotify ydotool)
  elif have apt-get; then
    INSTALL_CMD=(apt-get install -y)
    as_root apt-get update || warn "apt-get update failed; continuing anyway"
    packages=(python3 python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pipewire-bin
      wl-clipboard libnotify-bin ydotool)
  elif have pacman; then
    INSTALL_CMD=(pacman -S --needed --noconfirm)
    packages=(python python-gobject gtk4 libadwaita pipewire wl-clipboard libnotify ydotool)
  elif have zypper; then
    INSTALL_CMD=(zypper install -y)
    packages=(python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 pipewire-tools
      wl-clipboard libnotify-tools ydotool)
  else
    warn "No supported package manager was found."
    info "Install GTK 4, Libadwaita, PyGObject, PipeWire utilities, and wl-clipboard yourself,"
    info "then rerun with --skip-system-packages."
    return 0
  fi
  if ! can_elevate; then
    warn "Installing packages needs root, and sudo is not available."
    info "Install them yourself, then rerun with --skip-system-packages."
    return 0
  fi
  log "Installing desktop packages"
  install_packages "${packages[@]}"
}

install_macos_packages() {
  if ! have brew; then
    die "Homebrew is required for the desktop dependencies.
       Install it from https://brew.sh and run this script again, or rerun with
       --skip-system-packages if you installed them another way."
  fi
  log "Installing Homebrew packages"
  local package
  # ffmpeg records the microphone; the rest is the GTK control center.
  for package in ffmpeg pygobject3 gtk4 libadwaita adwaita-icon-theme; do
    if brew list --formula "$package" >/dev/null 2>&1; then
      info "$package is already installed"
    elif ! brew install "$package"; then
      warn "Skipped $package — install it yourself if something turns out to be missing"
    fi
  done
}

if [[ "$SKIP_SYSTEM_PACKAGES" -eq 1 ]]; then
  info "Skipping system packages at your request"
elif [[ "$OS" == "macos" ]]; then
  install_macos_packages
else
  install_linux_packages
fi

# --------------------------------------------------------------------------- #
# Python environment
# --------------------------------------------------------------------------- #

PYTHON=""
if [[ "$OS" == "macos" ]]; then
  # PyGObject is built for one specific Homebrew Python; the environment has to
  # match it or the desktop app will not find the `gi` module.
  PYTHON="$(brew_gtk_python || true)"
fi
if [[ -z "$PYTHON" ]]; then
  PYTHON="$(find_python || true)"
fi
if [[ -z "$PYTHON" ]]; then
  if [[ "$OS" == "macos" ]] && have brew; then
    log "Installing Python 3.12 with Homebrew"
    brew install python@3.12 || die "Could not install Python 3.12"
    PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
  else
    die "Python 3.11 or newer is required but was not found. Install it and run this again."
  fi
fi

log "Creating the TuxFlow environment"
info "Interpreter: $PYTHON"
info "Location:    $VENV_DIR"
mkdir -p "$LOCAL_BIN" "$(dirname "$VENV_DIR")"
if [[ -x "$VENV_DIR/bin/python" ]] && ! "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
  warn "The existing environment is broken; rebuilding it"
  rm -rf "$VENV_DIR"
fi
"$PYTHON" -m venv --system-site-packages "$VENV_DIR" ||
  die "Could not create the environment. On Debian or Ubuntu, install python3-venv first."

log "Installing TuxFlow and the Whisper engine"
info "The speech engine is a few hundred MB and is downloaded once."
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install "${PROJECT_DIR}[speech]" ||
  die "Installing TuxFlow failed. The output above says why."

ln -sfn "$TUXFLOW_BIN" "$LOCAL_BIN/tuxflow"

# --------------------------------------------------------------------------- #
# Desktop integration
# --------------------------------------------------------------------------- #

install_linux_integration() {
  local applications_dir icons_dir systemd_dir
  applications_dir="$(data_home)/applications"
  icons_dir="$(data_home)/icons/hicolor/scalable/apps"
  systemd_dir="$(config_home)/systemd/user"
  mkdir -p "$applications_dir" "$icons_dir" "$systemd_dir"

  install -m 0644 "$PROJECT_DIR/data/$APP_ID.desktop" "$applications_dir/"
  install -m 0644 "$PROJECT_DIR/data/$APP_ID.svg" "$icons_dir/"
  sed "s|@TUXFLOW_BIN@|$TUXFLOW_BIN|g" "$PROJECT_DIR/data/tuxflow.service.in" \
    >"$systemd_dir/tuxflow.service"

  update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
  gtk-update-icon-cache "$(data_home)/icons/hicolor" >/dev/null 2>&1 || true

  if [[ "$INSTALL_SERVICE" -eq 0 ]]; then
    info "Service file written but not enabled (--no-service)"
    return 0
  fi
  if ! have systemctl; then
    warn "systemd was not found; start TuxFlow yourself with: tuxflow daemon"
    return 0
  fi
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  systemctl --user enable tuxflow.service >/dev/null 2>&1 || true
  # restart, not start: on a reinstall the old process is still running the
  # previous version and would keep serving it.
  if ! systemctl --user restart tuxflow.service >/dev/null 2>&1; then
    warn "Could not start the user service from here. Inside a desktop session, run:"
    info "systemctl --user enable --now tuxflow.service"
  fi
}

install_uinput_rule() {
  log "Allowing ydotool to type the paste shortcut"
  if ! can_elevate; then
    warn "This needs root and sudo is not available; automatic paste stays unavailable"
    return 0
  fi
  if ! as_root install -m 0644 "$PROJECT_DIR/data/99-tuxflow-uinput.rules" \
    /etc/udev/rules.d/99-tuxflow-uinput.rules; then
    warn "Could not write the udev rule; automatic paste stays unavailable"
    return 0
  fi
  getent group input >/dev/null 2>&1 || as_root groupadd input || true
  as_root usermod -aG input "$USER" || warn "Could not add $USER to the input group"
  as_root udevadm control --reload-rules || true
  as_root udevadm trigger --subsystem-match=misc --sysname-match=uinput || true
  info "Log out and back in once so your session picks up the input group."
}

install_macos_integration() {
  local agents_dir plist log_dir app_dir brew_bin
  agents_dir="$HOME/Library/LaunchAgents"
  plist="$agents_dir/$APP_ID.plist"
  log_dir="$HOME/Library/Logs"
  app_dir="$HOME/Applications/TuxFlow.app"
  brew_bin="/usr/local/bin"
  if have brew; then
    brew_bin="$(brew --prefix)/bin"
  fi

  mkdir -p "$agents_dir" "$log_dir" "$app_dir/Contents/MacOS"

  # A tiny .app so TuxFlow appears in Launchpad and Spotlight.
  cat >"$app_dir/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>TuxFlow</string>
  <key>CFBundleDisplayName</key><string>TuxFlow</string>
  <key>CFBundleIdentifier</key><string>$APP_ID</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>TuxFlow</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>TuxFlow records your voice locally so it can be transcribed on this Mac.</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict>
</plist>
PLIST
  cat >"$app_dir/Contents/MacOS/TuxFlow" <<LAUNCHER
#!/bin/sh
export PATH="$brew_bin:/usr/bin:/bin:/usr/sbin:/sbin"
exec "$TUXFLOW_BIN" app
LAUNCHER
  chmod +x "$app_dir/Contents/MacOS/TuxFlow"

  sed -e "s|@TUXFLOW_BIN@|$TUXFLOW_BIN|g" \
    -e "s|@TUXFLOW_PATH@|$brew_bin:/usr/bin:/bin:/usr/sbin:/sbin|g" \
    -e "s|@TUXFLOW_LOG@|$log_dir/tuxflow.log|g" \
    "$PROJECT_DIR/data/$APP_ID.plist.in" >"$plist"

  if [[ "$INSTALL_SERVICE" -eq 0 ]]; then
    info "Launch agent written but not loaded (--no-service)"
    return 0
  fi
  launchctl bootout "gui/$UID/$APP_ID" >/dev/null 2>&1 || true
  if ! launchctl bootstrap "gui/$UID" "$plist" >/dev/null 2>&1; then
    launchctl load -w "$plist" >/dev/null 2>&1 ||
      warn "Could not load the launch agent; start TuxFlow yourself with: tuxflow daemon"
  fi
}

log "Setting up desktop integration"
if [[ "$OS" == "macos" ]]; then
  install_macos_integration
else
  install_linux_integration
  if [[ "$WITH_UINPUT" -eq 1 ]]; then
    install_uinput_rule
  fi
fi

# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

# The service takes a moment to open its socket, and the report below would
# otherwise say it is not running on a perfectly good install.
wait_for_service() {
  local socket attempt
  socket="$("$VENV_DIR/bin/python" -c \
    'from tuxflow.paths import socket_file; print(socket_file())' 2>/dev/null)" || return 0
  [[ -n "$socket" ]] || return 0
  for attempt in $(seq 1 20); do
    if [[ -S "$socket" ]]; then
      return 0
    fi
    sleep 0.25
  done
}

if [[ "$INSTALL_SERVICE" -eq 1 ]]; then
  wait_for_service
fi

echo
log "TuxFlow is installed."
"$TUXFLOW_BIN" doctor || true
echo

if ! path_contains "$LOCAL_BIN"; then
  warn "$LOCAL_BIN is not on your PATH. Add it to your shell profile to run \`tuxflow\`."
fi

if [[ "$OS" == "macos" ]]; then
  cat <<'NEXT'
Next steps on macOS:

  1. Run `tuxflow daemon` once in Terminal and hold your dictation key. macOS
     asks for Microphone and Accessibility access the first time, and those
     prompts do not appear for background agents — so this first run matters.
  2. System Settings › Keyboard › "Press 🌐 key to" → Do Nothing, so holding fn
     dictates instead of opening the emoji picker.
  3. Open TuxFlow from Launchpad, or run `tuxflow app`.
NEXT
else
  cat <<'NEXT'
Next steps on Linux:

  1. Open TuxFlow from your application launcher, or run `tuxflow app`.
  2. Your desktop asks you to approve the global shortcut the first time the
     background service starts. The suggested shortcut is Ctrl+Super+Space.
  3. If `tuxflow doctor` says automatic paste is unavailable, rerun this script
     with --with-uinput.
NEXT
fi
