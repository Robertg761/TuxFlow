from __future__ import annotations

from pathlib import Path

import pytest

from tuxflow import audio


def test_each_platform_prefers_its_native_recorder(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    assert audio.supported_backends()[0] is audio.PIPEWIRE

    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    assert audio.supported_backends()[0] is audio.FFMPEG_AVFOUNDATION


def test_second_choice_recorder_is_used_when_the_first_is_missing(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    installed = {"arecord"}
    monkeypatch.setattr(audio.shutil, "which", lambda name: name if name in installed else None)

    assert audio.select_backend() is audio.ALSA

    installed.clear()
    assert audio.select_backend() is None
    assert "pw-record" in audio.missing_recorder_message()


def test_missing_recorder_reports_the_install_command_for_the_platform(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    monkeypatch.setattr(audio.shutil, "which", lambda _name: None)

    message = audio.missing_recorder_message()
    assert "brew install ffmpeg" in message

    with pytest.raises(audio.RecordingError, match="No microphone recorder"):
        audio.create_recorder().start(Path("/tmp/tuxflow-test.wav"))


def test_avfoundation_retries_the_numeric_device_when_the_default_is_rejected(tmp_path):
    recorder = audio.CommandRecorder(audio.FFMPEG_AVFOUNDATION)
    attempts: list[str] = []

    def fake_spawn(_backend, _path, device):
        attempts.append(device)
        return "" if device == ":0" else "Input/output error"

    recorder._spawn = fake_spawn  # type: ignore[method-assign]
    recorder.start(tmp_path / "clip.wav")

    assert attempts == ["", ":0"]


def test_a_pinned_device_is_never_silently_replaced(tmp_path):
    recorder = audio.CommandRecorder(audio.FFMPEG_AVFOUNDATION, device=":2")
    attempts: list[str] = []

    def fake_spawn(_backend, _path, device):
        attempts.append(device)
        return "Input/output error"

    recorder._spawn = fake_spawn  # type: ignore[method-assign]
    with pytest.raises(audio.RecordingError, match="Input/output error"):
        recorder.start(tmp_path / "clip.wav")

    assert attempts == [":2"]


def test_every_backend_records_the_format_whisper_expects(tmp_path):
    path = tmp_path / "clip.wav"
    for backend in (audio.PIPEWIRE, audio.ALSA, audio.FFMPEG_PULSE, audio.FFMPEG_AVFOUNDATION):
        arguments = backend.arguments(path, "")
        assert audio.SAMPLE_RATE in arguments, backend.name
        assert audio.CHANNELS in arguments, backend.name
        assert str(path) in arguments, backend.name


def test_sox_takes_its_device_from_the_environment(tmp_path):
    recorder = audio.CommandRecorder(audio.SOX, device="MacBook Pro Microphone")
    environment = recorder._environment(audio.SOX)

    assert environment is not None
    assert environment["AUDIODEV"] == "MacBook Pro Microphone"
    # sox has no device flag, so the name must not leak into the command line.
    assert "MacBook Pro Microphone" not in audio.SOX.arguments(tmp_path / "clip.wav", "ignored")
    assert audio.CommandRecorder(audio.SOX)._environment(audio.SOX) is None
