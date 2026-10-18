"""The voice device (ESP32-S3-BOX-3).

WebSocketBox sends to the real Box over WS /ws/box. LoggingBox is the fake used in tests.
"""

import asyncio
import logging
from typing import Protocol

from fastapi import WebSocket

log = logging.getLogger("box")


class Box(Protocol):
    async def say(self, incident_id: str, text: str) -> None: ...


class LoggingBox:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []  # kept so tests can check what was spoken

    async def say(self, incident_id: str, text: str) -> None:
        self.said.append((incident_id, text))
        log.info("BOX SAYS [%s]: %s", incident_id, text)


class WebSocketBox:
    """Sends "say" messages to every connected Box.

    Usually there's one, but a Box that reconnects may briefly overlap with its old, dying
    connection; sending to all of them means the live one always gets the message.
    """

    def __init__(self, send_timeout_s: float = 2.0) -> None:
        self.send_timeout_s = send_timeout_s
        self.connections: set[WebSocket] = set()

    def connect(self, ws: WebSocket) -> None:
        self.connections.add(ws)
        log.info("Box connected (%d connected)", len(self.connections))

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)
        log.info("Box disconnected (%d connected)", len(self.connections))

    async def say(self, incident_id: str, text: str) -> None:
        # The log line stays even with real hardware, so the demo laptop shows what the Box was told.
        log.info("BOX SAYS [%s]: %s", incident_id, text)
        if not self.connections:
            # Don't fail: the reply timer is already running, so with no Box the incident still escalates.
            log.warning("Box not connected; incident %s continues without it", incident_id)
            return
        message = {"type": "say", "incident_id": incident_id, "text": text}
        for ws in list(self.connections):
            try:
                # Timeout because handle() waits on this while holding the incident's lock:
                # a half-dead Wi-Fi connection must not freeze the incident.
                await asyncio.wait_for(ws.send_json(message), self.send_timeout_s)
            except Exception:
                log.exception("Send to Box failed; dropping that connection")
                self.disconnect(ws)
