"""Tests for `tuxflow doctor`.

The checks are what a user is told to run when nothing works, so each one is
exercised in both directions: the tool is there, and the tool is missing.
"""

from __future__ import annotations

import socket as socket_module

import pytest

from tuxflow import __version__, audio, doctor
from tuxflow.config import ConfigStore, Settings


@pytest.fixture
def temporary_home(tmp_path, monkeypatch):
    for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    return tmp_path


@pytest.fixture
def nothing_installed(monkeypatch):
    """A machine with none of the optional command-line helpers."""
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    monkeypatch.setattr(doctor, "select_backend", lambda: None)
    monkeypatch.setattr(doctor, "clipboard_tool", lambda: None)
    monkeypatch.setattr(doctor, "ydotool_can_start", lambda: False)


def _named(checks: list[doctor.Check]) -> dict[str, doctor.Check]:
    return {check.name: check for check in checks}


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def test_the_recorder_check_names_the_backend_it_found(monkeypatch):
    monkeypatch.setattr(doctor, "select_backend", lambda: audio.PIPEWIRE)

    check = doctor._recorder_check()

    assert check.ok
    assert "pw-record" in check.detail
    assert "PipeWire" in check.detail


def test_a_machine_with_no_recorder_is_told_what_to_install(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    monkeypatch.setattr(doctor, "select_backend", lambda: None)

    check = doctor._recorder_check()

    assert not check.ok
    assert "pw-record" in check.detail
    assert "pipewire" in check.detail.lower()


def test_the_engine_check_points_at_the_installer_when_whisper_is_absent(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)

    check = doctor._engine_check()

    assert not check.ok
    assert "install.sh" in check.detail


def test_the_engine_check_passes_once_faster_whisper_is_importable(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())

    assert doctor._engine_check().ok


def test_the_clipboard_check_names_the_tool_it_would_use(monkeypatch):
    monkeypatch.setattr(doctor, "clipboard_tool", lambda: "wl-copy")

    check = doctor._clipboard_check()

    assert check.ok
    assert check.detail == "wl-copy"


def test_a_linux_desktop_without_a_clipboard_tool_is_told_which_to_install(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(doctor, "clipboard_tool", lambda: None)

    check = doctor._clipboard_check()

    assert not check.ok
    assert "wl-clipboard" in check.detail
    assert "wayland" in check.detail


def test_a_mac_without_pbcopy_gets_a_mac_answer(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    monkeypatch.setattr(doctor, "clipboard_tool", lambda: None)

    assert "pbcopy" in doctor._clipboard_check().detail


# --------------------------------------------------------------------------- #
# The background service
# --------------------------------------------------------------------------- #


def test_a_listening_service_is_found_at_its_socket(tmp_path, monkeypatch):
    path = tmp_path / "tuxflow.sock"
    monkeypatch.setattr(doctor, "socket_file", lambda: path)
    server = socket_module.socket(socket_module.AF_UNIX)
    server.bind(str(path))
    server.listen(1)
    try:
        check = doctor._service_check()
    finally:
        server.close()

    assert check.ok
    assert check.detail == str(path)
    # The service not running is a normal state, not a broken install.
    assert check.required is False


def test_a_socket_left_behind_by_a_dead_service_is_not_a_running_service(tmp_path, monkeypatch):
    path = tmp_path / "tuxflow.sock"
    # A crash leaves the file with nothing listening on the other end.
    path.write_bytes(b"")
    monkeypatch.setattr(doctor, "socket_file", lambda: path)

    check = doctor._service_check()

    assert not check.ok
    assert "tuxflow daemon" in check.detail


def test_no_socket_at_all_means_no_service(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "socket_file", lambda: tmp_path / "absent.sock")

    assert not doctor._service_check().ok


# --------------------------------------------------------------------------- #
# The platform check lists
# --------------------------------------------------------------------------- #


def test_the_linux_checks_cover_the_whole_desktop_integration(
    monkeypatch, temporary_home, nothing_installed
):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")

    checks = _named(doctor.run_checks())

    assert list(checks) == [
        "Microphone recorder",
        "Whisper engine",
        "Desktop portal",
        "Clipboard",
        "Automatic paste",
        "Background service",
    ]
    assert checks["Desktop portal"].ok
    assert "wayland" in checks["Desktop portal"].detail
    # Dictation without automatic paste still works, so it must not fail doctor.
    assert checks["Automatic paste"].required is False
    assert checks["Microphone recorder"].required is True
    assert all(check.detail for check in checks.values())


def test_a_session_without_a_message_bus_cannot_reach_the_portal(
    monkeypatch, temporary_home, nothing_installed
):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    portal = _named(doctor.run_checks())["Desktop portal"]

    assert not portal.ok
    assert "graphical desktop session" in portal.detail


def test_wtype_alone_is_enough_for_automatic_paste(monkeypatch, temporary_home, nothing_installed):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/wtype" if name else None)

    assert _named(doctor.run_checks())["Automatic paste"].ok


def test_the_mac_checks_cover_the_permissions_a_mac_needs(
    monkeypatch, temporary_home, nothing_installed
):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")

    checks = _named(doctor.run_checks())

    assert list(checks) == [
        "Microphone recorder",
        "Whisper engine",
        "Global hotkey support",
        "Accessibility permission",
        "Clipboard",
        "Automatic paste",
        "Background service",
        # The default hotkey is fn, which needs a keyboard setting changed.
        "Keyboard setting",
    ]
    assert checks["Automatic paste"].ok is False
    assert "osascript" in checks["Automatic paste"].detail
    assert checks["Keyboard setting"].required is False
    assert all(check.detail for check in checks.values())


def test_a_hotkey_that_is_not_fn_needs_no_keyboard_advice(
    monkeypatch, temporary_home, nothing_installed
):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    ConfigStore().save(Settings(macos_hotkey="right_option"))

    assert "Keyboard setting" not in _named(doctor.run_checks())


def test_an_operating_system_tuxflow_does_not_support_says_so(monkeypatch):
    monkeypatch.setattr(doctor, "is_macos", lambda: False)
    monkeypatch.setattr(doctor, "is_linux", lambda: False)

    checks = doctor.run_checks()

    assert len(checks) == 1
    assert not checks[0].ok
    assert "only supported on Linux and macOS" in checks[0].detail


def test_the_summary_carries_the_version_a_bug_report_needs(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "linux")

    assert doctor.platform_summary() == f"TuxFlow {__version__} on Linux"
