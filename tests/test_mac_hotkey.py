"""Tests for the macOS push-to-talk listener.

PyObjC only exists on macOS, so the Quartz calls are driven through a stand-in
that records what the listener asked for. That keeps the press/release logic —
the part that decides when dictation starts — testable everywhere.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from tuxflow.mac_hotkey import (
    ACCESSIBILITY_MESSAGE,
    HOTKEYS,
    MacHotkeyListener,
    resolve_hotkey,
)
from tuxflow.shortcuts import SHORTCUT_ID, ShortcutUnavailableError

FLAGS_CHANGED = 12
TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF


class FakeFrameworks:
    """The handful of Quartz symbols :class:`MacHotkeyListener` touches."""

    kCGEventFlagsChanged = FLAGS_CHANGED
    kCGEventTapDisabledByTimeout = TAP_DISABLED_BY_TIMEOUT
    kCGEventTapDisabledByUserInput = TAP_DISABLED_BY_USER_INPUT
    kCGKeyboardEventKeycode = 9
    kCGSessionEventTap = 0
    kCGHeadInsertEventTap = 0
    kCGEventTapOptionListenOnly = 1
    kCFRunLoopCommonModes = "common"

    def __init__(self, *, tap: object | None = "tap") -> None:
        self.tap = tap
        self.enable_calls: list[tuple[object, bool]] = []
        self.stopped = False
        self._running = threading.Event()
        self._finished = threading.Event()

    def CGEventMaskBit(self, event_type):
        return 1 << event_type

    def CGEventTapCreate(self, _location, _place, _option, mask, callback, _refcon):
        assert mask == 1 << FLAGS_CHANGED, "only flagsChanged should be watched"
        self.callback = callback
        return self.tap

    def CFMachPortCreateRunLoopSource(self, _allocator, tap, _order):
        assert tap is self.tap
        return "source"

    def CFRunLoopGetCurrent(self):
        return "run-loop"

    def CFRunLoopAddSource(self, run_loop, source, mode):
        assert (run_loop, source, mode) == ("run-loop", "source", self.kCFRunLoopCommonModes)

    def CGEventTapEnable(self, tap, enabled):
        self.enable_calls.append((tap, enabled))

    def CFRunLoopRun(self):
        self._running.set()
        self._finished.wait(5)

    def CFRunLoopStop(self, _run_loop):
        self.stopped = True
        self._finished.set()

    def CGEventGetIntegerValueField(self, event, field):
        assert field == self.kCGKeyboardEventKeycode
        return event["keycode"]

    def CGEventGetFlags(self, event):
        return event["flags"]


def _event(keycode: int, flags: int) -> dict[str, int]:
    return {"keycode": keycode, "flags": flags}


async def _drain() -> None:
    # One hop for call_soon_threadsafe, one for the task it schedules.
    for _ in range(3):
        await asyncio.sleep(0)


def test_holding_the_key_starts_dictation_and_releasing_it_stops(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    events: list[str] = []
    fn = HOTKEYS["fn"]

    async def pressed(shortcut_id: str) -> None:
        events.append(f"pressed:{shortcut_id}")

    async def released(shortcut_id: str) -> None:
        events.append(f"released:{shortcut_id}")

    frameworks = FakeFrameworks()
    listener = MacHotkeyListener(pressed, released, hotkey="fn", frameworks=frameworks)

    async def scenario() -> None:
        assert await listener.connect() == fn.label
        send = frameworks.callback
        send(None, FLAGS_CHANGED, _event(fn.keycode, fn.flag_mask), None)
        send(None, FLAGS_CHANGED, _event(fn.keycode, fn.flag_mask), None)
        await _drain()
        send(None, FLAGS_CHANGED, _event(fn.keycode, 0), None)
        await _drain()
        listener.close()

    asyncio.run(scenario())

    assert events == [f"pressed:{SHORTCUT_ID}", f"released:{SHORTCUT_ID}"]
    assert frameworks.stopped
    assert (frameworks.tap, True) in frameworks.enable_calls


def test_a_different_modifier_is_ignored():
    events: list[str] = []

    async def pressed(shortcut_id: str) -> None:
        events.append(shortcut_id)

    frameworks = FakeFrameworks()
    listener = MacHotkeyListener(pressed, hotkey="fn", frameworks=frameworks)

    async def scenario() -> None:
        await listener.connect()
        # Left shift, held down: same event type, different key.
        frameworks.callback(None, FLAGS_CHANGED, _event(56, 0x020000), None)
        await _drain()
        listener.close()

    asyncio.run(scenario())
    assert events == []


def test_the_tap_is_re_enabled_after_macos_disables_it():
    frameworks = FakeFrameworks()

    async def pressed(_shortcut_id: str) -> None:
        pass

    listener = MacHotkeyListener(pressed, frameworks=frameworks)

    async def scenario() -> None:
        await listener.connect()
        frameworks.enable_calls.clear()
        frameworks.callback(None, TAP_DISABLED_BY_TIMEOUT, _event(0, 0), None)
        listener.close()

    asyncio.run(scenario())
    assert (frameworks.tap, True) in frameworks.enable_calls


def test_a_disabled_tap_does_not_leave_dictation_running_forever():
    # The release arrives while the tap is off, so the listener never sees it.
    # Without treating the outage as a release, this dictation never ends.
    events: list[str] = []
    fn = HOTKEYS["fn"]

    async def pressed(_shortcut_id: str) -> None:
        events.append("pressed")

    async def released(_shortcut_id: str) -> None:
        events.append("released")

    frameworks = FakeFrameworks()
    listener = MacHotkeyListener(pressed, released, hotkey="fn", frameworks=frameworks)

    async def scenario() -> None:
        await listener.connect()
        frameworks.callback(None, FLAGS_CHANGED, _event(fn.keycode, fn.flag_mask), None)
        await _drain()
        frameworks.callback(None, TAP_DISABLED_BY_USER_INPUT, _event(0, 0), None)
        await _drain()
        # A later press still works, so recovery is not a one-way door.
        frameworks.callback(None, FLAGS_CHANGED, _event(fn.keycode, fn.flag_mask), None)
        await _drain()
        listener.close()

    asyncio.run(scenario())
    assert events == ["pressed", "released", "pressed"]


def test_denied_accessibility_permission_is_explained_not_swallowed():
    frameworks = FakeFrameworks(tap=None)

    async def pressed(_shortcut_id: str) -> None:
        pass

    listener = MacHotkeyListener(pressed, frameworks=frameworks)

    with pytest.raises(ShortcutUnavailableError) as error:
        asyncio.run(listener.connect())

    assert str(error.value) == ACCESSIBILITY_MESSAGE
    assert "System Settings" in ACCESSIBILITY_MESSAGE


def test_an_unknown_hotkey_setting_falls_back_to_fn():
    assert resolve_hotkey("Right_Command").keycode == HOTKEYS["right_command"].keycode
    assert resolve_hotkey("").key == "fn"
    assert resolve_hotkey("caps lock").key == "fn"
