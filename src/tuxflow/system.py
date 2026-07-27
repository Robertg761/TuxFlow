"""Operating-system detection shared by every TuxFlow platform backend.

TuxFlow supports Linux and macOS. Each integration point that differs between
them (recording, clipboard, paste, notifications, global shortcuts, autostart)
asks this module which platform it is on instead of testing ``sys.platform``
in a dozen places.
"""

from __future__ import annotations

import os
import sys

LINUX = "linux"
MACOS = "macos"
UNKNOWN = "unknown"

_LABELS = {LINUX: "Linux", MACOS: "macOS"}


def current_os() -> str:
    """Return ``LINUX``, ``MACOS``, or ``UNKNOWN``.

    ``TUXFLOW_PLATFORM`` overrides the detected value so the platform-specific
    code paths can be exercised from tests and from a foreign machine.
    """
    override = os.environ.get("TUXFLOW_PLATFORM", "").strip().lower()
    if override in {LINUX, MACOS}:
        return override
    if sys.platform.startswith("linux"):
        return LINUX
    if sys.platform == "darwin":
        return MACOS
    return UNKNOWN


def is_linux() -> bool:
    return current_os() == LINUX


def is_macos() -> bool:
    return current_os() == MACOS


def os_label() -> str:
    """Return a human-facing name for the current platform."""
    return _LABELS.get(current_os(), sys.platform)


def unsupported_platform_message(feature: str) -> str:
    return f"{feature} is only supported on Linux and macOS, not {os_label()}"
