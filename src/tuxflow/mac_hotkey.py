"""Global push-to-talk hotkey for macOS, built on a Quartz event tap.

macOS has no XDG portal, so TuxFlow watches the keyboard the way other macOS
dictation apps do: a listen-only ``CGEventTap`` reports when the chosen modifier
goes down and when it comes back up, which gives the same hold-to-dictate feel
as the Linux portal shortcut.

The tap needs Accessibility permission. macOS prompts for it the first time the
tap is created, and ``CGEventTapCreate`` returns ``None`` until it is granted.
Because the tap is listen-only it never swallows the key, so the modifier keeps
doing whatever the system already used it for — see ``FN_KEY_ADVICE``.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from tuxflow.shortcuts import SHORTCUT_ID, ShortcutCallback, ShortcutUnavailableError

TAP_START_TIMEOUT_SECONDS = 5.0

PYOBJC_MESSAGE = (
    "The macOS hotkey needs PyObjC. Install it with: pip install 'pyobjc-framework-Quartz'"
)
ACCESSIBILITY_MESSAGE = (
    "macOS has not granted TuxFlow Accessibility access, so the global hotkey "
    "cannot be watched. Open System Settings › Privacy & Security › "
    "Accessibility, allow TuxFlow (or your terminal), then restart the service."
)
FN_KEY_ADVICE = (
    "Set System Settings › Keyboard › “Press 🌐 key to” to “Do Nothing” so "
    "holding fn does not also open the emoji picker."
)


@dataclass(frozen=True, slots=True)
class MacHotkey:
    """A modifier key that TuxFlow can watch for hold-to-dictate."""

    key: str
    label: str
    # Key code reported by the flagsChanged event for this physical key.
    keycode: int
    # Modifier bit that is set while the key is held.
    flag_mask: int


HOTKEYS: dict[str, MacHotkey] = {
    "fn": MacHotkey("fn", "Hold 🌐 fn", 63, 0x800000),
    "right_command": MacHotkey("right_command", "Hold right ⌘ Command", 54, 0x100000),
    "right_option": MacHotkey("right_option", "Hold right ⌥ Option", 61, 0x080000),
    "right_control": MacHotkey("right_control", "Hold right ⌃ Control", 62, 0x040000),
}
DEFAULT_HOTKEY = "fn"


def hotkey_choices() -> list[str]:
    return list(HOTKEYS)


def resolve_hotkey(name: str) -> MacHotkey:
    return HOTKEYS.get(name.strip().lower(), HOTKEYS[DEFAULT_HOTKEY])


class _Frameworks:
    """Attribute lookup across the PyObjC modules that hold the tap symbols.

    Quartz re-exports most of the CoreFoundation run-loop calls, but not on
    every PyObjC release, so CoreFoundation is consulted as a fallback.
    """

    def __init__(self) -> None:
        try:
            import Quartz  # type: ignore[import-not-found]
        except ImportError as error:  # pragma: no cover - requires macOS
            raise ShortcutUnavailableError(PYOBJC_MESSAGE) from error
        self._modules: list[Any] = [Quartz]
        try:
            import CoreFoundation  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover - requires macOS
            pass
        else:
            self._modules.append(CoreFoundation)

    def __getattr__(self, name: str) -> Any:
        for module in self._modules:
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(f"PyObjC is missing {name}")


class MacHotkeyListener:
    """Watch a modifier key and report press and release to the daemon."""

    def __init__(
        self,
        press_callback: ShortcutCallback,
        release_callback: ShortcutCallback | None = None,
        *,
        hotkey: str = DEFAULT_HOTKEY,
        frameworks: Any | None = None,
    ) -> None:
        self.press_callback = press_callback
        self.release_callback = release_callback
        self.hotkey = resolve_hotkey(hotkey)
        self.bound_shortcut = ""
        self._frameworks = frameworks
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._run_loop: Any | None = None
        self._tap: Any | None = None
        self._ready = threading.Event()
        self._error = ""
        self._pressed = False
        # asyncio only holds a weak reference to a running task, so without this
        # a dictation could be collected mid-flight.
        self._tasks: set[asyncio.Task[Any]] = set()

    async def connect(self) -> str:
        self._loop = asyncio.get_running_loop()
        frameworks = self._frameworks or _Frameworks()
        self._frameworks = frameworks
        self._thread = threading.Thread(
            target=self._run,
            name="tuxflow-hotkey",
            daemon=True,
        )
        self._thread.start()
        started = await asyncio.to_thread(self._ready.wait, TAP_START_TIMEOUT_SECONDS)
        if not started:
            raise ShortcutUnavailableError("Timed out starting the macOS hotkey listener")
        if self._error:
            raise ShortcutUnavailableError(self._error)
        self.bound_shortcut = self.hotkey.label
        return self.bound_shortcut

    def _run(self) -> None:
        frameworks = self._frameworks
        try:
            mask = frameworks.CGEventMaskBit(frameworks.kCGEventFlagsChanged)
            tap = frameworks.CGEventTapCreate(
                frameworks.kCGSessionEventTap,
                frameworks.kCGHeadInsertEventTap,
                frameworks.kCGEventTapOptionListenOnly,
                mask,
                self._handle_event,
                None,
            )
            if tap is None:
                self._error = ACCESSIBILITY_MESSAGE
                return
            source = frameworks.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._run_loop = frameworks.CFRunLoopGetCurrent()
            frameworks.CFRunLoopAddSource(self._run_loop, source, frameworks.kCFRunLoopCommonModes)
            frameworks.CGEventTapEnable(tap, True)
            self._tap = tap
        except ShortcutUnavailableError as error:
            self._error = str(error)
            return
        except Exception as error:
            self._error = f"Could not start the macOS hotkey listener: {error}"
            return
        finally:
            self._ready.set()
        frameworks.CFRunLoopRun()

    def _handle_event(self, _proxy: Any, event_type: Any, event: Any, _refcon: Any) -> Any:
        frameworks = self._frameworks
        # An exception raised in a tap callback is swallowed by Quartz and can
        # leave the tap disabled, so a failure here drops one key event instead.
        with suppress(Exception):
            disabled = {
                frameworks.kCGEventTapDisabledByTimeout,
                frameworks.kCGEventTapDisabledByUserInput,
            }
            if event_type in disabled:
                # macOS switches a tap off if a callback ever runs long. Turning it
                # back on is the documented recovery and keeps dictation working.
                if self._tap is not None:
                    frameworks.CGEventTapEnable(self._tap, True)
                # The release that happened while the tap was off never arrived,
                # so treat the gap as one: otherwise a dictation started before
                # the tap died would record until the daemon is restarted.
                self._set_pressed(False)
            elif event_type == frameworks.kCGEventFlagsChanged:
                keycode = int(
                    frameworks.CGEventGetIntegerValueField(
                        event, frameworks.kCGKeyboardEventKeycode
                    )
                )
                if keycode == self.hotkey.keycode:
                    flags = int(frameworks.CGEventGetFlags(event))
                    self._set_pressed(bool(flags & self.hotkey.flag_mask))
        return event

    def _set_pressed(self, held: bool) -> None:
        if held and not self._pressed:
            self._pressed = True
            self._dispatch(self.press_callback)
        elif not held and self._pressed:
            self._pressed = False
            self._dispatch(self.release_callback)

    def _dispatch(self, callback: ShortcutCallback | None) -> None:
        loop = self._loop
        if callback is None or loop is None:
            return

        def schedule() -> None:
            task = loop.create_task(callback(SHORTCUT_ID))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        try:
            loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            # The daemon's loop is already closing.
            pass

    def close(self) -> None:
        frameworks = self._frameworks
        if frameworks is not None and self._tap is not None:
            with suppress(Exception):
                frameworks.CGEventTapEnable(self._tap, False)
        if frameworks is not None and self._run_loop is not None:
            with suppress(Exception):
                frameworks.CFRunLoopStop(self._run_loop)
        self._tap = None
        self._run_loop = None
