from __future__ import annotations

import asyncio

import pytest

# The desktop portal is Linux-only, and so is dbus-next.
pytest.importorskip("dbus_next", reason="the global shortcuts portal needs dbus-next")

from dbus_next import Variant
from dbus_next.constants import MessageType
from dbus_next.errors import DBusError

import tuxflow.portal as portal_module
from tuxflow import APP_ID
from tuxflow.portal import (
    PORTAL_INTERFACE,
    PREFERRED_TRIGGER,
    REGISTRY_INTERFACE,
    GlobalShortcutsPortal,
    PortalUnavailableError,
)


def test_global_shortcut_connect_avoids_broad_portal_introspection(monkeypatch):
    callbacks: list[tuple[str, str]] = []
    portal: GlobalShortcutsPortal

    async def press_callback(shortcut_id: str) -> None:
        callbacks.append(("pressed", shortcut_id))

    async def release_callback(shortcut_id: str) -> None:
        callbacks.append(("released", shortcut_id))

    class FakeInterface:
        activated_callback = None
        deactivated_callback = None

        async def call_create_session(self, options):
            assert options["handle_token"].signature == "s"
            path = "/org/freedesktop/portal/desktop/request/test/create"
            portal._responses[path] = (
                0,
                {"session_handle": "/org/freedesktop/portal/desktop/session/test/tuxflow"},
            )
            return path

        async def call_bind_shortcuts(self, session_handle, shortcuts, parent_window, options):
            assert session_handle.endswith("/tuxflow")
            assert shortcuts[0][0] == "toggle-dictation"
            assert shortcuts[0][1]["preferred_trigger"].value == PREFERRED_TRIGGER
            assert parent_window == ""
            assert options["handle_token"].signature == "s"
            path = "/org/freedesktop/portal/desktop/request/test/bind"
            portal._responses[path] = (
                0,
                {
                    "shortcuts": [
                        [
                            "toggle-dictation",
                            {"trigger_description": "Ctrl+Meta+Space"},
                        ]
                    ]
                },
            )
            return path

        def on_activated(self, callback):
            self.activated_callback = callback

        def on_deactivated(self, callback):
            self.deactivated_callback = callback

    class FakeRegistry:
        async def call_register(self, app_id, options):
            assert app_id == APP_ID
            assert options == {}

    class FakeProxy:
        def __init__(self):
            self.interface = FakeInterface()
            self.registry = FakeRegistry()

        def get_interface(self, name):
            if name == PORTAL_INTERFACE:
                return self.interface
            assert name == REGISTRY_INTERFACE
            return self.registry

    class FakeBus:
        def __init__(self):
            self.proxy = FakeProxy()
            self.message_handler = None

        async def connect(self):
            return self

        def add_message_handler(self, handler):
            self.message_handler = handler

        async def introspect(self, *_args):
            raise AssertionError("broad portal introspection must not be used")

        def get_proxy_object(self, service, path, introspection):
            assert service == "org.freedesktop.portal.Desktop"
            assert path == "/org/freedesktop/portal/desktop"
            assert [item.name for item in introspection.interfaces] == [
                REGISTRY_INTERFACE,
                PORTAL_INTERFACE,
            ]
            return self.proxy

        def disconnect(self):
            pass

    fake_bus = FakeBus()
    monkeypatch.setattr(portal_module, "MessageBus", lambda: fake_bus)
    portal = GlobalShortcutsPortal(press_callback, release_callback)

    async def run_scenario() -> None:
        assert await portal.connect() == "Ctrl+Meta+Space"
        fake_bus.proxy.interface.activated_callback(
            portal.session_handle,
            "toggle-dictation",
            0,
            {},
        )
        fake_bus.proxy.interface.deactivated_callback(
            portal.session_handle,
            "toggle-dictation",
            1,
            {},
        )
        await asyncio.sleep(0)

    asyncio.run(run_scenario())
    assert callbacks == [
        ("pressed", "toggle-dictation"),
        ("released", "toggle-dictation"),
    ]


def test_host_registry_can_be_absent_on_future_portal_versions():
    class MissingRegistry:
        async def call_register(self, _app_id, _options):
            raise DBusError("org.freedesktop.DBus.Error.UnknownMethod", "not available")

    async def callback(_shortcut_id: str) -> None:
        pass

    portal = GlobalShortcutsPortal(callback)
    asyncio.run(portal._register_host_app(MissingRegistry()))


def test_empty_binding_response_is_not_reported_as_configured(monkeypatch):
    portal: GlobalShortcutsPortal

    class FakeInterface:
        async def call_create_session(self, _options):
            path = "/org/freedesktop/portal/desktop/request/test/create"
            portal._responses[path] = (
                0,
                {"session_handle": "/org/freedesktop/portal/desktop/session/test/tuxflow"},
            )
            return path

        async def call_bind_shortcuts(self, *_args):
            path = "/org/freedesktop/portal/desktop/request/test/bind"
            portal._responses[path] = (
                0,
                {
                    "shortcuts": [
                        [
                            "toggle-dictation",
                            {"trigger_description": ""},
                        ]
                    ]
                },
            )
            return path

        def on_activated(self, _callback):
            raise AssertionError("an unbound shortcut must not be activated")

        def on_deactivated(self, _callback):
            raise AssertionError("an unbound shortcut must not be deactivated")

    class FakeRegistry:
        async def call_register(self, _app_id, _options):
            pass

    class FakeProxy:
        def get_interface(self, name):
            return FakeRegistry() if name == REGISTRY_INTERFACE else FakeInterface()

    class FakeBus:
        async def connect(self):
            return self

        def add_message_handler(self, _handler):
            pass

        def get_proxy_object(self, *_args):
            return FakeProxy()

    fake_bus = FakeBus()
    monkeypatch.setattr(portal_module, "MessageBus", lambda: fake_bus)

    async def callback(_shortcut_id: str) -> None:
        pass

    portal = GlobalShortcutsPortal(callback)
    try:
        asyncio.run(portal.connect())
    except PortalUnavailableError as error:
        assert "no key binding" in str(error)
    else:
        raise AssertionError("empty bindings must fail portal setup")


class FakeSignal:
    """A D-Bus Response signal as dbus-next hands it to the message handler."""

    def __init__(self, path: str, body: list, member: str = "Response") -> None:
        self.message_type = MessageType.SIGNAL
        self.interface = "org.freedesktop.portal.Request"
        self.member = member
        self.path = path
        self.body = body


def _portal() -> GlobalShortcutsPortal:
    async def callback(_shortcut_id: str) -> None:
        pass

    return GlobalShortcutsPortal(callback)


def test_an_answer_that_arrives_while_waiting_wakes_the_waiter():
    portal = _portal()
    path = "/org/freedesktop/portal/desktop/request/test/bind"

    async def scenario() -> dict:
        waiting = asyncio.create_task(portal._wait_response(path))
        await asyncio.sleep(0)
        portal._message_handler(
            FakeSignal(
                path,
                [
                    0,
                    {
                        "shortcuts": Variant(
                            "a(sa{sv})",
                            [["toggle-dictation", {"trigger_description": Variant("s", "Ctrl+D")}]],
                        )
                    },
                ],
            )
        )
        return await waiting

    results = asyncio.run(scenario())

    # Every layer of D-Bus variant is unwrapped before TuxFlow sees it.
    assert results == {"shortcuts": [["toggle-dictation", {"trigger_description": "Ctrl+D"}]]}
    assert portal._waiters == {}


def test_signals_from_other_interfaces_are_left_alone():
    portal = _portal()

    assert portal._message_handler(FakeSignal("/other", [0, {}], member="Closed")) is False
    assert portal._responses == {}


def test_a_shortcut_the_user_refuses_is_reported_in_plain_words():
    portal = _portal()
    portal._responses["/request/cancelled"] = (1, {})

    with pytest.raises(PortalUnavailableError, match="cancelled"):
        asyncio.run(portal._wait_response("/request/cancelled"))


def test_a_portal_that_rejects_the_request_is_reported_too():
    portal = _portal()
    portal._responses["/request/failed"] = (2, {})

    with pytest.raises(PortalUnavailableError, match="failed"):
        asyncio.run(portal._wait_response("/request/failed"))


def test_a_dialog_nobody_answers_gives_up_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(portal_module, "PORTAL_RESPONSE_TIMEOUT_SECONDS", 0.05)
    portal = _portal()

    with pytest.raises(PortalUnavailableError, match="Timed out"):
        asyncio.run(portal._wait_response("/request/ignored"))

    # A waiter left in the map would leak for the life of the daemon.
    assert portal._waiters == {}


def test_closing_the_portal_drops_the_bus():
    portal = _portal()
    closed: list[bool] = []

    class FakeBus:
        def disconnect(self):
            closed.append(True)

    portal.bus = FakeBus()
    portal.close()

    assert closed == [True]
    assert portal.bus is None
