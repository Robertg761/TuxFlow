"""Desktop notification helper."""

from __future__ import annotations

import shutil
import subprocess


def notify(title: str, body: str = "", *, urgency: str = "normal") -> None:
    executable = shutil.which("notify-send")
    if not executable:
        return
    try:
        subprocess.Popen(
            [
                executable,
                "--app-name=TuxFlow",
                f"--urgency={urgency}",
                "--expire-time=2500",
                title,
                body,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass
