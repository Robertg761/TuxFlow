"""Tests for the daemon's recording lifecycle.

Everything the daemon talks to — recorder, tray, notifier, Whisper — is
replaced, so these exercise the state machine rather than the machine's audio
stack.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tuxflow import daemon as daemon_module
from tuxflow.audio import Recording, RecordingError


class FakeRecorder:
    def __init__(self) -> None:
        self.device = ""
        self.is_recording = False
        self.started: list[Path] = []
        self.cancelled = 0
        self.stop_error: str = ""

    def start(self, path: Path) -> None:
        self.started.append(path)
        self.is_recording = True

    def stop(self) -> Recording:
        self.is_recording = False
        if self.stop_error:
            raise RecordingError(self.stop_error)
        return Recording(path=self.started[-1], duration_seconds=1.0)

    def cancel(self) -> Path | None:
        self.cancelled += 1
        self.is_recording = False
        return self.started[-1] if self.started else None


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    monkeypatch.setenv("TUXFLOW_DISABLE_SHORTCUT", "1")

    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        daemon_module,
        "notify",
        lambda title, body="", **_kwargs: notifications.append((title, body)),
    )

    instance = daemon_module.TuxFlowDaemon()
    instance.recorder = FakeRecorder()
    monkeypatch.setattr(instance.tray, "update", lambda *_args, **_kwargs: None)
    instance.notifications = notifications
    return instance


def test_a_held_key_that_is_never_released_stops_by_itself(daemon, monkeypatch):
    monkeypatch.setattr(daemon_module, "MAX_RECORDING_SECONDS", 0.05)
    transcribed: list[Path] = []

    async def fake_transcribe(path, _duration):
        transcribed.append(path)

    monkeypatch.setattr(daemon, "_transcribe", fake_transcribe)

    async def scenario() -> None:
        await daemon.start_recording()
        assert daemon.state == "recording"
        await asyncio.sleep(0.3)

    asyncio.run(scenario())

    assert daemon.state != "recording"
    assert transcribed == daemon.recorder.started
    assert any("Dictation stopped" in title for title, _ in daemon.notifications)


def test_a_normal_release_does_not_leave_the_watchdog_running(daemon, monkeypatch):
    monkeypatch.setattr(daemon_module, "MAX_RECORDING_SECONDS", 0.05)

    async def fake_transcribe(_path, _duration):
        return None

    monkeypatch.setattr(daemon, "_transcribe", fake_transcribe)

    async def scenario() -> None:
        await daemon.start_recording()
        await daemon.stop_recording()
        assert daemon._recording_watchdog is None
        # Long enough that a surviving watchdog would have fired by now.
        await asyncio.sleep(0.3)

    asyncio.run(scenario())

    assert daemon.notifications == []
    assert daemon.state == "idle"


def test_cancelling_stops_the_watchdog_too(daemon, monkeypatch):
    monkeypatch.setattr(daemon_module, "MAX_RECORDING_SECONDS", 0.05)

    async def scenario() -> None:
        await daemon.start_recording()
        await daemon.cancel_recording()
        await asyncio.sleep(0.3)

    asyncio.run(scenario())

    assert daemon.recorder.cancelled == 1
    assert daemon.state == "idle"
    assert daemon.notifications == []


def test_a_recorder_that_fails_mid_sentence_is_reported_and_resets(daemon):
    daemon.recorder.stop_error = "the microphone was disconnected"

    async def scenario() -> None:
        await daemon.start_recording()
        await daemon.stop_recording()

    asyncio.run(scenario())

    assert daemon.state == "idle"
    assert daemon.last_error == "the microphone was disconnected"
    assert daemon.notifications == [("Recording failed", "the microphone was disconnected")]
    # A failed dictation must not block the next one.
    asyncio.run(daemon.start_recording())
    assert daemon.state == "recording"
