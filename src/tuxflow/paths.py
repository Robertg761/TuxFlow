"""XDG-style paths used by TuxFlow.

macOS has no XDG specification, but the same layout is used there so that one
set of paths, one backup story, and one uninstall command cover both platforms.
The ``XDG_*`` environment variables are honoured wherever they are set.
"""

from __future__ import annotations

import os
from pathlib import Path


def _environment_path(env_name: str) -> Path | None:
    # An exported-but-empty variable is treated as unset; Path("") would
    # silently resolve to the current working directory.
    value = os.environ.get(env_name, "").strip()
    return Path(value).expanduser() if value else None


def _xdg(env_name: str, fallback: str) -> Path:
    return _environment_path(env_name) or (Path.home() / fallback)


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "tuxflow"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "tuxflow"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "tuxflow"


def runtime_dir() -> Path:
    base = _environment_path("XDG_RUNTIME_DIR")
    return base / "tuxflow" if base else cache_dir() / "runtime"


def config_file() -> Path:
    return config_dir() / "config.json"


def database_file() -> Path:
    return data_dir() / "history.sqlite3"


def socket_file() -> Path:
    return runtime_dir() / "tuxflow.sock"


def recordings_dir() -> Path:
    return cache_dir() / "recordings"


def models_dir() -> Path:
    return data_dir() / "models"


# Everything TuxFlow stores is private to one user: settings, transcripts, and
# any retained audio. On a shared machine the default 0755 would hand all of it
# to every other local account, so the directories are owner-only.
PRIVATE_DIR_MODE = 0o700


def ensure_directories() -> None:
    for path in (
        config_dir(),
        data_dir(),
        cache_dir(),
        runtime_dir(),
        recordings_dir(),
        models_dir(),
    ):
        path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        # mkdir only sets the mode when it creates the directory, and it is
        # further masked by the umask, so directories from older installs (and
        # from a permissive umask) are tightened explicitly.
        try:
            path.chmod(PRIVATE_DIR_MODE)
        except OSError:
            # A directory someone else owns is not ours to fix, and failing to
            # start over it would be worse than leaving it alone.
            pass
