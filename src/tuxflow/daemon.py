"""Background recording, transcription, and shortcut service."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from pathlib import Path
from typing import Any

from tuxflow.audio import RecordingError, create_recorder
from tuxflow.config import ConfigStore, Settings
from tuxflow.engine import EngineUnavailableError, WhisperEngine
from tuxflow.history import HistoryStore
from tuxflow.insertion import (
    InsertResult,
    insert_text,
    prepare_input_backend,
    shutdown_input_backend,
)
from tuxflow.notify import notify
from tuxflow.paths import ensure_directories, models_dir, recordings_dir, socket_file
from tuxflow.shortcuts import (
    MANUAL_FALLBACK,
    ShortcutUnavailableError,
    create_shortcut_backend,
)
from tuxflow.system import os_label
from tuxflow.text import process_text
from tuxflow.tray import TrayIndicator

# A held modifier can be missed — a key released while the screen was locked, a
# tap macOS switched off, a stuck portal. Without a ceiling the daemon would
# record until it was restarted and fill the disk with one WAV.
MAX_RECORDING_SECONDS = 600.0

# Opening the microphone takes long enough (device setup plus the recorder's
# settle pause) that a quick tap can release the shortcut before the stream is
# live. "starting" claims the recording early so that release is not dropped;
# it is an internal step, and clients are told "recording" for both.
ACTIVE_STATES = ("starting", "recording")


class TuxFlowDaemon:
    def __init__(self) -> None:
        ensure_directories()
        self.config_store = ConfigStore()
        settings = self.config_store.load()
        self.history = HistoryStore()
        self.recorder = create_recorder(settings.audio_device)
        self.shortcuts = create_shortcut_backend(
            self._on_shortcut_pressed,
            self._on_shortcut_released,
            hotkey=settings.macos_hotkey,
        )
        self.tray = TrayIndicator()
        self.server: asyncio.AbstractServer | None = None
        self.state = "idle"
        self.last_error = ""
        self.shortcut = ""
        self._engine: WhisperEngine | None = None
        self._engine_key: tuple[str, str, str] | None = None
        self._operation_lock = asyncio.Lock()
        self._shortcut_task: asyncio.Task[None] | None = None
        self._transcription_task: asyncio.Task[None] | None = None
        self._recording_watchdog: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        # "starting" is a private step of start_recording; the tray, the desktop
        # app, and `tuxflow status` only know the three settled states.
        state = "recording" if self.state == "starting" else self.state
        return {
            "ok": True,
            "state": state,
            "recording": self.recorder.is_recording,
            "shortcut": self.shortcut,
            "last_error": self.last_error,
            "platform": os_label(),
            "pid": os.getpid(),
        }

    async def run(self) -> None:
        path = socket_file()
        path.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(self._handle_client, path=path)
        path.chmod(0o600)
        await self.tray.start()
        await asyncio.to_thread(prepare_input_backend)
        self._shortcut_task = asyncio.create_task(self._setup_shortcut())
        async with self.server:
            await self.server.serve_forever()

    async def _setup_shortcut(self) -> None:
        disabled = {"TUXFLOW_DISABLE_SHORTCUT", "TUXFLOW_DISABLE_PORTAL"}
        if any(os.environ.get(name) == "1" for name in disabled):
            self.shortcut = f"Global shortcut disabled; {MANUAL_FALLBACK.lower()}"
            return
        try:
            self.shortcut = await self.shortcuts.connect()
        except (ShortcutUnavailableError, TimeoutError) as error:
            self.last_error = str(error)
            self.shortcut = MANUAL_FALLBACK
            self.tray.update("error", self.last_error)
            notify("TuxFlow shortcut needs attention", self.last_error, urgency="normal")

    async def shutdown(self) -> None:
        self.recorder.cancel()
        shutdown_input_backend()
        for task in (self._shortcut_task, self._transcription_task, self._recording_watchdog):
            if task and not task.done():
                task.cancel()
        self.shortcuts.close()
        self.tray.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        socket_file().unlink(missing_ok=True)

    async def _on_shortcut_pressed(self, _shortcut_id: str) -> None:
        await self.start_recording()

    async def _on_shortcut_released(self, _shortcut_id: str) -> None:
        # A release during "starting" waits on the operation lock inside
        # stop_recording, so it takes effect the moment the stream is live.
        if self.state in ACTIVE_STATES:
            await self.stop_recording()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        response: dict[str, Any]
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            request = json.loads(line)
            command = str(request.get("command", "status"))
            response = await self.handle_command(command)
        except Exception as error:
            response = {"ok": False, "error": str(error), **self.status()}
            response["ok"] = False
        try:
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        except (BrokenPipeError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def handle_command(self, command: str) -> dict[str, Any]:
        if command == "status":
            return self.status()
        if command == "toggle":
            await self.toggle()
            return self.status()
        if command == "start":
            await self.start_recording()
            return self.status()
        if command == "stop":
            await self.stop_recording()
            return self.status()
        if command == "cancel":
            await self.cancel_recording()
            return self.status()
        raise ValueError(f"Unknown command: {command}")

    async def toggle(self) -> None:
        if self.state == "idle":
            await self.start_recording()
        elif self.state in ACTIVE_STATES:
            await self.stop_recording()

    async def start_recording(self) -> None:
        async with self._operation_lock:
            if self.state != "idle":
                return
            self.last_error = ""
            # Re-read the microphone choice so a settings change applies to the
            # next dictation instead of waiting for a service restart.
            self.recorder.device = self.config_store.load().audio_device
            path = recordings_dir() / f"{uuid.uuid4().hex}.wav"
            # Claim the recording before the blocking open, so a release that
            # arrives while the microphone is still coming up is not ignored.
            self.state = "starting"
            try:
                await asyncio.to_thread(self.recorder.start, path)
            except RecordingError as error:
                self.state = "idle"
                self.last_error = str(error)
                self.tray.update("error", self.last_error)
                notify("Could not start dictation", self.last_error, urgency="critical")
                return
            self.state = "recording"
            self.tray.update("recording")
            self._recording_watchdog = asyncio.create_task(self._watch_recording_length())

    async def _watch_recording_length(self) -> None:
        await asyncio.sleep(MAX_RECORDING_SECONDS)
        if self.state != "recording":
            return
        notify(
            "Dictation stopped",
            f"TuxFlow recorded for {int(MAX_RECORDING_SECONDS // 60)} minutes without a "
            "release and is transcribing what it has.",
        )
        await self.stop_recording()

    def _cancel_watchdog(self) -> None:
        task = self._recording_watchdog
        self._recording_watchdog = None
        # The watchdog stops the recording itself, so it must not cancel itself
        # part-way through doing so.
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def stop_recording(self) -> None:
        async with self._operation_lock:
            if self.state != "recording":
                return
            self._cancel_watchdog()
            try:
                recording = await asyncio.to_thread(self.recorder.stop)
            except RecordingError as error:
                self.state = "idle"
                self.last_error = str(error)
                self.tray.update("error", self.last_error)
                notify("Recording failed", self.last_error, urgency="critical")
                return
            self.state = "processing"
            self.tray.update("processing")

        self._transcription_task = asyncio.create_task(
            self._finish_transcription(recording.path, recording.duration_seconds)
        )

    async def _finish_transcription(self, path: Path, duration_seconds: float) -> None:
        try:
            await self._transcribe(path, duration_seconds)
        finally:
            self.state = "idle"
            self.tray.update("error" if self.last_error else "idle", self.last_error)

    async def cancel_recording(self) -> None:
        async with self._operation_lock:
            if self.state == "recording":
                self._cancel_watchdog()
                await asyncio.to_thread(self.recorder.cancel)
                self.state = "idle"
                self.tray.update("idle")

    def _get_engine(self, settings: Settings) -> WhisperEngine:
        key = (settings.model, settings.device, settings.compute_type)
        if self._engine is None or self._engine_key != key:
            self._engine = WhisperEngine(
                model_name=settings.model,
                device=settings.device,
                compute_type=settings.compute_type,
                download_root=models_dir(),
            )
            self._engine_key = key
        return self._engine

    def _report_insertion(self, result: InsertResult, text: str, settings: Settings) -> None:
        """Surface a clipboard or paste failure the way every other failure is surfaced.

        Silence here used to look exactly like success: the words were gone and
        nothing said why.
        """
        # An empty transcript is a bare "press enter" command, which has nothing
        # to paste, so a false paste is expected there.
        paste_missing = settings.auto_paste and bool(text) and not result.pasted
        if result.copied and not paste_missing:
            return
        self.last_error = result.detail
        self.tray.update("error", self.last_error)
        if result.copied:
            notify("Could not paste the transcript", self.last_error, urgency="normal")
        else:
            notify("Could not insert the transcript", self.last_error, urgency="critical")

    async def _transcribe(self, path: Path, duration_seconds: float) -> None:
        settings = self.config_store.load()
        engine = self._get_engine(settings)
        try:
            transcript = await asyncio.to_thread(engine.transcribe, path, settings.language)
            processed = process_text(transcript.text, settings)
            if not processed.text and not processed.press_enter:
                return
            insertion = await asyncio.to_thread(
                insert_text,
                processed.text,
                auto_paste=settings.auto_paste,
                send_enter=processed.press_enter,
            )
            # The transcript is kept whatever happened to the clipboard, so a
            # failed paste never loses the words. sqlite opens and commits on
            # the calling thread, which would otherwise stall every IPC client.
            await asyncio.to_thread(
                self.history.add,
                text=processed.text,
                raw_text=transcript.text,
                language=transcript.language,
                duration_seconds=duration_seconds,
                model=settings.model,
            )
            self._report_insertion(insertion, processed.text, settings)
        except EngineUnavailableError as error:
            self.last_error = str(error)
            self.tray.update("error", self.last_error)
            notify("Transcription failed", self.last_error, urgency="critical")
        except Exception as error:
            self.last_error = f"Unexpected transcription error: {error}"
            self.tray.update("error", self.last_error)
            notify("Transcription failed", self.last_error, urgency="critical")
        finally:
            if not settings.keep_audio:
                path.unlink(missing_ok=True)


async def run_daemon() -> None:
    daemon = TuxFlowDaemon()
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def request_stop() -> None:
        stopping.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, request_stop)

    task = asyncio.create_task(daemon.run())
    stop_task = asyncio.create_task(stopping.wait())
    try:
        done, _pending = await asyncio.wait({task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
    finally:
        task.cancel()
        stop_task.cancel()
        await daemon.shutdown()
        try:
            await task
        except asyncio.CancelledError:
            pass
