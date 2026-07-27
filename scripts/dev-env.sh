#!/usr/bin/env bash
# Create (or repair) the .venv used for TuxFlow development.
#
# Safe to run repeatedly. It notices a virtual environment that no longer
# matches this checkout — for example after the project directory was moved —
# and rebuilds it instead of failing with a confusing import error.

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/common.sh
source "$PROJECT_DIR/scripts/common.sh"

VENV_DIR="$PROJECT_DIR/.venv"
RECREATE=0
EXTRAS="speech,dev"

usage() {
  cat <<'USAGE'
Usage: ./scripts/dev-env.sh [options]

  --recreate   Delete .venv first and build it from scratch
  --minimal    Skip faster-whisper; installs only the test and lint tools
  -h, --help   Show this message

Set TUXFLOW_PYTHON to choose the interpreter.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recreate) RECREATE=1 ;;
    --minimal) EXTRAS="dev" ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
  shift
done

OS="$(detect_os)"
[[ "$OS" != "unsupported" ]] || die "TuxFlow supports Linux and macOS; this is $(uname -s)"

PYTHON="$(find_python)" || die "Python 3.11 or newer is required but was not found"

# The desktop app needs the system PyGObject, so on macOS the venv has to come
# from the same Homebrew Python that Homebrew built pygobject3 for.
if [[ "$OS" == "macos" ]]; then
  if GTK_PYTHON="$(brew_gtk_python)"; then
    PYTHON="$GTK_PYTHON"
  fi
fi

venv_is_healthy() {
  [[ -x "$VENV_DIR/bin/python" ]] || return 1
  "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1 || return 1
  python_is_supported "$VENV_DIR/bin/python" || return 1
  return 0
}

if [[ "$RECREATE" -eq 1 ]]; then
  log "Removing $VENV_DIR"
  rm -rf "$VENV_DIR"
elif [[ -d "$VENV_DIR" ]] && ! venv_is_healthy; then
  warn "$VENV_DIR is broken or built by an interpreter that is gone; rebuilding it"
  rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  log "Creating $VENV_DIR with $PYTHON"
  "$PYTHON" -m venv --system-site-packages "$VENV_DIR" ||
    die "Could not create the virtual environment. On Debian or Ubuntu install python3-venv."
fi

log "Installing TuxFlow in editable mode with extras: $EXTRAS"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet -e "${PROJECT_DIR}[${EXTRAS}]"

# A stale editable install points at wherever the project used to live, so
# confirm the package really resolves inside this checkout.
RESOLVED="$("$VENV_DIR/bin/python" -c 'import tuxflow, pathlib; print(pathlib.Path(tuxflow.__file__).resolve())')"
case "$RESOLVED" in
  "$PROJECT_DIR"/*) ;;
  *) die "tuxflow resolves to $RESOLVED instead of this checkout. Try: ./scripts/dev-env.sh --recreate" ;;
esac

log "Ready."
info "Tests:  make test"
info "Lint:   make lint"
info "Run:    .venv/bin/tuxflow daemon    (then, in another shell, .venv/bin/tuxflow app)"
