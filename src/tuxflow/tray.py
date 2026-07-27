"""Tray status for the background daemon.

The presentation of each daemon state lives here and is platform neutral. The
only implementation today is the Linux StatusNotifierItem in
:mod:`tuxflow.tray_sni`; on macOS the daemon reports state through
notifications instead, so the indicator quietly does nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tuxflow import APP_ID
from tuxflow.system import is_linux


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


class TrayIndicator:
    """Show daemon state in the tray, where the platform has one."""

    def __init__(self) -> None:
        self._backend: Any | None = None

    @property
    def active(self) -> bool:
        return self._backend is not None

    async def start(self) -> bool:
        if not is_linux():
            return False
        try:
            from tuxflow.tray_sni import StatusNotifierTray
        except ImportError:
            return False
        backend = StatusNotifierTray()
        if not await backend.start():
            return False
        self._backend = backend
        return True

    def update(self, state: str, detail: str = "") -> None:
        if self._backend is not None:
            self._backend.update(state, detail)

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
