"""Actionable diagnostics for TuxFlow's desktop integration."""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
from dataclasses import dataclass

from tuxflow.audio import missing_recorder_message, select_backend
from tuxflow.insertion import (
    clipboard_tool,
    macos_accessibility_trusted,
    ydotool_can_start,
)
from tuxflow.paths import socket_file
from tuxflow.system import is_linux, is_macos, os_label, unsupported_platform_message


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def run_checks() -> list[Check]:
    """Return the integration checks that matter on this platform."""
    if is_macos():
        return _macos_checks()
    if is_linux():
        return _linux_checks()
    return [Check("Operating system", False, unsupported_platform_message("TuxFlow"))]


# --------------------------------------------------------------------------- #
# Shared checks
# --------------------------------------------------------------------------- #


def _recorder_check() -> Check:
    backend = select_backend()
    return Check(
        "Microphone recorder",
        backend is not None,
        f"{backend.executable} — {backend.name}" if backend else missing_recorder_message(),
    )


def _engine_check() -> Check:
    installed = importlib.util.find_spec("faster_whisper") is not None
    return Check(
        "Whisper engine",
        installed,
        "faster-whisper installed" if installed else "Run ./scripts/install.sh",
    )


def _clipboard_check() -> Check:
    tool = clipboard_tool()
    if is_macos():
        hint = "pbcopy is missing from this macOS install"
    else:
        session = os.environ.get("XDG_SESSION_TYPE", "unknown")
        hint = f"Install wl-clipboard for the {session} session, or xclip on X11"
    return Check("Clipboard", tool is not None, tool or hint)


def _service_check() -> Check:
    running = _socket_responding()
    return Check(
        "Background service",
        running,
        str(socket_file()) if running else "Not running. Start it with: tuxflow daemon",
        required=False,
    )


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


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #


def _linux_checks() -> list[Check]:
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    has_bus = bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))
    can_paste = (
        ydotool_can_start()
        or shutil.which("wtype") is not None
        or (session == "x11" and shutil.which("xdotool") is not None)
    )
    return [
        _recorder_check(),
        _engine_check(),
        Check(
            "Desktop portal",
            has_bus,
            f"Session D-Bus available for the {session} session"
            if has_bus
            else "Run TuxFlow inside a graphical desktop session",
        ),
        _clipboard_check(),
        Check(
            "Automatic paste",
            can_paste,
            "ydotool, wtype, or xdotool available"
            if can_paste
            else "Grant access to /dev/uinput for ydotool "
            "(./scripts/install.sh --with-uinput); clipboard-only mode still works",
            required=False,
        ),
        _service_check(),
    ]


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #


def _macos_checks() -> list[Check]:
    from tuxflow.config import ConfigStore
    from tuxflow.mac_hotkey import FN_KEY_ADVICE, resolve_hotkey

    has_pyobjc = importlib.util.find_spec("Quartz") is not None
    trusted = macos_accessibility_trusted()
    hotkey = resolve_hotkey(ConfigStore().load().macos_hotkey)
    checks = [
        _recorder_check(),
        _engine_check(),
        Check(
            "Global hotkey support",
            has_pyobjc,
            f"PyObjC installed — {hotkey.label}"
            if has_pyobjc
            else "Run: pip install 'pyobjc-framework-Quartz'",
        ),
        Check(
            "Accessibility permission",
            trusted is not False,
            {
                True: "Granted — the hotkey and automatic paste can run",
                False: "Not granted. Open System Settings › Privacy & Security › "
                "Accessibility and allow TuxFlow, then restart the service",
                None: "Unknown. macOS asks the first time TuxFlow sends a keystroke",
            }[trusted],
        ),
        _clipboard_check(),
        Check(
            "Automatic paste",
            shutil.which("osascript") is not None,
            "System Events can send ⌘V"
            if shutil.which("osascript")
            else "osascript is missing; clipboard-only mode still works",
            required=False,
        ),
        _service_check(),
    ]
    if hotkey.key == "fn":
        checks.append(Check("Keyboard setting", True, FN_KEY_ADVICE, required=False))
    return checks


def platform_summary() -> str:
    return f"TuxFlow on {os_label()}"
