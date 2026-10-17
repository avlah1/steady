"""The voice device (ESP32-S3-BOX-3). Faked as log lines until the hardware is ready."""

import logging
from typing import Protocol

log = logging.getLogger("box")


class Box(Protocol):
    async def say(self, incident_id: str, text: str) -> None: ...


class LoggingBox:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []  # kept so tests can check what was spoken

    async def say(self, incident_id: str, text: str) -> None:
        self.said.append((incident_id, text))
        log.info("BOX SAYS [%s]: %s", incident_id, text)
