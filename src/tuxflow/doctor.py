"""Actionable diagnostics for Linux integration dependencies."""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
from dataclasses import dataclass

from tuxflow.insertion import ydotool_can_start
from tuxflow.paths import socket_file


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def run_checks() -> list[Check]:
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    clipboard_tool = next(
        (tool for tool in ("wl-copy", "xclip", "xsel") if shutil.which(tool)), None
    )
    can_paste = (
        ydotool_can_start()
        or shutil.which("wtype") is not None
        or (session == "x11" and shutil.which("xdotool") is not None)
    )
    checks = [
        Check(
            "PipeWire recorder",
            shutil.which("pw-record") is not None,
            shutil.which("pw-record") or "Install pipewire-utils",
        ),
        Check(
            "Whisper engine",
            importlib.util.find_spec("faster_whisper") is not None,
            "faster-whisper installed"
            if importlib.util.find_spec("faster_whisper")
            else "Run ./scripts/install.sh",
        ),
        Check(
            "Desktop portal",
            shutil.which("gdbus") is not None and bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")),
            "Session D-Bus available"
            if os.environ.get("DBUS_SESSION_BUS_ADDRESS")
            else "Run TuxFlow inside a graphical desktop session",
        ),
        Check(
            "Clipboard",
            clipboard_tool is not None,
            f"{clipboard_tool} available for the {session} session"
            if clipboard_tool
            else "Install wl-clipboard on Wayland or xclip on X11",
        ),
        Check(
            "Automatic paste",
            can_paste,
            "ydotool, wtype, or xdotool available"
            if can_paste
            else "Grant access to /dev/uinput for ydotool; clipboard-only mode still works",
            required=False,
        ),
        Check(
            "Background service",
            _socket_responding(),
            str(socket_file()),
            required=False,
        ),
    ]
    return checks


def _socket_responding() -> bool:
    path = socket_file()
    if not path.exists():
        return False
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(0.3)
    try:
        client.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        client.close()
