"""Desktop notification helper for Linux and macOS."""

from __future__ import annotations

import shutil
import subprocess

from tuxflow.system import is_macos

APP_NAME = "TuxFlow"


def _spawn(command: list[str]) -> None:
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def _applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _notify_macos(title: str, body: str) -> None:
    if shutil.which("terminal-notifier"):
        _spawn(["terminal-notifier", "-title", APP_NAME, "-subtitle", title, "-message", body])
        return
    if not shutil.which("osascript"):
        return
    script = (
        f"display notification {_applescript_string(body)} "
        f"with title {_applescript_string(APP_NAME)} "
        f"subtitle {_applescript_string(title)}"
    )
    _spawn(["osascript", "-e", script])


def _notify_linux(title: str, body: str, urgency: str) -> None:
    executable = shutil.which("notify-send")
    if not executable:
        return
    _spawn(
        [
            executable,
            f"--app-name={APP_NAME}",
            f"--urgency={urgency}",
            "--expire-time=2500",
            title,
            body,
        ]
    )


def notify(title: str, body: str = "", *, urgency: str = "normal") -> None:
    if is_macos():
        _notify_macos(title, body)
        return
    _notify_linux(title, body, urgency)
