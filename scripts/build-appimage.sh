#!/usr/bin/env bash
# Build a self-contained Linux AppImage of TuxFlow.
#
# This has to run on Ubuntu 24.04, which is the oldest distribution carrying a
# libadwaita new enough for the control center (Adw.ToolbarView and
# Adw.SwitchRow need 1.4). That fixes the floor at glibc 2.39, so the result
# runs on Ubuntu 24.04+, Fedora 40+, and Debian 13+.
#
#   Locally:  ./scripts/build-appimage.sh --in-container
#   In CI:    ./scripts/build-appimage.sh          (already on ubuntu-24.04)
#
# The AppImage bundles Python, GTK 4, libadwaita, PyGObject, faster-whisper,
# and TuxFlow itself. It deliberately does not bundle the microphone recorder,
# the clipboard tools, or ydotool: those talk to host daemons (PipeWire, the
# Wayland compositor, /dev/uinput) and work far better as the host's own copies.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="docker.io/library/ubuntu:24.04"
PYTHON_VERSION="3.12"
ARCH="x86_64"

# --------------------------------------------------------------------------- #
# Run the whole build inside a container when asked
# --------------------------------------------------------------------------- #

if [[ "${1:-}" == "--in-container" ]]; then
  runtime=""
  for candidate in podman docker; do
    if command -v "$candidate" >/dev/null 2>&1; then
      runtime="$candidate"
      break
    fi
  done
  [[ -n "$runtime" ]] || {
    echo "Need podman or docker to build in a container" >&2
    exit 1
  }
  echo "==> Building in $IMAGE with $runtime"
  exec "$runtime" run --rm \
    -v "$PROJECT_DIR:/src:z" \
    -w /src \
    "$IMAGE" \
    bash /src/scripts/build-appimage.sh
fi

if [[ ! -r /etc/os-release ]] || ! grep -q "24.04" /etc/os-release; then
  echo "This script expects Ubuntu 24.04. Use --in-container, or run it on an" >&2
  echo "ubuntu-24.04 CI runner." >&2
  exit 1
fi

log() { echo "==> $*"; }

export DEBIAN_FRONTEND=noninteractive

# --------------------------------------------------------------------------- #
# Build dependencies
# --------------------------------------------------------------------------- #

log "Installing build dependencies"
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  ca-certificates wget file desktop-file-utils zsync squashfs-tools \
  dpkg-dev patchelf pkg-config \
  "python$PYTHON_VERSION" "python$PYTHON_VERSION-venv" "python$PYTHON_VERSION-dev" \
  python3-pip python3-gi python3-gi-cairo \
  libgtk-4-1 gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1 \
  libglib2.0-bin libgdk-pixbuf-2.0-0 libgdk-pixbuf2.0-bin \
  librsvg2-common adwaita-icon-theme shared-mime-info \
  >/dev/null

# The GTK plugin locates the pixbuf loader directory, the GTK module directory,
# and the theme paths through pkg-config, so the -dev packages have to be here
# even though nothing is compiled.
apt-get install -y -qq --no-install-recommends \
  libgtk-4-dev libadwaita-1-dev libgdk-pixbuf-2.0-dev librsvg2-dev \
  >/dev/null

BUILD_DIR="/tmp/appimage-build"
APPDIR="$BUILD_DIR/AppDir"
TOOLS="$BUILD_DIR/tools"
rm -rf "$BUILD_DIR"
mkdir -p "$APPDIR" "$TOOLS"

# --------------------------------------------------------------------------- #
# AppImage tooling
# --------------------------------------------------------------------------- #

log "Fetching linuxdeploy and appimagetool"
base="https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous"
gtk_base="https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master"
appimage_base="https://github.com/AppImage/appimagetool/releases/download/continuous"

wget -q -O "$TOOLS/linuxdeploy" "$base/linuxdeploy-$ARCH.AppImage"
wget -q -O "$TOOLS/linuxdeploy-plugin-gtk" "$gtk_base/linuxdeploy-plugin-gtk.sh"
wget -q -O "$TOOLS/appimagetool" "$appimage_base/appimagetool-$ARCH.AppImage"
chmod +x "$TOOLS/linuxdeploy" "$TOOLS/linuxdeploy-plugin-gtk" "$TOOLS/appimagetool"

# There is no FUSE inside a container or on a CI runner.
export APPIMAGE_EXTRACT_AND_RUN=1
export PATH="$TOOLS:$PATH"

# --------------------------------------------------------------------------- #
# Python runtime and TuxFlow itself
# --------------------------------------------------------------------------- #

site="$APPDIR/usr/lib/python$PYTHON_VERSION/site-packages"
mkdir -p "$APPDIR/usr/bin" "$site"

log "Copying the Python runtime"
cp "/usr/bin/python$PYTHON_VERSION" "$APPDIR/usr/bin/"
ln -sf "python$PYTHON_VERSION" "$APPDIR/usr/bin/python3"
# A second copy under the app's own name. The desktop file says Exec=tuxflow,
# and linuxdeploy refuses to finish unless it can point that at a real binary;
# running it is also what gives the process a recognisable name in ps and in
# the task switcher.
cp "/usr/bin/python$PYTHON_VERSION" "$APPDIR/usr/bin/tuxflow"
# The standard library, minus the parts no shipped app needs. The config-*
# directory has to go before linuxdeploy runs: it holds a static python.o that
# is not dynamically linked, and patchelf fails outright when it reaches it.
cp -a "/usr/lib/python$PYTHON_VERSION" "$APPDIR/usr/lib/"
stdlib="$APPDIR/usr/lib/python$PYTHON_VERSION"
rm -rf \
  "$stdlib/test" \
  "$stdlib/idlelib" \
  "$stdlib/tkinter" \
  "$stdlib/turtledemo" \
  "$stdlib/config-"*
find "$stdlib" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$stdlib" -name "*.a" -delete 2>/dev/null || true

log "Copying PyGObject"
# PyGObject comes from apt rather than pip: the apt build already matches the
# GTK and girepository this image ships, and pip would compile its own.
cp -a /usr/lib/python3/dist-packages/gi "$site/"
for extra in cairo _cairo*.so; do
  cp -a "/usr/lib/python3/dist-packages/$extra" "$site/" 2>/dev/null || true
done

# --------------------------------------------------------------------------- #
# Desktop metadata
# --------------------------------------------------------------------------- #

log "Writing desktop metadata"
icons="$APPDIR/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$APPDIR/usr/share/applications" "$icons"
sed "s|@TUXFLOW_BIN@|tuxflow|g" \
  /src/data/io.github.tuxflow.TuxFlow.desktop.in \
  >"$APPDIR/usr/share/applications/io.github.tuxflow.TuxFlow.desktop"
cp /src/data/io.github.tuxflow.TuxFlow.svg "$icons/"
cp "$icons/io.github.tuxflow.TuxFlow.svg" "$APPDIR/"
cp "$APPDIR/usr/share/applications/io.github.tuxflow.TuxFlow.desktop" "$APPDIR/"

# --------------------------------------------------------------------------- #
# Bundle the libraries
# --------------------------------------------------------------------------- #

log "Bundling GTK and its dependencies"
# The compiled extensions carry the library dependencies that matter; pointing
# linuxdeploy at them is what pulls libgtk-4, libadwaita, and friends in.
deploy_args=()
for library in "$site"/gi/_gi*.so "$APPDIR/usr/bin/python$PYTHON_VERSION" \
  "$APPDIR/usr/bin/tuxflow"; do
  [[ -e "$library" ]] && deploy_args+=("--executable" "$library")
done

# libadwaita is reached only through its typelib, so no binary in the bundle
# names it and linuxdeploy would never find it on its own. Left out, the import
# silently picks up the host's copy, which is linked against the host's GTK --
# two GTKs in one process, and every GType comes back void.
for adwaita in /usr/lib/*/libadwaita-1.so.0; do
  [[ -e "$adwaita" ]] && deploy_args+=("--library" "$adwaita")
done

DEPLOY_GTK_VERSION=4 "$TOOLS/linuxdeploy" \
  --appdir "$APPDIR" \
  "${deploy_args[@]}" \
  --desktop-file "$APPDIR/usr/share/applications/io.github.tuxflow.TuxFlow.desktop" \
  --icon-file "$icons/io.github.tuxflow.TuxFlow.svg" \
  --plugin gtk

# --------------------------------------------------------------------------- #
# TuxFlow and its Python dependencies
# --------------------------------------------------------------------------- #

# Deliberately after linuxdeploy. Manylinux wheels ship their own libraries in
# sibling .libs directories under mangled names, reachable through an $ORIGIN
# rpath; linuxdeploy resolves those names against the system and fails. Nothing
# in these wheels needs deploying, so they are installed once linuxdeploy is
# finished walking the bundle.
log "Installing TuxFlow and faster-whisper"
"/usr/bin/python$PYTHON_VERSION" -m pip install \
  --quiet --break-system-packages \
  --target "$site" \
  --upgrade \
  "/src[speech]"

# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

log "Writing AppRun"
cat >"$APPDIR/AppRun" <<'APPRUN'
#!/bin/bash
# Resolve the bundle root whether it was mounted or extracted.
SELF="$(readlink -f "$0")"
HERE="${APPDIR:-$(dirname "$SELF")}"
export APPDIR="$HERE"

PYTHON_VERSION="@PYTHON_VERSION@"

# Run the bundled interpreter against the bundled standard library.
export PYTHONHOME="$HERE/usr"
export PYTHONPATH="$HERE/usr/lib/python$PYTHON_VERSION/site-packages"
export PYTHONDONTWRITEBYTECODE=1
export LD_LIBRARY_PATH="$HERE/usr/lib:$HERE/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# The GTK plugin drops its environment into apprun-hooks: typelibs, pixbuf
# loaders, GSettings schemas, and the icon theme all need pointing at the
# bundle.
requested_backend="${GDK_BACKEND:-}"
if [ -d "$HERE/apprun-hooks" ]; then
  for hook in "$HERE"/apprun-hooks/*.sh; do
    [ -r "$hook" ] && . "$hook"
  done
fi

# That hook pins GDK_BACKEND=x11, which is a GTK 3-era workaround: GTK 4 runs
# natively on Wayland, and going through XWayland instead costs sharpness on a
# scaled display. X11 stays in the list as the fallback, and anything the user
# asked for wins outright.
export GDK_BACKEND="${requested_backend:-wayland,x11}"

exec "$HERE/usr/bin/tuxflow" -m tuxflow "$@"
APPRUN
sed -i "s|@PYTHON_VERSION@|$PYTHON_VERSION|g" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

# --------------------------------------------------------------------------- #
# Pack
# --------------------------------------------------------------------------- #

log "Packing the AppImage"
mkdir -p /src/dist
version="$("/usr/bin/python$PYTHON_VERSION" -c \
  "import re,pathlib;print(re.search(r'version = \"([^\"]+)\"',pathlib.Path('/src/pyproject.toml').read_text()).group(1))")"
output="/src/dist/TuxFlow-$version-$ARCH.AppImage"

ARCH="$ARCH" "$TOOLS/appimagetool" "$APPDIR" "$output"
chmod +x "$output"

log "Built $output"
ls -lh "$output"
