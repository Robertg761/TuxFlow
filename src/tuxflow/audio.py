"""Microphone recording through PipeWire's command-line client."""

from __future__ import annotations

import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class RecordingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Recording:
    path: Path
    duration_seconds: float


class PipeWireRecorder:
    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._path: Path | None = None
        self._started_at: float | None = None

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, path: Path) -> None:
        if self.is_recording:
            raise RecordingError("A recording is already in progress")
        executable = shutil.which("pw-record")
        if not executable:
            raise RecordingError("pw-record is missing; install PipeWire utilities")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._started_at = time.monotonic()
        try:
            self._process = subprocess.Popen(
                [
                    executable,
                    "--format",
                    "s16",
                    "--rate",
                    "16000",
                    "--channels",
                    "1",
                    str(path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            self._reset()
            raise RecordingError(f"Could not start PipeWire recording: {error}") from error
        time.sleep(0.08)
        if self._process.poll() is not None:
            stderr = (self._process.stderr.read() if self._process.stderr else b"").decode(
                errors="replace"
            )
            self._reset()
            raise RecordingError(stderr.strip() or "PipeWire recording exited immediately")

    def stop(self) -> Recording:
        if not self.is_recording or not self._process or not self._path:
            raise RecordingError("No recording is in progress")
        process = self._process
        path = self._path
        started_at = self._started_at or time.monotonic()
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=2)
        duration = max(0.0, time.monotonic() - started_at)
        self._reset()
        if not path.exists() or path.stat().st_size < 44:
            raise RecordingError("The microphone recording was empty")
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
