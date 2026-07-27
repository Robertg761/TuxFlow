"""TuxFlow command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from tuxflow.config import ConfigStore
from tuxflow.daemon import run_daemon
from tuxflow.doctor import platform_summary, run_checks
from tuxflow.engine import EngineUnavailableError, WhisperEngine
from tuxflow.ipc import send_command
from tuxflow.paths import models_dir
from tuxflow.system import is_macos
from tuxflow.text import process_text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tuxflow", description="Private, local Whisper dictation for Linux and macOS"
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("app", help="Open the desktop app")
    subcommands.add_parser("daemon", help="Run the background service")
    for command in ("toggle", "start", "stop", "cancel", "status"):
        subcommands.add_parser(command, help=f"{command.title()} dictation")
    transcribe = subcommands.add_parser("transcribe", help="Transcribe an audio file")
    transcribe.add_argument("audio", type=Path)
    transcribe.add_argument("--raw", action="store_true", help="Skip TuxFlow text cleanup")
    subcommands.add_parser("doctor", help="Check system integration")
    return parser


def _open_app() -> int:
    try:
        from tuxflow.app import run_app
    except (ImportError, ValueError) as error:
        install = (
            "brew install pygobject3 gtk4 libadwaita adwaita-icon-theme"
            if is_macos()
            else "install python3-gobject, gtk4, and libadwaita with your package manager"
        )
        print(
            f"The GTK desktop dependencies are missing. Run: {install}\n"
            "Every other command, including `tuxflow toggle`, works without them.\n"
            f"Detail: {error}",
            file=sys.stderr,
        )
        return 1
    return run_app()


def _control(command: str) -> int:
    try:
        response = asyncio.run(send_command(command))
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(response, indent=2))
    return 0 if response.get("ok") else 1


def _transcribe(path: Path, raw: bool) -> int:
    if not path.is_file():
        print(f"Audio file not found: {path}", file=sys.stderr)
        return 2
    settings = ConfigStore().load()
    engine = WhisperEngine(
        model_name=settings.model,
        device=settings.device,
        compute_type=settings.compute_type,
        download_root=models_dir(),
    )
    try:
        result = engine.transcribe(path, settings.language)
    except EngineUnavailableError as error:
        print(error, file=sys.stderr)
        return 1
    text = result.text if raw else process_text(result.text, settings).text
    print(text)
    return 0


def _doctor() -> int:
    checks = run_checks()
    print(platform_summary())
    for check in checks:
        icon = "✓" if check.ok else ("!" if not check.required else "✗")
        print(f"{icon} {check.name}: {check.detail}")
    return 0 if all(check.ok or not check.required for check in checks) else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "app"
    if command == "app":
        return _open_app()
    if command == "daemon":
        try:
            asyncio.run(run_daemon())
        except KeyboardInterrupt:
            pass
        return 0
    if command in {"toggle", "start", "stop", "cancel", "status"}:
        return _control(command)
    if command == "transcribe":
        return _transcribe(args.audio, args.raw)
    if command == "doctor":
        return _doctor()
    return 2
