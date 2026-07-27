"""Tests for the control socket client.

These run a real Unix socket server so the failures under test — a service that
never answers, one that hangs up — are the ones a user actually hits.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tuxflow import ipc


@pytest.fixture
def socket_path(short_sock_dir, monkeypatch):
    path = short_sock_dir / "tuxflow.sock"
    monkeypatch.setattr(ipc, "socket_file", lambda: path)
    return path


def test_a_command_gets_the_services_answer_back(socket_path):
    async def scenario() -> dict:
        async def handle(reader, writer):
            request = json.loads(await reader.readline())
            writer.write((json.dumps({"ok": True, "state": request["command"]}) + "\n").encode())
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        async with server:
            return await ipc.send_command("status")

    assert asyncio.run(scenario()) == {"ok": True, "state": "status"}


def test_a_service_that_never_answers_fails_readably_and_hangs_up(socket_path, monkeypatch):
    monkeypatch.setattr(ipc, "RESPONSE_TIMEOUT_SECONDS", 0.1)
    hung_up = asyncio.Event()

    async def scenario() -> RuntimeError:
        async def handle(reader, writer):
            # Accept the command and answer nothing, then wait for the client
            # to close, which is what the timeout path has to do.
            await reader.read()
            hung_up.set()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        async with server:
            with pytest.raises(RuntimeError) as caught:
                await ipc.send_command("toggle")
            await asyncio.wait_for(hung_up.wait(), timeout=2)
        return caught.value

    error = asyncio.run(scenario())

    # A bare TimeoutError stringifies to "", which would blank the app's status line.
    assert str(error)
    assert "toggle" in str(error)
    assert not isinstance(error, TimeoutError)


def test_a_service_that_hangs_up_without_answering_is_reported(socket_path):
    async def scenario() -> RuntimeError:
        async def handle(reader, writer):
            await reader.readline()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        async with server:
            with pytest.raises(RuntimeError) as caught:
                await ipc.send_command("status")
        return caught.value

    assert "closed the connection" in str(asyncio.run(scenario()))


def test_no_service_at_all_says_how_to_start_one(socket_path):
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(ipc.send_command("status"))

    assert "tuxflow daemon" in str(caught.value)


def test_a_reply_that_is_not_json_is_reported_readably(socket_path):
    async def scenario() -> RuntimeError:
        async def handle(reader, writer):
            await reader.readline()
            # A half-upgraded install, or something else listening on the path.
            writer.write(b"not json at all\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        async with server:
            with pytest.raises(RuntimeError) as caught:
                await ipc.send_command("status")
        return caught.value

    assert "could not read" in str(asyncio.run(scenario()))
