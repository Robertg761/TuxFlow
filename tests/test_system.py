from tuxflow import system


def test_platform_override_lets_tests_exercise_the_other_platform(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
    assert system.current_os() == system.MACOS
    assert system.is_macos()
    assert not system.is_linux()
    assert system.os_label() == "macOS"

    monkeypatch.setenv("TUXFLOW_PLATFORM", " LINUX ")
    assert system.current_os() == system.LINUX
    assert system.os_label() == "Linux"


def test_unknown_override_falls_back_to_the_real_platform(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "windows")
    monkeypatch.setattr(system.sys, "platform", "linux")
    assert system.current_os() == system.LINUX

    monkeypatch.setattr(system.sys, "platform", "darwin")
    assert system.current_os() == system.MACOS

    monkeypatch.setattr(system.sys, "platform", "freebsd14")
    assert system.current_os() == system.UNKNOWN
    assert "only supported on Linux and macOS" in system.unsupported_platform_message("Dictation")
