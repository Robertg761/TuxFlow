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

    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
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


def test_macos_copies_with_pbcopy_and_pastes_through_system_events(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    commands: list[list[str]] = []

    monkeypatch.setattr(insertion.shutil, "which", lambda name: Path("/usr/bin") / name)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        insertion,
        "_run",
        lambda command, input_text=None: commands.append(command) or True,
    )

    result = insertion.insert_text("hello there", auto_paste=True)

    assert result.copied and result.pasted
    assert commands[0] == ["pbcopy"]
    assert commands[1][0] == "osascript"
    assert "command down" in commands[1][-1]
    assert insertion.clipboard_tool() == "pbcopy"


def test_macos_without_accessibility_does_not_claim_it_pasted(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    monkeypatch.setattr(insertion.shutil, "which", lambda name: Path("/usr/bin") / name)
    monkeypatch.setattr(insertion.time, "sleep", lambda _seconds: None)
    # osascript exits non-zero when System Events is not allowed to type.
    monkeypatch.setattr(
        insertion,
        "_run",
        lambda command, input_text=None: command[0] == "pbcopy",
    )
    monkeypatch.setattr(insertion, "macos_accessibility_trusted", lambda: False)

    result = insertion.insert_text("hello there", auto_paste=True)

    assert result.copied
    assert not result.pasted
    assert result.detail == "Copied, but automatic paste is unavailable"
    assert not insertion.can_paste_automatically()
