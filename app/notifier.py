"""Caregiver alerts. Faked as log lines until real messaging (SMS etc.) is wired up."""

import logging
from typing import Protocol

from app.models import Contact, Incident

log = logging.getLogger("notifier")


class Notifier(Protocol):
    async def alert(self, contact: Contact, incident: Incident) -> None: ...


class LoggingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []  # (contact_id, incident_id), kept so tests can check

    async def alert(self, contact: Contact, incident: Incident) -> None:
        self.sent.append((contact.id, incident.id))
        log.warning("ALERT -> %s (%s): possible fall, incident %s", contact.name, contact.id, incident.id)
