"""TuxFlow command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from tuxflow import __version__, updater
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
    parser.add_argument("--version", action="version", version=f"tuxflow {__version__}")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("app", help="Open the desktop app")
    subcommands.add_parser("daemon", help="Run the background service")
    for command in ("toggle", "start", "stop", "cancel", "status"):
        subcommands.add_parser(command, help=f"{command.title()} dictation")
    transcribe = subcommands.add_parser("transcribe", help="Transcribe an audio file")
    transcribe.add_argument("audio", type=Path)
    transcribe.add_argument("--raw", action="store_true", help="Skip TuxFlow text cleanup")
    subcommands.add_parser("doctor", help="Check system integration")
    subcommands.add_parser(
        "update", help="Check GitHub for a newer release, and install it on an AppImage"
    )
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
    except Exception as error:
        # send_command raises RuntimeError with a readable sentence, but a
        # traceback is never the right answer for `tuxflow stop`, whatever
        # slipped through — and some exceptions stringify to nothing.
        print(str(error) or f"`tuxflow {command}` failed: {type(error).__name__}", file=sys.stderr)
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


def _print_progress(fraction: float, message: str) -> None:
    # One line that rewrites itself, so a long download does not scroll away
    # the version numbers printed above it.
    percent = f" {int(fraction * 100):3d}%" if fraction > 0 else ""
    print(f"\r{message}…{percent}   ", end="", flush=True)


def _update() -> int:
    current = updater.current_version()
    print(f"Installed: {current}")
    try:
        payload = updater.latest_release(strict=True)
    except updater.UpdateError as error:
        print(error, file=sys.stderr)
        return 1
    if payload is None:
        # 404: nothing has been published. A repository whose only release is
        # still a draft looks exactly like this from outside, which is the
        # expected answer right up until the first release goes public.
        print(f"No release has been published yet. See {updater.RELEASES_PAGE_URL}")
        return 0
    latest = updater.normalise_version(str(payload.get("tag_name") or payload.get("name") or ""))
    print(f"Latest:    {latest or 'unknown'}")

    update = updater.update_from_release(payload, current=current)
    updater.write_last_check()
    if update is None:
        print("TuxFlow is up to date.")
        return 0

    if updater.running_appimage_path() is None:
        # A source install is owned by whatever installed it; rewriting files
        # underneath pip or a distribution package would be a hostile act.
        print(
            f"TuxFlow {update.version} is available: {update.release_url}\n"
            "This copy was not started from an AppImage, so update it the way it "
            "was installed — re-run ./scripts/install.sh from an updated clone."
        )
        return 0

    try:
        installed = updater.download_and_install(update, progress=_print_progress)
    except updater.UpdateError as error:
        print(file=sys.stderr)  # close the progress line
        print(error, file=sys.stderr)
        return 1
    print()  # close the progress line
    print(f"Installed TuxFlow {update.version} into {installed}.")
    print("Restart TuxFlow to run the new version.")
    return 0


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
    if command == "update":
        return _update()
    return 2
