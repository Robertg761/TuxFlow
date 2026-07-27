from __future__ import annotations

import pytest

from tuxflow import notify as notify_module
from tuxflow.notify import notify


@pytest.fixture
def spawned(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(notify_module, "_spawn", commands.append)
    return commands


def test_linux_uses_notify_send(monkeypatch, spawned):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: f"/usr/bin/{name}")

    notify("Dictation failed", "The microphone was busy", urgency="critical")

    assert spawned == [
        [
            "/usr/bin/notify-send",
            "--app-name=TuxFlow",
            "--urgency=critical",
            "--expire-time=2500",
            "Dictation failed",
            "The microphone was busy",
        ]
    ]


def test_macos_prefers_terminal_notifier_and_falls_back_to_osascript(monkeypatch, spawned):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    installed = {"terminal-notifier", "osascript"}
    monkeypatch.setattr(
        notify_module.shutil, "which", lambda name: name if name in installed else None
    )

    notify("Ready", "Hold fn to dictate")
    assert spawned[-1][0] == "terminal-notifier"

    installed.discard("terminal-notifier")
    notify("Ready", "Hold fn to dictate")
    assert spawned[-1][0] == "osascript"
    assert 'display notification "Hold fn to dictate"' in spawned[-1][-1]


def test_quotes_in_a_transcript_cannot_break_the_applescript(monkeypatch, spawned):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    monkeypatch.setattr(
        notify_module.shutil, "which", lambda name: name if name == "osascript" else None
    )

    notify('He said "hello"', 'a back\\slash and a "quote"')

    script = spawned[-1][-1]
    assert '\\"hello\\"' in script
    assert "\\\\slash" in script


def test_a_machine_without_a_notifier_stays_quiet(monkeypatch, spawned):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    monkeypatch.setattr(notify_module.shutil, "which", lambda _name: None)
    notify("Ready")

    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    notify("Ready")

    assert spawned == []
