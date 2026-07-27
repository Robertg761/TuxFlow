"""Global push-to-talk shortcut backends.

Linux asks the XDG desktop portal to own the shortcut; macOS watches the
keyboard through a Quartz event tap. Both expose the same two calls, so the
daemon never has to know which one it is talking to.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from tuxflow.system import is_linux, is_macos, os_label

SHORTCUT_ID = "toggle-dictation"
MANUAL_FALLBACK = "Use `tuxflow toggle` or bind it to a shortcut of your choice"

ShortcutCallback = Callable[[str], Awaitable[None]]


class ShortcutUnavailableError(RuntimeError):
    """The platform could not give TuxFlow a global shortcut."""


@runtime_checkable
class ShortcutBackend(Protocol):
    async def connect(self) -> str:
        """Register the shortcut and return its human-readable description."""

    def close(self) -> None:
        """Release the shortcut."""


class NullShortcutBackend:
    """Stand-in for platforms with no supported shortcut mechanism."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def connect(self) -> str:
        raise ShortcutUnavailableError(self.reason)

    def close(self) -> None:
        return None


def create_shortcut_backend(
    press_callback: ShortcutCallback,
    release_callback: ShortcutCallback | None = None,
    *,
    hotkey: str = "",
) -> ShortcutBackend:
    """Return the shortcut backend for this platform.

    Imports are deferred because each backend pulls in bindings that only exist
    on its own platform (``dbus-next`` on Linux, PyObjC on macOS).
    """
    if is_linux():
        from tuxflow.portal import GlobalShortcutsPortal

        return GlobalShortcutsPortal(press_callback, release_callback)
    if is_macos():
        from tuxflow.mac_hotkey import MacHotkeyListener

        return MacHotkeyListener(press_callback, release_callback, hotkey=hotkey)
    return NullShortcutBackend(
        f"TuxFlow has no global shortcut backend for {os_label()}. {MANUAL_FALLBACK}."
    )
