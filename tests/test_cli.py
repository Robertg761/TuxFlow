"""Tests for the control subcommands of the command line."""

from __future__ import annotations

import argparse
import json
import sys
import types

import pytest

from tuxflow import cli
from tuxflow.doctor import Check
from tuxflow.engine import EngineUnavailableError, Transcript


def test_a_control_command_prints_the_daemons_answer(monkeypatch, capsys):
    async def fake_send(command: str) -> dict:
        return {"ok": True, "state": "idle", "command": command}

    monkeypatch.setattr(cli, "send_command", fake_send)

    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "state": "idle", "command": "status"}


def test_a_failing_control_command_prints_a_message_instead_of_a_traceback(monkeypatch, capsys):
    async def fake_send(_command: str) -> dict:
        raise RuntimeError("TuxFlow's background service is not running")

    monkeypatch.setattr(cli, "send_command", fake_send)

    assert cli.main(["toggle"]) == 1
    assert "not running" in capsys.readouterr().err


def test_an_error_with_no_message_still_prints_something(monkeypatch, capsys):
    async def fake_send(_command: str) -> dict:
        raise TimeoutError

    monkeypatch.setattr(cli, "send_command", fake_send)

    assert cli.main(["stop"]) == 1
    printed = capsys.readouterr().err.strip()
    assert printed
    assert "TimeoutError" in printed


@pytest.fixture
def temporary_home(tmp_path, monkeypatch):
    """Keep every command away from the developer's real settings."""
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    return tmp_path


class FakeEngine:
    """A Whisper engine that answers from a script instead of a model."""

    def __init__(self, text: str = "", failure: Exception | None = None) -> None:
        self.text = text
        self.failure = failure
        self.languages: list[str] = []

    def transcribe(self, _path, language):
        self.languages.append(language)
        if self.failure is not None:
            raise self.failure
        return Transcript(text=self.text, language="en", language_probability=0.9)


def _install_engine(monkeypatch, engine: FakeEngine) -> None:
    monkeypatch.setattr(cli, "WhisperEngine", lambda **_options: engine)


def test_transcribing_a_file_that_is_not_there_exits_two(temporary_home, capsys):
    missing = temporary_home / "nowhere.wav"

    assert cli.main(["transcribe", str(missing)]) == 2
    assert str(missing) in capsys.readouterr().err


def test_a_missing_speech_engine_is_explained_instead_of_traced(
    temporary_home, monkeypatch, capsys
):
    _install_engine(
        monkeypatch,
        FakeEngine(failure=EngineUnavailableError("The speech engine is not installed.")),
    )
    audio = temporary_home / "clip.wav"
    audio.write_bytes(b"RIFF")

    assert cli.main(["transcribe", str(audio)]) == 1
    assert "not installed" in capsys.readouterr().err


def test_transcribe_cleans_the_text_up_and_raw_leaves_it_alone(temporary_home, monkeypatch, capsys):
    engine = FakeEngine("um hello comma world period")
    _install_engine(monkeypatch, engine)
    audio = temporary_home / "clip.wav"
    audio.write_bytes(b"RIFF")

    assert cli.main(["transcribe", str(audio)]) == 0
    assert capsys.readouterr().out.strip() == "Hello, world."

    assert cli.main(["transcribe", str(audio), "--raw"]) == 0
    assert capsys.readouterr().out.strip() == "um hello comma world period"
    # The configured language reaches the engine rather than a hard-coded one.
    assert engine.languages == ["auto", "auto"]


def test_doctor_succeeds_when_only_optional_checks_fail(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda: [
            Check("Microphone recorder", True, "pw-record — PipeWire"),
            Check("Background service", False, "Not running", required=False),
        ],
    )

    assert cli.main(["doctor"]) == 0
    printed = capsys.readouterr().out
    assert "✓ Microphone recorder" in printed
    # An optional failure is a warning, never a cross.
    assert "! Background service" in printed


def test_doctor_fails_when_something_required_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda: [Check("Clipboard", False, "Install wl-clipboard")],
    )

    assert cli.main(["doctor"]) == 1
    assert "✗ Clipboard" in capsys.readouterr().out


def test_the_daemon_command_ends_quietly_on_ctrl_c(monkeypatch):
    async def interrupted() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_daemon", interrupted)

    assert cli.main(["daemon"]) == 0


def test_with_no_arguments_the_desktop_app_opens(monkeypatch):
    app = types.ModuleType("tuxflow.app")
    app.run_app = lambda: 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tuxflow.app", app)

    assert cli.main([]) == 0


def test_missing_desktop_dependencies_are_explained_per_platform(monkeypatch, capsys):
    # A GTK import that fails is what a headless or GTK-less machine looks like.
    monkeypatch.setitem(sys.modules, "tuxflow.app", None)

    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    assert cli.main(["app"]) == 1
    assert "python3-gobject" in capsys.readouterr().err

    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    assert cli.main(["app"]) == 1
    assert "brew install" in capsys.readouterr().err


def test_a_command_that_does_not_exist_is_rejected_by_the_parser(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["dictate-everything"])

    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_a_command_main_does_not_handle_exits_two(monkeypatch):
    # Nothing on the command line can reach this today; it is the guard that
    # keeps a subcommand added to the parser alone from silently doing nothing.
    class StubParser:
        def parse_args(self, _argv):
            return argparse.Namespace(command="brand-new")

    monkeypatch.setattr(cli, "_parser", StubParser)

    assert cli.main([]) == 2
