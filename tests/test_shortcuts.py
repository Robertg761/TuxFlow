from __future__ import annotations

import asyncio

import pytest

from tuxflow import shortcuts


async def _callback(_shortcut_id: str) -> None:
    pass


def test_linux_uses_the_desktop_portal(monkeypatch):
    pytest.importorskip("dbus_next", reason="the portal backend needs dbus-next")
    from tuxflow.portal import GlobalShortcutsPortal

    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    assert isinstance(shortcuts.create_shortcut_backend(_callback), GlobalShortcutsPortal)


def test_macos_uses_the_quartz_hotkey_listener(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    from tuxflow.mac_hotkey import MacHotkeyListener

    backend = shortcuts.create_shortcut_backend(_callback, hotkey="right_option")

    assert isinstance(backend, MacHotkeyListener)
    # An empty hotkey setting must still produce the Wispr-style fn default.
    assert backend.hotkey.key == "right_option"
    assert shortcuts.create_shortcut_backend(_callback).hotkey.key == "fn"


def test_other_platforms_fail_with_advice_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "")
    monkeypatch.setattr(shortcuts, "is_linux", lambda: False)
    monkeypatch.setattr(shortcuts, "is_macos", lambda: False)

    backend = shortcuts.create_shortcut_backend(_callback)
    backend.close()

    with pytest.raises(shortcuts.ShortcutUnavailableError, match="tuxflow toggle"):
        asyncio.run(backend.connect())


def test_the_portal_error_is_a_shortcut_error_so_the_daemon_catches_both():
    pytest.importorskip("dbus_next", reason="the portal backend needs dbus-next")
    from tuxflow.portal import PortalUnavailableError

    assert issubclass(PortalUnavailableError, shortcuts.ShortcutUnavailableError)
