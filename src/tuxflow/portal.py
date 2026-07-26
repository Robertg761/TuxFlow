"""XDG GlobalShortcuts portal integration for KDE, GNOME, and other desktops."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import MessageType
from dbus_next.errors import DBusError
from dbus_next.introspection import Node

from tuxflow import APP_ID

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REGISTRY_INTERFACE = "org.freedesktop.host.portal.Registry"
PORTAL_RESPONSE_TIMEOUT_SECONDS = 30
PREFERRED_TRIGGER = "CTRL+LOGO+Space"

PORTAL_INTROSPECTION = Node.parse(
    """
    <node>
      <interface name="org.freedesktop.host.portal.Registry">
        <method name="Register">
          <arg name="app_id" type="s" direction="in"/>
          <arg name="options" type="a{sv}" direction="in"/>
        </method>
      </interface>
      <interface name="org.freedesktop.portal.GlobalShortcuts">
        <method name="CreateSession">
          <arg name="options" type="a{sv}" direction="in"/>
          <arg name="handle" type="o" direction="out"/>
        </method>
        <method name="BindShortcuts">
          <arg name="session_handle" type="o" direction="in"/>
          <arg name="shortcuts" type="a(sa{sv})" direction="in"/>
          <arg name="parent_window" type="s" direction="in"/>
          <arg name="options" type="a{sv}" direction="in"/>
          <arg name="request_handle" type="o" direction="out"/>
        </method>
        <signal name="Activated">
          <arg name="session_handle" type="o"/>
          <arg name="shortcut_id" type="s"/>
          <arg name="timestamp" type="t"/>
          <arg name="options" type="a{sv}"/>
        </signal>
        <signal name="Deactivated">
          <arg name="session_handle" type="o"/>
          <arg name="shortcut_id" type="s"/>
          <arg name="timestamp" type="t"/>
          <arg name="options" type="a{sv}"/>
        </signal>
      </interface>
    </node>
    """
)


class PortalUnavailableError(RuntimeError):
    pass


def _unpack(value: Any) -> Any:
    if isinstance(value, Variant):
        return _unpack(value.value)
    if isinstance(value, dict):
        return {key: _unpack(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_unpack(item) for item in value)
    return value


class GlobalShortcutsPortal:
    def __init__(
        self,
        press_callback: Callable[[str], Awaitable[None]],
        release_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.press_callback = press_callback
        self.release_callback = release_callback
        self.bus: MessageBus | None = None
        self.session_handle: str | None = None
        self.bound_shortcut = ""
        self._responses: dict[str, tuple[int, dict[str, Any]]] = {}
        self._waiters: dict[str, asyncio.Future[tuple[int, dict[str, Any]]]] = {}

    def _message_handler(self, message: Any) -> bool:
        if (
            message.message_type == MessageType.SIGNAL
            and message.interface == "org.freedesktop.portal.Request"
            and message.member == "Response"
            and message.path
        ):
            response = int(message.body[0])
            results = _unpack(message.body[1])
            future = self._waiters.pop(message.path, None)
            if future and not future.done():
                future.set_result((response, results))
            else:
                self._responses[message.path] = (response, results)
        return False

    async def _wait_response(self, request_path: str) -> dict[str, Any]:
        cached = self._responses.pop(request_path, None)
        if cached is not None:
            response, results = cached
        else:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._waiters[request_path] = future
            try:
                response, results = await asyncio.wait_for(
                    future, timeout=PORTAL_RESPONSE_TIMEOUT_SECONDS
                )
            except TimeoutError as error:
                raise PortalUnavailableError(
                    "Timed out waiting for global shortcut approval"
                ) from error
            finally:
                self._waiters.pop(request_path, None)
        if response != 0:
            raise PortalUnavailableError(
                "Global shortcut setup was cancelled"
                if response == 1
                else "Global shortcut setup failed"
            )
        return results

    async def connect(self) -> str:
        try:
            self.bus = await MessageBus().connect()
            self.bus.add_message_handler(self._message_handler)
            # Do not introspect the entire portal root. Some portal versions expose
            # unrelated, non-standard members that strict D-Bus clients reject. A
            # narrow static interface keeps TuxFlow isolated from those interfaces.
            proxy = self.bus.get_proxy_object(
                PORTAL_SERVICE,
                PORTAL_PATH,
                PORTAL_INTROSPECTION,
            )
            registry = proxy.get_interface(REGISTRY_INTERFACE)
            interface = proxy.get_interface(PORTAL_INTERFACE)
            await self._register_host_app(registry)

            session_token = "tuxflow_" + uuid.uuid4().hex
            request_token = "tuxflow_create_" + uuid.uuid4().hex
            request_path = await interface.call_create_session(
                {
                    "handle_token": Variant("s", request_token),
                    "session_handle_token": Variant("s", session_token),
                }
            )
            results = await self._wait_response(request_path)
            self.session_handle = str(results["session_handle"])

            bind_token = "tuxflow_bind_" + uuid.uuid4().hex
            shortcuts = [
                [
                    "toggle-dictation",
                    {
                        "description": Variant("s", "Hold to dictate; release to transcribe"),
                        "preferred_trigger": Variant("s", PREFERRED_TRIGGER),
                    },
                ]
            ]
            request_path = await interface.call_bind_shortcuts(
                self.session_handle,
                shortcuts,
                "",
                {"handle_token": Variant("s", bind_token)},
            )
            bind_results = await self._wait_response(request_path)
            bound = bind_results.get("shortcuts", [])
            if not bound:
                raise PortalUnavailableError(
                    "The global shortcut was approved but no key binding was configured"
                )
            details = bound[0][1]
            self.bound_shortcut = str(details.get("trigger_description", "")).strip()
            if not self.bound_shortcut:
                raise PortalUnavailableError(
                    "The global shortcut was approved but no key binding was configured"
                )
            interface.on_activated(self._activated)
            interface.on_deactivated(self._deactivated)
            return self.bound_shortcut
        except PortalUnavailableError:
            raise
        except Exception as error:
            raise PortalUnavailableError(
                f"Global shortcuts portal is unavailable: {error}"
            ) from error

    async def _register_host_app(self, registry: Any) -> None:
        try:
            await registry.call_register(APP_ID, {})
        except DBusError as error:
            unsupported = error.type in {
                "org.freedesktop.DBus.Error.UnknownInterface",
                "org.freedesktop.DBus.Error.UnknownMethod",
            }
            already_registered = "already associated" in error.text.lower()
            if not unsupported and not already_registered:
                raise

    def _activated(
        self, session_handle: str, shortcut_id: str, _timestamp: int, _options: dict
    ) -> None:
        if session_handle == self.session_handle and shortcut_id == "toggle-dictation":
            asyncio.create_task(self.press_callback(shortcut_id))

    def _deactivated(
        self, session_handle: str, shortcut_id: str, _timestamp: int, _options: dict
    ) -> None:
        if (
            self.release_callback
            and session_handle == self.session_handle
            and shortcut_id == "toggle-dictation"
        ):
            asyncio.create_task(self.release_callback(shortcut_id))

    def close(self) -> None:
        if self.bus:
            self.bus.disconnect()
            self.bus = None
