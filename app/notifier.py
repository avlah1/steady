"""Caregiver alerts. Faked as log lines until real messaging (SMS etc.) is wired up.

Emergency services stays simulated even then: software can't reliably reach 911 directly.
A real deployment would route these through a staffed monitoring service that verifies and dispatches.
"""

import logging
from typing import Protocol

from app.models import Contact, ContactKind, Incident

log = logging.getLogger("notifier")


class Notifier(Protocol):
    async def alert(self, contact: Contact, incident: Incident) -> None: ...


class LoggingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []  # (contact_id, incident_id), kept so tests can check

    async def alert(self, contact: Contact, incident: Incident) -> None:
        self.sent.append((contact.id, incident.id))
        if contact.kind == ContactKind.EMERGENCY:
            log.warning("ALERT -> EMERGENCY SERVICES (simulated) (%s): possible fall, incident %s", contact.id, incident.id)
            return
        log.warning("ALERT -> %s (%s): possible fall, incident %s", contact.name, contact.id, incident.id)
