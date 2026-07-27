from __future__ import annotations

import socket

import pytest

# The control center needs PyGObject and the GTK 4 / libadwaita typelibs, which are
# not present on every machine that runs the rest of the suite.
app = pytest.importorskip("tuxflow.app", reason="PyGObject with GTK 4 and Adw is required")


@pytest.fixture
def socket_path(tmp_path, monkeypatch):
    path = tmp_path / "tuxflow.sock"
    monkeypatch.setattr(app, "socket_file", lambda: path)
    return path


def _listener(path):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    return server


def test_probe_is_false_without_a_socket_file(socket_path):
    assert app._probe_daemon() is False


def test_probe_is_false_for_a_socket_left_behind_by_a_crash(socket_path):
    socket_path.touch()
    assert app._probe_daemon(timeout=0.1) is False


def test_probe_is_true_when_the_daemon_answers(socket_path):
    server = _listener(socket_path)
    try:
        assert app._probe_daemon() is True
    finally:
        server.close()


def test_running_daemon_is_not_respawned(socket_path, monkeypatch):
    server = _listener(socket_path)
    monkeypatch.setattr(
        app.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned a second daemon")
    )
    try:
        assert app._ensure_daemon_running() is True
    finally:
        server.close()


def test_stale_socket_is_replaced_and_the_daemon_respawned(socket_path, monkeypatch):
    socket_path.touch()
    servers = []

    def fake_popen(*_args, **_kwargs):
        assert not socket_path.exists(), "the stale socket must be unlinked before spawning"
        servers.append(_listener(socket_path))
        return object()

    monkeypatch.setattr(app.subprocess, "Popen", fake_popen)
    try:
        assert app._ensure_daemon_running(wait=2.0) is True
        assert len(servers) == 1
    finally:
        for server in servers:
            server.close()


def test_failure_to_spawn_reports_false(socket_path, monkeypatch):
    socket_path.touch()

    def fake_popen(*_args, **_kwargs):
        raise OSError("no such executable")

    monkeypatch.setattr(app.subprocess, "Popen", fake_popen)
    assert app._ensure_daemon_running(wait=0.1) is False
    assert not socket_path.exists()


def test_daemon_that_never_listens_times_out(socket_path, monkeypatch):
    monkeypatch.setattr(app.subprocess, "Popen", lambda *a, **k: object())
    assert app._ensure_daemon_running(wait=0.2) is False


class _Item:
    def __init__(self, identifier: int) -> None:
        self.id = identifier


def test_history_signature_tracks_additions_and_removals():
    first = app._history_signature([_Item(3), _Item(2), _Item(1)])
    assert first == app._history_signature([_Item(3), _Item(2), _Item(1)])
    assert first != app._history_signature([_Item(4), _Item(3), _Item(2), _Item(1)])
    assert first != app._history_signature([])


def test_status_message_covers_the_known_states():
    assert app._status_message("recording", "Super+D")[1] == "Stop and transcribe"
    assert app._status_message("recording", "Super+D")[3] is True
    assert app._status_message("processing", "Super+D")[2] is False
    assert app._status_message("idle", "Super+D")[0] == "Ready — Super+D"
    assert "not running" in app._status_message("offline", "Super+D")[0]


def test_unknown_state_is_named_rather_than_called_ready():
    label, button, sensitive, destructive = app._status_message("starting", "Super+D")
    assert label == "Starting — Super+D"
    assert (button, sensitive, destructive) == ("Start dictation", True, False)


def test_error_text_never_returns_an_empty_string():
    assert app._error_text(RuntimeError("timed out")) == "timed out"
    assert app._error_text(RuntimeError("")) == app.GENERIC_ERROR
    assert app._error_text("   ") == app.GENERIC_ERROR
    assert app._error_text(None) == app.GENERIC_ERROR
