"""Tests for the daemon's recording lifecycle.

Everything the daemon talks to — recorder, tray, notifier, Whisper — is
replaced, so these exercise the state machine rather than the machine's audio
stack.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from pathlib import Path

import pytest

from tuxflow import daemon as daemon_module
from tuxflow.audio import Recording, RecordingError
from tuxflow.engine import EngineUnavailableError, Transcript
from tuxflow.insertion import InsertResult
from tuxflow.ipc import send_command
from tuxflow.paths import models_dir, socket_file
from tuxflow.shortcuts import MANUAL_FALLBACK, ShortcutUnavailableError


class FakeRecorder:
    def __init__(self) -> None:
        self.device = ""
        self.is_recording = False
        self.started: list[Path] = []
        self.cancelled = 0
        self.start_error: str = ""
        self.stop_error: str = ""
        # A real recorder opens the device and waits for it to settle, so
        # `start` blocks for around a tenth of a second.
        self.start_delay = 0.0

    def start(self, path: Path) -> None:
        if self.start_delay:
            time.sleep(self.start_delay)
        if self.start_error:
            raise RecordingError(self.start_error)
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


def test_a_key_released_while_the_microphone_opens_still_stops_the_recording(daemon, monkeypatch):
    # A quick tap: the key is back up before the recorder has finished opening
    # the device. The stop used to be dropped and the microphone stayed live
    # until the watchdog fired ten minutes later.
    daemon.recorder.start_delay = 0.2
    transcribed: list[Path] = []

    async def fake_transcribe(path, _duration):
        transcribed.append(path)

    monkeypatch.setattr(daemon, "_transcribe", fake_transcribe)

    async def scenario() -> None:
        press = asyncio.create_task(daemon._on_shortcut_pressed("shortcut"))
        await asyncio.sleep(0.05)
        assert daemon.state == "starting"
        # Clients must not be told about the in-between step.
        assert daemon.status()["state"] == "recording"
        await daemon._on_shortcut_released("shortcut")
        await press
        await daemon._transcription_task

    asyncio.run(scenario())

    assert daemon.recorder.is_recording is False
    assert daemon.state == "idle"
    assert daemon._recording_watchdog is None
    assert transcribed == daemon.recorder.started


def test_a_stop_command_during_a_slow_start_waits_for_it(daemon, monkeypatch):
    daemon.recorder.start_delay = 0.2

    async def fake_transcribe(_path, _duration):
        return None

    monkeypatch.setattr(daemon, "_transcribe", fake_transcribe)

    async def scenario() -> None:
        start = asyncio.create_task(daemon.handle_command("start"))
        await asyncio.sleep(0.05)
        await daemon.handle_command("stop")
        await start
        await daemon._transcription_task

    asyncio.run(scenario())

    assert daemon.recorder.is_recording is False
    assert daemon.state == "idle"
    assert daemon.notifications == []


class FakeEngine:
    def __init__(self, text: str = "hello there", failure: Exception | None = None) -> None:
        self.text = text
        self.failure = failure

    def transcribe(self, _path: Path, _language: str) -> Transcript:
        if self.failure is not None:
            raise self.failure
        return Transcript(text=self.text, language="en", language_probability=0.98)


def _audio_file(daemon) -> Path:
    audio = Path(daemon.history.path).parent / "voice.wav"
    audio.write_bytes(b"RIFF")
    return audio


def _prepare_transcription(daemon, monkeypatch, result: InsertResult) -> Path:
    monkeypatch.setattr(daemon, "_get_engine", lambda _settings: FakeEngine())
    monkeypatch.setattr(daemon_module, "insert_text", lambda _text, **_kwargs: result)
    return _audio_file(daemon)


def test_a_transcript_with_nowhere_to_go_is_reported_and_still_saved(daemon, monkeypatch):
    audio = _prepare_transcription(
        daemon, monkeypatch, InsertResult(False, False, "No supported clipboard tool was found")
    )

    asyncio.run(daemon._transcribe(audio, 2.5))

    assert daemon.last_error == "No supported clipboard tool was found"
    assert daemon.notifications == [
        ("Could not insert the transcript", "No supported clipboard tool was found")
    ]
    saved = daemon.history.recent()
    assert [item.raw_text for item in saved] == ["hello there"]


def test_a_transcript_that_could_not_be_pasted_is_reported(daemon, monkeypatch):
    detail = "Copied, but automatic paste is unavailable"
    audio = _prepare_transcription(daemon, monkeypatch, InsertResult(True, False, detail))

    asyncio.run(daemon._transcribe(audio, 2.5))

    assert daemon.last_error == detail
    assert daemon.notifications == [("Could not paste the transcript", detail)]
    assert len(daemon.history.recent()) == 1


def test_a_pasted_transcript_says_nothing(daemon, monkeypatch):
    audio = _prepare_transcription(
        daemon, monkeypatch, InsertResult(True, True, "Pasted into the active app")
    )

    asyncio.run(daemon._transcribe(audio, 2.5))

    assert daemon.last_error == ""
    assert daemon.notifications == []
    assert len(daemon.history.recent()) == 1


def test_a_bare_press_enter_command_is_not_a_failed_paste(daemon):
    settings = daemon.config_store.load()
    assert settings.auto_paste

    # Nothing was dictated except the command itself, so there is no text to
    # paste and `pasted` is legitimately False.
    daemon._report_insertion(InsertResult(True, False, "Copied to the clipboard"), "", settings)

    assert daemon.last_error == ""
    assert daemon.notifications == []


def test_a_microphone_that_will_not_open_leaves_the_daemon_idle(daemon):
    daemon.recorder.start_error = "PipeWire is not running"

    asyncio.run(daemon.start_recording())

    assert daemon.state == "idle"
    assert daemon.recorder.is_recording is False
    assert daemon.last_error == "PipeWire is not running"
    assert daemon.notifications == [("Could not start dictation", "PipeWire is not running")]
    # A start that never happened must not leave a watchdog behind.
    assert daemon._recording_watchdog is None


def test_pressing_the_shortcut_twice_does_not_start_a_second_recording(daemon):
    async def scenario() -> None:
        await daemon.start_recording()
        await daemon.start_recording()

    asyncio.run(scenario())

    assert len(daemon.recorder.started) == 1
    assert daemon.state == "recording"


def test_toggle_starts_a_recording_and_toggle_again_ends_it(daemon, monkeypatch):
    async def fake_transcribe(_path, _duration):
        return None

    monkeypatch.setattr(daemon, "_transcribe", fake_transcribe)

    async def scenario() -> None:
        await daemon.toggle()
        assert daemon.state == "recording"
        await daemon.toggle()
        assert daemon.state == "processing"
        await daemon._transcription_task
        # Nothing is recording, so a third toggle starts a fresh dictation.
        await daemon.toggle()

    asyncio.run(scenario())

    assert len(daemon.recorder.started) == 2


def test_the_engine_is_reused_until_the_model_settings_change(daemon):
    settings = daemon.config_store.load()

    first = daemon._get_engine(settings)

    assert daemon._get_engine(settings) is first
    # A freshly loaded copy of the same settings is still the same engine.
    assert daemon._get_engine(daemon.config_store.load()) is first
    assert first.download_root == models_dir()

    settings.model = "medium"
    second = daemon._get_engine(settings)
    assert second is not first
    assert second.model_name == "medium"

    settings.compute_type = "float16"
    third = daemon._get_engine(settings)
    assert third is not second
    assert third.compute_type == "float16"


def test_the_recording_is_deleted_once_it_has_been_transcribed(daemon, monkeypatch):
    audio = _prepare_transcription(daemon, monkeypatch, InsertResult(True, True, "Pasted"))

    asyncio.run(daemon._transcribe(audio, 1.0))

    assert not audio.exists()


def test_keeping_the_audio_leaves_the_recording_on_disk(daemon, monkeypatch):
    settings = daemon.config_store.load()
    settings.keep_audio = True
    daemon.config_store.save(settings)
    audio = _prepare_transcription(daemon, monkeypatch, InsertResult(True, True, "Pasted"))

    asyncio.run(daemon._transcribe(audio, 1.0))

    assert audio.exists()


def test_history_keeps_both_what_was_said_and_what_was_typed(daemon, monkeypatch):
    monkeypatch.setattr(
        daemon, "_get_engine", lambda _settings: FakeEngine("um hello there comma world period")
    )
    pasted = InsertResult(True, True, "Pasted into the active app")
    monkeypatch.setattr(daemon_module, "insert_text", lambda _text, **_kwargs: pasted)
    audio = _audio_file(daemon)

    asyncio.run(daemon._transcribe(audio, 4.25))

    entry = daemon.history.recent()[0]
    assert entry.text == "Hello there, world."
    assert entry.raw_text == "um hello there comma world period"
    assert entry.language == "en"
    assert entry.duration_seconds == 4.25
    assert entry.model == daemon.config_store.load().model


def test_a_missing_speech_engine_is_reported_and_the_audio_still_goes_away(daemon, monkeypatch):
    monkeypatch.setattr(
        daemon,
        "_get_engine",
        lambda _settings: FakeEngine(failure=EngineUnavailableError("Run: ./scripts/install.sh")),
    )
    audio = _audio_file(daemon)

    asyncio.run(daemon._transcribe(audio, 1.0))

    assert daemon.last_error == "Run: ./scripts/install.sh"
    assert daemon.notifications == [("Transcription failed", "Run: ./scripts/install.sh")]
    assert daemon.history.recent() == []
    assert not audio.exists()


def test_an_unexpected_failure_is_reported_rather_than_swallowed(daemon, monkeypatch):
    monkeypatch.setattr(
        daemon, "_get_engine", lambda _settings: FakeEngine(failure=MemoryError("out of memory"))
    )
    audio = _audio_file(daemon)

    asyncio.run(daemon._transcribe(audio, 1.0))

    assert daemon.last_error == "Unexpected transcription error: out of memory"
    assert daemon.notifications == [("Transcription failed", daemon.last_error)]
    assert not audio.exists()


def test_a_silent_recording_is_neither_pasted_nor_saved(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_get_engine", lambda _settings: FakeEngine(""))

    def refuse_to_insert(*_args, **_kwargs):
        raise AssertionError("nothing was dictated, so nothing may be pasted")

    monkeypatch.setattr(daemon_module, "insert_text", refuse_to_insert)
    audio = _audio_file(daemon)

    asyncio.run(daemon._transcribe(audio, 0.4))

    assert daemon.history.recent() == []
    assert daemon.last_error == ""
    assert daemon.notifications == []
    assert not audio.exists()


def test_a_finished_transcription_returns_the_tray_to_idle(daemon, monkeypatch):
    states: list[tuple] = []
    monkeypatch.setattr(daemon.tray, "update", lambda *args, **_kwargs: states.append(args))
    audio = _prepare_transcription(daemon, monkeypatch, InsertResult(True, True, "Pasted"))

    asyncio.run(daemon._finish_transcription(audio, 1.0))

    assert daemon.state == "idle"
    assert states[-1] == ("idle", "")


async def _wait_until(condition, message: str, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError(message)
        await asyncio.sleep(0.01)


@pytest.fixture
def headless_daemon(daemon, monkeypatch):
    """The daemon with no tray, no input backend, and no global shortcut."""
    monkeypatch.setattr(daemon_module, "prepare_input_backend", lambda: None)
    monkeypatch.setattr(daemon_module, "shutdown_input_backend", lambda: None)

    async def no_tray() -> bool:
        return False

    monkeypatch.setattr(daemon.tray, "start", no_tray)
    return daemon


def test_the_control_socket_answers_a_status_command(headless_daemon):
    async def scenario() -> dict:
        serving = asyncio.create_task(headless_daemon.run())
        await _wait_until(
            lambda: socket_file().exists(), "the daemon never opened its control socket"
        )
        try:
            return await send_command("status")
        finally:
            await headless_daemon.shutdown()
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serving

    response = asyncio.run(scenario())

    assert response["ok"] is True
    assert response["state"] == "idle"
    assert response["recording"] is False
    assert response["pid"] == os.getpid()
    # Shutdown takes the socket with it, so a stale file never fools `doctor`.
    assert not socket_file().exists()


def test_a_command_the_daemon_does_not_know_comes_back_as_an_error(headless_daemon):
    async def scenario() -> dict:
        serving = asyncio.create_task(headless_daemon.run())
        await _wait_until(
            lambda: socket_file().exists(), "the daemon never opened its control socket"
        )
        try:
            return await send_command("self-destruct")
        finally:
            await headless_daemon.shutdown()
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serving

    response = asyncio.run(scenario())

    # A bad command must be answered, not met with a dropped connection.
    assert response["ok"] is False
    assert "self-destruct" in response["error"]
    assert response["state"] == "idle"


def test_the_control_socket_is_private_to_its_owner(headless_daemon):
    async def scenario() -> int:
        serving = asyncio.create_task(headless_daemon.run())
        await _wait_until(
            lambda: socket_file().exists(), "the daemon never opened its control socket"
        )
        mode = socket_file().stat().st_mode & 0o777
        await headless_daemon.shutdown()
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        return mode

    # Anyone who can talk to this socket can start and stop the microphone.
    assert asyncio.run(scenario()) == 0o600


def test_a_desktop_that_refuses_the_shortcut_says_how_to_dictate_anyway(daemon, monkeypatch):
    monkeypatch.delenv("TUXFLOW_DISABLE_SHORTCUT")

    async def refuse() -> str:
        raise ShortcutUnavailableError("The desktop portal did not answer")

    monkeypatch.setattr(daemon.shortcuts, "connect", refuse)

    asyncio.run(daemon._setup_shortcut())

    assert daemon.shortcut == MANUAL_FALLBACK
    assert daemon.last_error == "The desktop portal did not answer"
    assert daemon.notifications == [
        ("TuxFlow shortcut needs attention", "The desktop portal did not answer")
    ]


def test_a_bound_shortcut_is_reported_without_a_notification(daemon, monkeypatch):
    monkeypatch.delenv("TUXFLOW_DISABLE_SHORTCUT")

    async def bind() -> str:
        return "Ctrl+Meta+Space"

    monkeypatch.setattr(daemon.shortcuts, "connect", bind)

    asyncio.run(daemon._setup_shortcut())

    assert daemon.shortcut == "Ctrl+Meta+Space"
    assert daemon.last_error == ""
    assert daemon.notifications == []


def test_disabling_the_shortcut_explains_the_manual_alternative(daemon):
    # TUXFLOW_DISABLE_SHORTCUT is how the tests and a headless install opt out.
    asyncio.run(daemon._setup_shortcut())

    assert "disabled" in daemon.shortcut
    assert MANUAL_FALLBACK.lower() in daemon.shortcut
    assert daemon.notifications == []


def test_a_toggle_command_reports_the_recording_it_started(daemon):
    status = asyncio.run(daemon.handle_command("toggle"))

    assert status["state"] == "recording"
    assert status["recording"] is True
    assert status["ok"] is True


def test_a_cancel_command_throws_the_recording_away(daemon):
    async def scenario() -> dict:
        await daemon.handle_command("start")
        return await daemon.handle_command("cancel")

    status = asyncio.run(scenario())

    assert status["state"] == "idle"
    assert daemon.recorder.cancelled == 1


def test_the_service_shuts_down_cleanly_when_the_system_asks_it_to(monkeypatch):
    events: list[str] = []
    running = asyncio.Event()

    class FakeDaemon:
        def __init__(self) -> None:
            events.append("created")

        async def run(self) -> None:
            running.set()
            await asyncio.Event().wait()

        async def shutdown(self) -> None:
            events.append("shutdown")

    monkeypatch.setattr(daemon_module, "TuxFlowDaemon", FakeDaemon)

    async def scenario() -> None:
        service = asyncio.create_task(daemon_module.run_daemon())
        # The signal handlers are installed before the daemon starts serving,
        # so by now SIGTERM belongs to TuxFlow rather than to the process.
        await asyncio.wait_for(running.wait(), timeout=3)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(service, timeout=3)

    asyncio.run(scenario())

    assert events == ["created", "shutdown"]
