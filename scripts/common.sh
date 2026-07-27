#!/usr/bin/env bash
# Shared helpers for TuxFlow's install, uninstall, and development scripts.
# Sourced, never executed directly.

set -euo pipefail

BOLD=""
DIM=""
RESET=""
if [[ -t 1 ]]; then
  BOLD="$(printf '\033[1m')"
  DIM="$(printf '\033[2m')"
  RESET="$(printf '\033[0m')"
fi

log() { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
info() { printf '    %s%s%s\n' "$DIM" "$*" "$RESET"; }
warn() { printf '%s!!%s %s\n' "$BOLD" "$RESET" "$*" >&2; }
die() {
  printf '%serror:%s %s\n' "$BOLD" "$RESET" "$*" >&2
  exit 1
}

have() { command -v "$1" >/dev/null 2>&1; }

# Prints "macos" or "linux"; anything else is unsupported.
detect_os() {
  case "$(uname -s)" in
    Darwin) printf 'macos\n' ;;
    Linux) printf 'linux\n' ;;
    *) printf 'unsupported\n' ;;
  esac
}

python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    >/dev/null 2>&1
}

# Prints the path to a Python interpreter TuxFlow can run on, or fails.
# Set TUXFLOW_PYTHON to pin a specific interpreter.
find_python() {
  local candidate
  if [[ -n "${TUXFLOW_PYTHON:-}" ]]; then
    python_is_supported "$TUXFLOW_PYTHON" ||
      die "TUXFLOW_PYTHON=$TUXFLOW_PYTHON is not Python 3.11 or newer"
    command -v "$TUXFLOW_PYTHON"
    return 0
  fi
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if have "$candidate" && python_is_supported "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

# macOS builds PyGObject against one specific Homebrew Python. A venv made from
# any other interpreter cannot see the `gi` module, so the desktop app would be
# missing even though Homebrew installed it.
brew_gtk_python() {
  have brew || return 1
  local formula prefix version
  formula="$(brew deps pygobject3 2>/dev/null | grep -E '^python@3\.[0-9]+$' | tail -n 1 || true)"
  [[ -n "$formula" ]] || return 1
  prefix="$(brew --prefix "$formula" 2>/dev/null)" || return 1
  version="${formula#python@}"
  [[ -x "$prefix/bin/python$version" ]] || return 1
  printf '%s\n' "$prefix/bin/python$version"
}

path_contains() {
  case ":$PATH:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

data_home() { printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}"; }
config_home() { printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}"; }
cache_home() { printf '%s\n' "${XDG_CACHE_HOME:-$HOME/.cache}"; }
venv_dir() { printf '%s/tuxflow/venv\n' "$(data_home)"; }
