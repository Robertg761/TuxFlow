"""XDG paths used by TuxFlow."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(env_name: str, fallback: str) -> Path:
    return Path(os.environ.get(env_name, Path.home() / fallback)).expanduser()


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "tuxflow"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "tuxflow"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "tuxflow"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    return Path(base) / "tuxflow" if base else cache_dir() / "runtime"


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


def ensure_directories() -> None:
    for path in (
        config_dir(),
        data_dir(),
        cache_dir(),
        runtime_dir(),
        recordings_dir(),
        models_dir(),
    ):
        path.mkdir(parents=True, exist_ok=True)
