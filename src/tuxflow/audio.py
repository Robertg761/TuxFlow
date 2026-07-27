"""Microphone recording through whichever command-line recorder the machine has.

Every backend writes the same thing — 16 kHz mono signed 16-bit WAV, which is
what Whisper wants — and every backend is stopped with a signal it handles by
finalising the WAV header. That keeps :class:`CommandRecorder` identical across
PipeWire, ALSA, AVFoundation, and sox.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from tuxflow.system import LINUX, MACOS, current_os

SAMPLE_RATE = "16000"
CHANNELS = "1"


def _last_line(text: str) -> str:
    """Return the most useful line of a recorder's diagnostics.

    FFmpeg in particular prints a banner of context before the sentence that
    says what actually went wrong, and only the last line belongs in a
    notification.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _restrict(path: Path) -> None:
    """Keep a recording readable only by its owner.

    The WAV is created by the recorder subprocess under the daemon's umask —
    normally 0644 — so the mode can only be tightened once the file exists,
    not at open time.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class RecordingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Recording:
    path: Path
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class RecorderBackend:
    """A command-line recorder TuxFlow knows how to drive."""

    name: str
    executable: str
    install_hint: str
    arguments: Callable[[Path, str], list[str]]
    # sox picks its input device from the environment rather than from a flag.
    device_env_var: str | None = None
    # Tried when the default device is rejected and the user pinned no device.
    fallback_device: str = ""
    stop_signal: int = field(default=signal.SIGINT)

    def is_available(self) -> bool:
        return shutil.which(self.executable) is not None


def _pipewire_arguments(path: Path, device: str) -> list[str]:
    target = ["--target", device] if device else []
    return [
        "--format",
        "s16",
        "--rate",
        SAMPLE_RATE,
        "--channels",
        CHANNELS,
        *target,
        str(path),
    ]


def _alsa_arguments(path: Path, device: str) -> list[str]:
    target = ["-D", device] if device else []
    return [
        "-q",
        "-f",
        "S16_LE",
        "-r",
        SAMPLE_RATE,
        "-c",
        CHANNELS,
        *target,
        str(path),
    ]


def _ffmpeg_arguments(input_format: str, default_device: str) -> Callable[[Path, str], list[str]]:
    def build(path: Path, device: str) -> list[str]:
        return [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            input_format,
            "-i",
            device or default_device,
            "-ac",
            CHANNELS,
            "-ar",
            SAMPLE_RATE,
            "-sample_fmt",
            "s16",
            "-y",
            str(path),
        ]

    return build


def _sox_arguments(path: Path, _device: str) -> list[str]:
    return [
        "-q",
        "-d",
        "-c",
        CHANNELS,
        "-r",
        SAMPLE_RATE,
        "-b",
        "16",
        "-e",
        "signed-integer",
        str(path),
    ]


PIPEWIRE = RecorderBackend(
    name="PipeWire",
    executable="pw-record",
    install_hint="Install pipewire-utils (Fedora) or pipewire-bin (Debian/Ubuntu)",
    arguments=_pipewire_arguments,
)
ALSA = RecorderBackend(
    name="ALSA",
    executable="arecord",
    install_hint="Install alsa-utils",
    arguments=_alsa_arguments,
)
FFMPEG_PULSE = RecorderBackend(
    name="FFmpeg (PulseAudio)",
    executable="ffmpeg",
    install_hint="Install ffmpeg",
    arguments=_ffmpeg_arguments("pulse", "default"),
)
FFMPEG_AVFOUNDATION = RecorderBackend(
    name="FFmpeg (AVFoundation)",
    executable="ffmpeg",
    install_hint="Run: brew install ffmpeg",
    arguments=_ffmpeg_arguments("avfoundation", ":default"),
    # Older FFmpeg builds only accept the numeric AVFoundation device index.
    fallback_device=":0",
)
SOX = RecorderBackend(
    name="SoX",
    executable="sox",
    install_hint="Run: brew install sox",
    arguments=_sox_arguments,
    device_env_var="AUDIODEV",
)

BACKENDS: dict[str, tuple[RecorderBackend, ...]] = {
    LINUX: (PIPEWIRE, ALSA, FFMPEG_PULSE),
    MACOS: (FFMPEG_AVFOUNDATION, SOX),
}


def supported_backends() -> tuple[RecorderBackend, ...]:
    """Return the recorders TuxFlow can use here, in order of preference."""
    return BACKENDS.get(current_os(), ())


def select_backend() -> RecorderBackend | None:
    """Return the preferred recorder that is actually installed."""
    return next((backend for backend in supported_backends() if backend.is_available()), None)


def missing_recorder_message() -> str:
    candidates = supported_backends()
    if not candidates:
        return "TuxFlow has no microphone backend for this operating system"
    hints = " or ".join(f"{backend.executable} ({backend.install_hint})" for backend in candidates)
    return f"No microphone recorder was found. Install {hints}."


class CommandRecorder:
    """Record the microphone by supervising an external recorder process."""

    def __init__(self, backend: RecorderBackend | None = None, *, device: str = "") -> None:
        self.backend = backend
        self.device = device
        self._process: subprocess.Popen[bytes] | None = None
        self._path: Path | None = None
        self._started_at: float | None = None
        self._stderr: IO[bytes] | None = None

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _resolve_backend(self) -> RecorderBackend:
        backend = self.backend or select_backend()
        if backend is None:
            raise RecordingError(missing_recorder_message())
        executable = shutil.which(backend.executable)
        if not executable:
            raise RecordingError(f"{backend.executable} is missing. {backend.install_hint}")
        self.backend = backend
        return backend

    def _environment(self, backend: RecorderBackend) -> dict[str, str] | None:
        if backend.device_env_var and self.device:
            return {**os.environ, backend.device_env_var: self.device}
        return None

    def start(self, path: Path) -> None:
        if self.is_recording:
            raise RecordingError("A recording is already in progress")
        backend = self._resolve_backend()
        path.parent.mkdir(parents=True, exist_ok=True)
        devices = [self.device]
        if not self.device and backend.fallback_device:
            devices.append(backend.fallback_device)
        failure = ""
        for device in devices:
            failure = self._spawn(backend, path, device)
            if not failure:
                return
        # A recorder that failed may still have created the WAV before giving
        # up, and nothing will ever come back for it once start() has raised.
        path.unlink(missing_ok=True)
        raise RecordingError(failure)

    def _spawn(self, backend: RecorderBackend, path: Path, device: str) -> str:
        """Start the recorder once. Return an error message, or "" on success."""
        executable = shutil.which(backend.executable) or backend.executable
        self._path = path
        self._started_at = time.monotonic()
        # A pipe would deadlock the recorder once its 64 KiB buffer filled, and
        # nothing reads that buffer until the recording ends. A temporary file
        # has no such limit and is discarded with the recorder.
        # The handle deliberately outlives this call: it belongs to the
        # recorder for as long as the recorder runs, and _reset() closes it.
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115
        try:
            self._process = subprocess.Popen(
                [executable, *backend.arguments(path, device)],
                stdout=subprocess.DEVNULL,
                stderr=self._stderr,
                env=self._environment(backend),
            )
        except OSError as error:
            self._reset()
            return f"Could not start {backend.name} recording: {error}"
        time.sleep(0.08)
        if self._process.poll() is not None:
            stderr = self._read_stderr()
            self._reset()
            return stderr or f"{backend.name} recording exited immediately"
        # The recorder has opened the WAV by now, so this is the first moment
        # the audio can be taken away from other local users.
        _restrict(path)
        return ""

    def _read_stderr(self) -> str:
        if self._stderr is None:
            return ""
        try:
            self._stderr.seek(0)
            return _last_line(self._stderr.read().decode(errors="replace"))
        except (OSError, ValueError):
            return ""

    def stop(self) -> Recording:
        if self._process is None or self._path is None:
            raise RecordingError("No recording is in progress")
        process = self._process
        path = self._path
        started_at = self._started_at or time.monotonic()
        # The recorder may already have quit on its own: an unplugged
        # microphone, a device another app grabbed, or a full disk. That is not
        # the same as never having started, and it deserves the real reason.
        died_on_its_own = process.poll() is not None
        if not died_on_its_own:
            stop_signal = self.backend.stop_signal if self.backend else signal.SIGINT
            process.send_signal(stop_signal)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        duration = max(0.0, time.monotonic() - started_at)
        reason = self._read_stderr()
        self._reset()
        if died_on_its_own:
            path.unlink(missing_ok=True)
            raise RecordingError(reason or "The microphone stopped recording unexpectedly")
        if not path.exists() or path.stat().st_size < 44:
            path.unlink(missing_ok=True)
            # Here the headline is the useful part; the recorder's own last word
            # is only ever a detail, so it does not get to replace it.
            detail = f" ({reason})" if reason else ""
            raise RecordingError(f"The microphone recording was empty{detail}")
        # Repeated in case the recorder replaced the file it was given: FFmpeg
        # does exactly that with -y. A kept recording lives on in the cache, so
        # this is the mode it ends up with on disk.
        _restrict(path)
        return Recording(path=path, duration_seconds=duration)

    def cancel(self) -> Path | None:
        path = self._path
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._reset()
        if path:
            path.unlink(missing_ok=True)
        return path

    def _reset(self) -> None:
        self._process = None
        self._path = None
        self._started_at = None
        if self._stderr is not None:
            try:
                self._stderr.close()
            except OSError:
                pass
            self._stderr = None


def create_recorder(device: str = "") -> CommandRecorder:
    """Return a recorder for this machine, resolving the backend lazily.

    Resolution is deferred to the first recording so the daemon still starts on a
    machine that is missing the recorder, and so the resulting error can be shown
    in the UI instead of crashing at import time.
    """
    return CommandRecorder(device=device)
