from __future__ import annotations

import os
from pathlib import Path

import pytest

from tuxflow import paths


@pytest.fixture
def permissive_umask():
    """Prove the mode comes from the code and not from a strict umask."""
    previous = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def private_home(tmp_path, monkeypatch):
    for name, folder in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_RUNTIME_DIR", "runtime"),
    ):
        monkeypatch.setenv(name, str(tmp_path / folder))
    return tmp_path


def _mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_every_directory_is_private_to_its_owner(private_home, permissive_umask):
    paths.ensure_directories()

    for directory in (
        paths.config_dir(),
        paths.data_dir(),
        paths.cache_dir(),
        paths.runtime_dir(),
        paths.recordings_dir(),
        paths.models_dir(),
    ):
        assert _mode(directory) == 0o700, directory


def test_a_world_readable_directory_from_an_older_install_is_healed(private_home, permissive_umask):
    recordings = paths.recordings_dir()
    recordings.mkdir(parents=True)
    recordings.chmod(0o755)

    paths.ensure_directories()

    assert _mode(recordings) == 0o700


@pytest.fixture
def plain_home(tmp_path, monkeypatch):
    """A machine where nothing XDG is exported at all."""
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_without_any_xdg_variables_everything_lands_under_the_home_directory(plain_home):
    assert paths.config_file() == plain_home / ".config/tuxflow/config.json"
    assert paths.database_file() == plain_home / ".local/share/tuxflow/history.sqlite3"
    assert paths.recordings_dir() == plain_home / ".cache/tuxflow/recordings"
    assert paths.models_dir() == plain_home / ".local/share/tuxflow/models"
    # No runtime directory on this login, so the socket goes with the cache.
    assert paths.socket_file() == plain_home / ".cache/tuxflow/runtime/tuxflow.sock"


def test_an_exported_but_empty_variable_counts_as_unset(plain_home, monkeypatch):
    # Path("") is the current working directory, which is the last place a
    # user's transcripts should end up.
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "   ")

    assert paths.data_dir() == plain_home / ".local/share/tuxflow"
    assert paths.runtime_dir() == plain_home / ".cache/tuxflow/runtime"


def test_a_variable_written_with_a_tilde_is_expanded(plain_home, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "~/scratch")

    assert paths.cache_dir() == plain_home / "scratch/tuxflow"


def test_an_exported_runtime_directory_is_where_the_socket_goes(plain_home, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(plain_home / "run"))

    assert paths.socket_file() == plain_home / "run/tuxflow/tuxflow.sock"


def test_a_directory_that_cannot_be_tightened_does_not_stop_tuxflow(private_home, monkeypatch):
    def refuse(_self, _mode):
        raise PermissionError("not the owner")

    monkeypatch.setattr(Path, "chmod", refuse)

    # A directory owned by someone else is not TuxFlow's to fix, and refusing
    # to start over it would be worse than leaving it alone.
    paths.ensure_directories()

    assert paths.config_dir().is_dir()
