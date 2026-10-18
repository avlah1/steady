"""Caregiver alerts. TelegramNotifier messages caregivers; LoggingNotifier only logs (tests, no bot token).

Emergency services stays simulated either way: software can't reliably reach 911 directly.
A real deployment would route these through a staffed monitoring service that verifies and dispatches.
"""

import asyncio
import logging
from typing import Protocol

from app.models import Contact, ContactKind, Incident, IncidentState, Trigger
from app.telegram import TelegramClient

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


class TelegramNotifier(LoggingNotifier):
    """Messages each caregiver's linked Telegram chat, and still logs every alert like LoggingNotifier.

    Sends run in the background. handle() holds the incident's lock while alerting, so waiting on
    Telegram would delay the Box and any ack. A failed send is logged, never retried: the ack
    timer is already running, so the next contact is still alerted on time.
    Contacts with no linked chat, and emergency services, only get the log line.
    """

    def __init__(self, client: TelegramClient) -> None:
        super().__init__()
        self.client = client
        # Running sends. asyncio only keeps weak references to tasks, so without this one could vanish mid-send.
        self._sends: set[asyncio.Task] = set()

    async def alert(self, contact: Contact, incident: Incident) -> None:
        await super().alert(contact, incident)
        if contact.kind == ContactKind.EMERGENCY:
            return
        if contact.telegram_chat_id is None:
            log.warning("%s (%s) has no Telegram linked; alert only logged", contact.name, contact.id)
            return
        task = asyncio.create_task(self._send(contact.telegram_chat_id, contact, incident))
        self._sends.add(task)
        task.add_done_callback(self._sends.discard)

    async def flush(self) -> None:
        """Wait for sends in progress (tests)."""
        await asyncio.gather(*self._sends)

    async def _send(self, chat_id: int, contact: Contact, incident: Incident) -> None:
        try:
            await self.client.send_message(chat_id, alert_text(incident))
            log.info("Telegram alert sent to %s for incident %s", contact.id, incident.id)
        except Exception as e:
            log.error("Telegram alert to %s failed for incident %s: %s", contact.id, incident.id, e)


def alert_text(incident: Incident) -> str:
    at = incident.created_at.astimezone().strftime("%H:%M")  # the laptop's local time
    return (
        f"⚠️ Possible fall ({incident.camera_id}) at {at}.\n"
        f"{_why_escalated(incident)}\n"
        "Please check on them, then acknowledge on the dashboard.\n"
        f"Incident {incident.id[:8]}"
    )


def _why_escalated(incident: Incident) -> str:
    for entry in incident.timeline:
        if entry.from_state == IncidentState.ASKING and entry.to_state == IncidentState.ESCALATED:
            if entry.trigger == Trigger.NEEDS_HELP:
                return "They said they need help."
            break
    return "They didn't say they were okay when asked."
