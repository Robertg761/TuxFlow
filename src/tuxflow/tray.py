"""KDE-compatible system tray status for the background daemon."""

# dbus-next intentionally uses D-Bus type signatures as string annotations.
# ruff: noqa: F821

import os
import subprocess
import sys
from dataclasses import dataclass

from dbus_next import Message
from dbus_next.aio import MessageBus
from dbus_next.constants import MessageType, PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method, signal

from tuxflow import APP_ID

WATCHER_SERVICE = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_INTERFACE = "org.kde.StatusNotifierWatcher"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
ITEM_PATH = "/StatusNotifierItem"


@dataclass(frozen=True)
class TrayPresentation:
    icon_name: str
    title: str
    description: str
    status: str


def presentation_for_state(state: str, detail: str = "") -> TrayPresentation:
    """Return the desktop-facing representation of a daemon state."""
    if state == "recording":
        return TrayPresentation(
            icon_name="media-record",
            title="TuxFlow — Recording",
            description="Listening… release the shortcut to transcribe",
            status="NeedsAttention",
        )
    if state == "processing":
        return TrayPresentation(
            icon_name="view-refresh",
            title="TuxFlow — Transcribing",
            description="Whisper is transcribing locally",
            status="Active",
        )
    if state == "error":
        return TrayPresentation(
            icon_name="dialog-error",
            title="TuxFlow — Error",
            description=detail or "TuxFlow needs attention",
            status="NeedsAttention",
        )
    return TrayPresentation(
        icon_name=APP_ID,
        title="TuxFlow — Ready",
        description="Ready — hold your shortcut to dictate",
        status="Active",
    )


class _StatusNotifierItem(ServiceInterface):
    def __init__(self) -> None:
        super().__init__(ITEM_INTERFACE)
        self.presentation = presentation_for_state("idle")

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return "ApplicationStatus"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return APP_ID

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return self.presentation.title

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return self.presentation.status

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "i":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> "o":
        return "/"

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return self.presentation.icon_name

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconName(self) -> "s":
        return self.presentation.icon_name

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> "(sa(iiay)ss)":
        return [
            self.presentation.icon_name,
            [],
            self.presentation.title,
            self.presentation.description,
        ]

    @method()
    def Activate(self, _x: "i", _y: "i") -> None:
        self._open_app()

    @method()
    def SecondaryActivate(self, _x: "i", _y: "i") -> None:
        self._open_app()

    @method()
    def ContextMenu(self, _x: "i", _y: "i") -> None:
        self._open_app()

    @method()
    def Scroll(self, _delta: "i", _orientation: "s") -> None:
        pass

    @signal()
    def NewTitle(self):
        pass

    @signal()
    def NewIcon(self):
        pass

    @signal()
    def NewAttentionIcon(self):
        pass

    @signal()
    def NewToolTip(self):
        pass

    @signal()
    def NewStatus(self, status: "s") -> "s":
        return status

    def set_state(self, state: str, detail: str = "") -> None:
        presentation = presentation_for_state(state, detail)
        if presentation == self.presentation:
            return
        self.presentation = presentation
        self.emit_properties_changed(
            {
                "Title": presentation.title,
                "Status": presentation.status,
                "IconName": presentation.icon_name,
                "AttentionIconName": presentation.icon_name,
                "ToolTip": [
                    presentation.icon_name,
                    [],
                    presentation.title,
                    presentation.description,
                ],
            }
        )
        self.NewTitle()
        self.NewIcon()
        self.NewAttentionIcon()
        self.NewToolTip()
        self.NewStatus(presentation.status)

    @staticmethod
    def _open_app() -> None:
        subprocess.Popen(
            [sys.executable, "-m", "tuxflow", "app"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


class TrayIndicator:
    """Own a StatusNotifierItem without making it a daemon dependency."""

    def __init__(self) -> None:
        self.bus: MessageBus | None = None
        self.item = _StatusNotifierItem()
        self.service_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

    async def start(self) -> bool:
        try:
            self.bus = await MessageBus().connect()
            self.bus.export(ITEM_PATH, self.item)
            await self.bus.request_name(self.service_name)
            reply = await self.bus.call(
                Message(
                    destination=WATCHER_SERVICE,
                    path=WATCHER_PATH,
                    interface=WATCHER_INTERFACE,
                    member="RegisterStatusNotifierItem",
                    signature="s",
                    body=[self.service_name],
                )
            )
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(reply.error_name or "tray registration failed")
            return True
        except Exception:
            self.close()
            return False

    def update(self, state: str, detail: str = "") -> None:
        self.item.set_state(state, detail)

    def close(self) -> None:
        if self.bus:
            self.bus.disconnect()
            self.bus = None
