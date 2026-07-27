from __future__ import annotations

import os

import pytest

from tuxflow.history import HistoryStore


@pytest.fixture
def permissive_umask():
    """Prove the mode comes from the code and not from a strict umask."""
    previous = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(previous)


def test_history_is_newest_first_and_clearable(tmp_path):
    history = HistoryStore(tmp_path / "history.sqlite3")
    history.add(
        text="First",
        raw_text="first",
        language="en",
        duration_seconds=1.0,
        model="tiny",
    )
    history.add(
        text="Second",
        raw_text="second",
        language="en",
        duration_seconds=2.0,
        model="base",
    )

    assert [item.text for item in history.recent()] == ["Second", "First"]
    history.clear()
    assert history.recent() == []


def _mode(path) -> int:
    return os.stat(path).st_mode & 0o777


def test_transcripts_are_not_readable_by_other_users(tmp_path, permissive_umask):
    path = tmp_path / "history.sqlite3"
    HistoryStore(path)

    assert _mode(path) == 0o600


def test_a_world_readable_database_from_an_older_install_is_healed(tmp_path, permissive_umask):
    path = tmp_path / "history.sqlite3"
    HistoryStore(path).add(
        text="Secret",
        raw_text="secret",
        language="en",
        duration_seconds=1.0,
        model="tiny",
    )
    path.chmod(0o644)

    reopened = HistoryStore(path)

    assert _mode(path) == 0o600
    assert [item.text for item in reopened.recent()] == ["Secret"]


def test_a_limit_returns_only_the_newest_entries(tmp_path):
    history = HistoryStore(tmp_path / "history.sqlite3")
    for index in range(5):
        history.add(
            text=f"Line {index}",
            raw_text=f"line {index}",
            language="en",
            duration_seconds=1.0,
            model="tiny",
        )

    assert [item.text for item in history.recent(limit=2)] == ["Line 4", "Line 3"]
    assert len(history.recent(limit=50)) == 5
