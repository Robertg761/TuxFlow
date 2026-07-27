"""Clipboard and simulated paste for Wayland, X11, and macOS.

The public surface is platform neutral — :func:`insert_text` copies the
transcript and, when asked, presses the paste shortcut of the host platform.
Everything below it is split into ``_linux_*`` and ``_macos_*`` helpers.
"""

from __future__ import annotations

import importlib
import os
import shutil
import socket as socket_module
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from tuxflow.system import is_linux, is_macos

_ydotool_daemon: subprocess.Popen[bytes] | None = None
_ydotool_owned_socket: Path | None = None
YDOTOOL_DEVICE_SETTLE_SECONDS = 0.75

# macOS keystrokes go through System Events rather than a CGEventTap because
# osascript reports a real failure when Accessibility access has not been
# granted. Posting CGEvents from an untrusted process silently does nothing,
# which would make TuxFlow claim it pasted when it did not.
_MACOS_PASTE_SCRIPT = 'tell application "System Events" to keystroke "v" using command down'
_MACOS_RETURN_SCRIPT = 'tell application "System Events" to key code 36'


@dataclass(frozen=True, slots=True)
class InsertResult:
    copied: bool
    pasted: bool
    detail: str


def _run(command: list[str], *, input_text: str | None = None) -> bool:
    try:
        subprocess.run(
            command,
            input=input_text,
            text=True,
            check=True,
            timeout=4,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #


def _linux_copy_text(text: str) -> bool:
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland" and shutil.which("wl-copy"):
        return _run(["wl-copy", "--type", "text/plain;charset=utf-8"], input_text=text)
    if shutil.which("xclip"):
        return _run(["xclip", "-selection", "clipboard"], input_text=text)
    if shutil.which("xsel"):
        return _run(["xsel", "--clipboard", "--input"], input_text=text)
    if shutil.which("wl-copy"):
        return _run(["wl-copy", "--type", "text/plain;charset=utf-8"], input_text=text)
    return False


def _ydotool_socket() -> Path:
    configured = os.environ.get("YDOTOOL_SOCKET")
    if configured:
        return Path(configured)
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / ".ydotool_socket"


def ydotool_can_start() -> bool:
    return bool(
        shutil.which("ydotool")
        and (
            _socket_listening(_ydotool_socket())
            or (shutil.which("ydotoold") and os.access("/dev/uinput", os.W_OK))
        )
    )


def _socket_listening(path: Path) -> bool:
    if not path.exists():
        return False
    client = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_DGRAM)
    client.settimeout(0.1)
    try:
        client.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        client.close()


def _ensure_ydotool() -> bool:
    global _ydotool_daemon, _ydotool_owned_socket
    if not shutil.which("ydotool"):
        return False
    socket = _ydotool_socket()
    os.environ["YDOTOOL_SOCKET"] = str(socket)
    if _socket_listening(socket):
        return True
    socket.unlink(missing_ok=True)
    daemon = shutil.which("ydotoold")
    if not daemon or not os.access("/dev/uinput", os.W_OK):
        return False
    socket.parent.mkdir(parents=True, exist_ok=True)
    if _ydotool_daemon is None or _ydotool_daemon.poll() is not None:
        try:
            _ydotool_daemon = subprocess.Popen(
                [
                    daemon,
                    f"--socket-path={socket}",
                    "--socket-perm=0600",
                    "--mouse-off",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _ydotool_owned_socket = socket
        except OSError:
            return False
    for _attempt in range(10):
        if _socket_listening(socket):
            # The control socket becomes available before compositors have finished
            # discovering ydotoold's new virtual keyboard. Without this pause, the
            # first paste after daemon startup can be reported as sent but be dropped.
            time.sleep(YDOTOOL_DEVICE_SETTLE_SECONDS)
            return True
        time.sleep(0.05)
    return False


def _linux_paste_clipboard() -> bool:
    time.sleep(0.08)
    # Linux input event codes: LEFTCTRL=29 and V=47.
    if _ensure_ydotool() and _run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]):
        return True
    if shutil.which("wtype") and _run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"]):
        return True
    if os.environ.get("DISPLAY") and shutil.which("xdotool"):
        return _run(["xdotool", "key", "--clearmodifiers", "ctrl+v"])
    return False


def _linux_press_enter() -> bool:
    if _ensure_ydotool() and _run(["ydotool", "key", "28:1", "28:0"]):
        return True
    if shutil.which("wtype") and _run(["wtype", "-k", "Return"]):
        return True
    if os.environ.get("DISPLAY") and shutil.which("xdotool"):
        return _run(["xdotool", "key", "Return"])
    return False


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #


def _macos_copy_text(text: str) -> bool:
    if not shutil.which("pbcopy"):
        return False
    return _run(["pbcopy"], input_text=text)


def _macos_run_script(script: str) -> bool:
    if not shutil.which("osascript"):
        return False
    return _run(["osascript", "-e", script])


def _macos_paste_clipboard() -> bool:
    time.sleep(0.08)
    return _macos_run_script(_MACOS_PASTE_SCRIPT)


def _macos_press_enter() -> bool:
    return _macos_run_script(_MACOS_RETURN_SCRIPT)


def macos_accessibility_trusted() -> bool | None:
    """Whether macOS trusts this process to send keystrokes.

    Returns ``None`` when the answer cannot be determined, which is the case
    whenever PyObjC is not installed.

    ``AXIsProcessTrusted`` has moved between PyObjC modules over the years and
    lives in a different one depending on which subpackages are installed, so
    every known home is tried before giving up.
    """
    for module_name in ("ApplicationServices", "HIServices", "Quartz"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        is_trusted = getattr(module, "AXIsProcessTrusted", None)
        if is_trusted is None:
            continue
        try:
            return bool(is_trusted())
        except Exception:
            return None
    return None


# --------------------------------------------------------------------------- #
# Platform-neutral entry points
# --------------------------------------------------------------------------- #


def copy_text(text: str) -> bool:
    if is_macos():
        return _macos_copy_text(text)
    if is_linux():
        return _linux_copy_text(text)
    return False


def paste_clipboard() -> bool:
    if is_macos():
        return _macos_paste_clipboard()
    if is_linux():
        return _linux_paste_clipboard()
    return False


def press_enter() -> bool:
    if is_macos():
        return _macos_press_enter()
    if is_linux():
        return _linux_press_enter()
    return False


def clipboard_tool() -> str | None:
    """Name of the clipboard helper TuxFlow would use, for diagnostics."""
    candidates = ("pbcopy",) if is_macos() else ("wl-copy", "xclip", "xsel")
    return next((tool for tool in candidates if shutil.which(tool)), None)


def can_paste_automatically() -> bool:
    if is_macos():
        return shutil.which("osascript") is not None and macos_accessibility_trusted() is not False
    if is_linux():
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        return (
            ydotool_can_start()
            or shutil.which("wtype") is not None
            or (session == "x11" and shutil.which("xdotool") is not None)
        )
    return False


def prepare_input_backend() -> bool:
    """Start the virtual keyboard early so it is ready before the first dictation."""
    if is_linux():
        return _ensure_ydotool()
    # macOS needs no helper process; permission is requested on first keystroke.
    return is_macos()


def shutdown_input_backend() -> None:
    global _ydotool_daemon, _ydotool_owned_socket
    if _ydotool_daemon and _ydotool_daemon.poll() is None:
        _ydotool_daemon.terminate()
        try:
            _ydotool_daemon.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _ydotool_daemon.kill()
    if _ydotool_owned_socket:
        _ydotool_owned_socket.unlink(missing_ok=True)
    _ydotool_daemon = None
    _ydotool_owned_socket = None


def insert_text(text: str, *, auto_paste: bool, send_enter: bool = False) -> InsertResult:
    copied = copy_text(text) if text else True
    if not copied:
        return InsertResult(False, False, "No supported clipboard tool was found")
    pasted = paste_clipboard() if auto_paste and text else False
    if send_enter and (pasted or not text):
        press_enter()
    if pasted:
        detail = "Pasted into the active app"
    elif auto_paste:
        detail = "Copied, but automatic paste is unavailable"
    else:
        detail = "Copied to the clipboard"
    return InsertResult(copied, pasted, detail)
