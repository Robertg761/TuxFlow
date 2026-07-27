"""Shared fixtures."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def short_sock_dir():
    """A temp directory whose path is short enough to hold an AF_UNIX socket.

    macOS caps socket paths at ~104 bytes, and pytest's tmp_path there lives
    under /private/var/folders/..., which overflows the limit. /tmp stays
    short on every platform TuxFlow supports.
    """
    path = Path(tempfile.mkdtemp(prefix="tuxflow-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)
