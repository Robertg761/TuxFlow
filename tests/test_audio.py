from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from tuxflow import audio


def _scripted_backend(script: str) -> audio.RecorderBackend:
    """A backend that runs a Python snippet instead of a real recorder.

    ``{path}`` in the snippet is replaced with the WAV path the recorder was
    given, so a test can decide exactly how the process misbehaves.
    """
    return audio.RecorderBackend(
        name="Scripted",
        executable=sys.executable,
        install_hint="",
        arguments=lambda path, _device: ["-c", script.format(path=repr(str(path)))],
    )


def _wait_for_exit(recorder: audio.CommandRecorder, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while recorder.is_recording and time.monotonic() < deadline:
        time.sleep(0.02)


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


def test_a_recorder_that_dies_mid_sentence_reports_why(tmp_path):
    # An unplugged microphone: the process writes a partial file, then quits.
    recorder = audio.CommandRecorder(
        _scripted_backend(
            "import sys, time; open({path}, 'wb').write(b'\\0' * 100); time.sleep(0.2);"
            " sys.stderr.write('warming up\\n'); sys.stderr.write('device disconnected\\n');"
            " sys.exit(1)"
        )
    )
    path = tmp_path / "clip.wav"
    recorder.start(path)
    _wait_for_exit(recorder)

    with pytest.raises(audio.RecordingError, match="device disconnected"):
        recorder.stop()
    # The half-written file would otherwise sit in the cache directory forever.
    assert not path.exists()


def test_an_empty_recording_does_not_leave_a_file_behind(tmp_path):
    recorder = audio.CommandRecorder(
        # Like a real recorder, it finalises on SIGINT — with nothing in it.
        _scripted_backend(
            "import signal, sys, time;"
            " signal.signal(signal.SIGINT, lambda *a: sys.exit(0));"
            " open({path}, 'wb'); time.sleep(30)"
        )
    )
    path = tmp_path / "clip.wav"
    recorder.start(path)

    with pytest.raises(audio.RecordingError, match="empty"):
        recorder.stop()
    assert not path.exists()


def test_a_chatty_recorder_keeps_recording(tmp_path):
    # FFmpeg can print far more than a pipe buffer holds. Collecting stderr in a
    # pipe would block the recorder here before it ever wrote the WAV.
    recorder = audio.CommandRecorder(
        _scripted_backend(
            "import sys, time; sys.stderr.write('noise\\n' * 40000); sys.stderr.flush();"
            " open({path}, 'wb').write(b'\\0' * 128); time.sleep(30)"
        )
    )
    recording = tmp_path / "clip.wav"
    recorder.start(recording)
    time.sleep(0.5)

    result = recorder.stop()
    assert result.path == recording
    assert result.duration_seconds > 0


def test_only_the_last_line_of_a_recorder_complaint_is_shown():
    assert audio._last_line("ffmpeg version 7.1\n\nInput/output error\n") == "Input/output error"
    assert audio._last_line("   \n") == ""


def test_sox_takes_its_device_from_the_environment(tmp_path):
    recorder = audio.CommandRecorder(audio.SOX, device="MacBook Pro Microphone")
    environment = recorder._environment(audio.SOX)

    assert environment is not None
    assert environment["AUDIODEV"] == "MacBook Pro Microphone"
    # sox has no device flag, so the name must not leak into the command line.
    assert "MacBook Pro Microphone" not in audio.SOX.arguments(tmp_path / "clip.wav", "ignored")
    assert audio.CommandRecorder(audio.SOX)._environment(audio.SOX) is None
