"""Configuration storage with safe defaults and atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tuxflow.paths import config_file, ensure_directories


@dataclass(slots=True)
class Replacement:
    spoken: str
    written: str


@dataclass(slots=True)
class Snippet:
    trigger: str
    expansion: str


@dataclass(slots=True)
class Settings:
    model: str = "small"
    language: str = "auto"
    device: str = "cpu"
    compute_type: str = "int8"
    # Empty means "whatever the recorder treats as the default microphone".
    audio_device: str = ""
    # Which modifier to hold on macOS; ignored on Linux, where the desktop
    # portal owns the binding. See tuxflow.mac_hotkey.HOTKEYS.
    macos_hotkey: str = "fn"
    auto_paste: bool = True
    remove_fillers: bool = True
    spoken_punctuation: bool = True
    press_enter_command: bool = False
    keep_audio: bool = False
    launch_at_login: bool = True
    dictionary: list[Replacement] = field(default_factory=list)
    snippets: list[Snippet] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Settings:
        known = {
            key: raw[key]
            for key in cls.__dataclass_fields__
            if key in raw and key not in {"dictionary", "snippets"}
        }
        known["dictionary"] = [
            Replacement(str(item["spoken"]), str(item["written"]))
            for item in raw.get("dictionary", [])
            if isinstance(item, dict) and "spoken" in item and "written" in item
        ]
        known["snippets"] = [
            Snippet(str(item["trigger"]), str(item["expansion"]))
            for item in raw.get("snippets", [])
            if isinstance(item, dict) and "trigger" in item and "expansion" in item
        ]
        return cls(**known)


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_file()

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return Settings.from_dict(raw) if isinstance(raw, dict) else Settings()
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return Settings()

    def save(self, settings: Settings) -> None:
        ensure_directories()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
