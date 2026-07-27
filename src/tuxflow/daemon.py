"""Background recording, transcription, and shortcut service."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from pathlib import Path
from typing import Any

from tuxflow.audio import PipeWireRecorder, RecordingError
from tuxflow.config import ConfigStore, Settings
from tuxflow.engine import EngineUnavailableError, WhisperEngine
from tuxflow.history import HistoryStore
from tuxflow.insertion import insert_text, prepare_input_backend, shutdown_input_backend
from tuxflow.notify import notify
from tuxflow.paths import ensure_directories, models_dir, recordings_dir, socket_file
from tuxflow.portal import GlobalShortcutsPortal, PortalUnavailableError
from tuxflow.text import process_text
from tuxflow.tray import TrayIndicator


class TuxFlowDaemon:
    def __init__(self) -> None:
        ensure_directories()
        self.config_store = ConfigStore()
        self.history = HistoryStore()
        self.recorder = PipeWireRecorder()
        self.portal = GlobalShortcutsPortal(
            self._on_shortcut_pressed,
            self._on_shortcut_released,
        )
        self.tray = TrayIndicator()
        self.server: asyncio.AbstractServer | None = None
        self.state = "idle"
        self.last_error = ""
        self.shortcut = ""
        self._engine: WhisperEngine | None = None
        self._engine_key: tuple[str, str, str] | None = None
        self._operation_lock = asyncio.Lock()
        self._portal_task: asyncio.Task[None] | None = None
        self._transcription_task: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "state": self.state,
            "recording": self.recorder.is_recording,
            "shortcut": self.shortcut,
            "last_error": self.last_error,
            "pid": os.getpid(),
        }

    async def run(self) -> None:
        path = socket_file()
        path.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(self._handle_client, path=path)
        path.chmod(0o600)
        await self.tray.start()
        await asyncio.to_thread(prepare_input_backend)
        self._portal_task = asyncio.create_task(self._setup_portal())
        async with self.server:
            await self.server.serve_forever()

    async def _setup_portal(self) -> None:
        if os.environ.get("TUXFLOW_DISABLE_PORTAL") == "1":
            self.shortcut = "Global shortcut portal disabled; use `tuxflow toggle`"
            return
        try:
            self.shortcut = await self.portal.connect()
        except (PortalUnavailableError, TimeoutError) as error:
            self.last_error = str(error)
            self.shortcut = "Use `tuxflow toggle` or configure a desktop shortcut"
            self.tray.update("error", self.last_error)
            notify("TuxFlow shortcut needs attention", self.last_error, urgency="normal")

    async def shutdown(self) -> None:
        self.recorder.cancel()
        shutdown_input_backend()
        for task in (self._portal_task, self._transcription_task):
            if task and not task.done():
                task.cancel()
        self.portal.close()
        self.tray.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        socket_file().unlink(missing_ok=True)

    async def _on_shortcut_pressed(self, _shortcut_id: str) -> None:
        await self.start_recording()

    async def _on_shortcut_released(self, _shortcut_id: str) -> None:
        if self.state == "recording":
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
        elif self.state == "recording":
            await self.stop_recording()

    async def start_recording(self) -> None:
        async with self._operation_lock:
            if self.state != "idle":
                return
            self.last_error = ""
            path = recordings_dir() / f"{uuid.uuid4().hex}.wav"
            try:
                await asyncio.to_thread(self.recorder.start, path)
            except RecordingError as error:
                self.last_error = str(error)
                self.tray.update("error", self.last_error)
                notify("Could not start dictation", self.last_error, urgency="critical")
                return
            self.state = "recording"
            self.tray.update("recording")

    async def stop_recording(self) -> None:
        async with self._operation_lock:
            if self.state != "recording":
                return
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

    async def _transcribe(self, path: Path, duration_seconds: float) -> None:
        settings = self.config_store.load()
        engine = self._get_engine(settings)
        try:
            transcript = await asyncio.to_thread(engine.transcribe, path, settings.language)
            processed = process_text(transcript.text, settings)
            if not processed.text and not processed.press_enter:
                return
            await asyncio.to_thread(
                insert_text,
                processed.text,
                auto_paste=settings.auto_paste,
                send_enter=processed.press_enter,
            )
            self.history.add(
                text=processed.text,
                raw_text=transcript.text,
                language=transcript.language,
                duration_seconds=duration_seconds,
                model=settings.model,
            )
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
