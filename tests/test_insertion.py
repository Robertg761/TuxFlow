from __future__ import annotations

from pathlib import Path

from tuxflow import insertion


def test_new_ydotool_device_settles_before_first_input(monkeypatch, tmp_path):
    socket = tmp_path / "ydotool.sock"
    sleep_calls: list[float] = []
    listening_results = iter([False, True])

    class FakeDaemon:
        def poll(self):
            return None

    monkeypatch.setattr(insertion, "_ydotool_socket", lambda: socket)
    monkeypatch.setattr(insertion, "_socket_listening", lambda _path: next(listening_results))
    monkeypatch.setattr(insertion.shutil, "which", lambda name: Path("/usr/bin") / name)
    monkeypatch.setattr(insertion.os, "access", lambda _path, _mode: True)
    monkeypatch.setattr(insertion.subprocess, "Popen", lambda *_args, **_kwargs: FakeDaemon())
    monkeypatch.setattr(insertion.time, "sleep", sleep_calls.append)
    monkeypatch.setattr(insertion, "_ydotool_daemon", None)
    monkeypatch.setattr(insertion, "_ydotool_owned_socket", None)

    assert insertion.prepare_input_backend()
    assert sleep_calls[-1] == insertion.YDOTOOL_DEVICE_SETTLE_SECONDS
