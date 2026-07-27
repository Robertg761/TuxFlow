"""Tiny JSON-over-Unix-socket control protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from tuxflow.paths import socket_file

RESPONSE_TIMEOUT_SECONDS = 3.0


async def send_command(command: str) -> dict[str, Any]:
    """Send one command to the running daemon and return its status reply.

    Every failure leaves as a :class:`RuntimeError` carrying a sentence a person
    can read: callers print it, and the desktop app puts it in the status line,
    where a bare ``TimeoutError`` would render as an empty string.
    """
    try:
        reader, writer = await asyncio.open_unix_connection(socket_file())
    except (ConnectionError, FileNotFoundError, OSError) as error:
        raise RuntimeError(
            "TuxFlow's background service is not running. Start it with: tuxflow daemon"
        ) from error
    try:
        writer.write((json.dumps({"command": command}) + "\n").encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), timeout=RESPONSE_TIMEOUT_SECONDS)
    # TimeoutError subclasses OSError, so it has to be caught before it.
    except TimeoutError as error:
        raise RuntimeError(
            f"TuxFlow's background service did not answer `{command}` within "
            f"{int(RESPONSE_TIMEOUT_SECONDS)} seconds"
        ) from error
    except (ConnectionError, OSError) as error:
        detail = str(error) or type(error).__name__
        message = f"Lost the connection to TuxFlow's background service: {detail}"
        raise RuntimeError(message) from error
    finally:
        writer.close()
        # A half-broken transport can refuse to finish closing; the reply, or
        # the error about it, matters more than a tidy shutdown.
        with contextlib.suppress(ConnectionError, OSError):
            await asyncio.wait_for(writer.wait_closed(), timeout=1)
    if not response:
        raise RuntimeError("TuxFlow's background service closed the connection")
    try:
        return json.loads(response)
    except json.JSONDecodeError as error:
        message = "TuxFlow's background service sent a reply TuxFlow could not read"
        raise RuntimeError(message) from error
