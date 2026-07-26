"""Tiny JSON-over-Unix-socket control protocol."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from tuxflow.paths import socket_file


async def send_command(command: str) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.open_unix_connection(socket_file())
    except (ConnectionError, FileNotFoundError, OSError) as error:
        raise RuntimeError(
            "TuxFlow's background service is not running. Start it with: tuxflow daemon"
        ) from error
    writer.write((json.dumps({"command": command}) + "\n").encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.readline(), timeout=3)
    writer.close()
    await writer.wait_closed()
    if not response:
        raise RuntimeError("TuxFlow's background service closed the connection")
    return json.loads(response)
