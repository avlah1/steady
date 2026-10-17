"""Incident state machine. Every state change goes through IncidentFlow.handle().

Endpoints and timers never change an incident directly; they report a Trigger and
handle() decides what happens based on the trigger and the incident's current state.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any

from app.box import Box
from app.models import Incident, IncidentState, TimelineEntry, Trigger
from app.notifier import Notifier
from app.repository import Repository

log = logging.getLogger("incidents")

MAX_ATTEMPTS = 3

ASK_PROMPT = "Are you okay?"
ASK_AGAIN_PROMPT = "I didn't catch that. Are you okay?"
RESOLVED_PROMPT = "Okay, glad you're alright."
ESCALATING_PROMPT = "I'm contacting your caregiver now."
HELP_COMING_PROMPT = "Help is coming."

S = IncidentState
T = Trigger


class IncidentFlow:
    def __init__(
        self,
        repo: Repository,
        box: Box,
        notifier: Notifier,
        *,
        reply_timeout_s: float,
        ack_timeout_s: float,
    ) -> None:
        self.repo = repo
        self.box = box
        self.notifier = notifier
        self.reply_timeout_s = reply_timeout_s
        self.ack_timeout_s = ack_timeout_s
        self._timers: dict[str, asyncio.Task] = {}
        # One lock per incident: a reply and a timer arriving together are handled one after the other.
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def handle(self, incident: Incident, trigger: Trigger | str, data: dict[str, Any] | None = None) -> None:
        trigger = Trigger(trigger)  # raises on typos like "needs-help"
        data = data or {}
        async with self._locks[incident.id]:
            # Re-read inside the lock so we act on the latest state, not a copy from before we waited.
            current = self.repo.get_incident(incident.id)
            if current is None:
                log.error("handle(%s): incident %s not found", trigger, incident.id)
                return
            if self._is_stale_timer(current, trigger, data):
                log.info("Stale %s for incident %s ignored", trigger, current.id)
                return
            await self._dispatch(current, trigger)

    async def _dispatch(self, incident: Incident, trigger: Trigger) -> None:
        state = incident.state

        if state == S.OPEN and trigger == T.NEW_FALL:
            await self._ask(incident, trigger, attempt=1)

        elif state == S.ASKING and trigger == T.OK:
            self._transition(incident, S.RESOLVED_OK, trigger)
            await self.box.say(incident.id, RESOLVED_PROMPT)

        elif state == S.ASKING and trigger == T.NEEDS_HELP:
            await self._escalate(incident, trigger)

        elif state == S.ASKING and trigger in (T.UNCLEAR, T.REPLY_TIMEOUT):
            if incident.attempt < MAX_ATTEMPTS:
                await self._ask(incident, trigger, attempt=incident.attempt + 1)
            else:
                await self._escalate(incident, trigger)

        elif state == S.ESCALATED and trigger == T.ACK_TIMEOUT:
            self._transition(incident, S.ESCALATED, trigger)
            await self._alert_next_contact(incident)

        # Ack is also accepted while ASKING: a caregiver watching the dashboard may respond early.
        elif state in (S.ASKING, S.ESCALATED) and trigger == T.ACK:
            self._transition(incident, S.ACKNOWLEDGED, trigger)
            await self.box.say(incident.id, HELP_COMING_PROMPT)

        else:
            # e.g. a late "ok" after escalation: help is already on the way, so it doesn't cancel anything.
            log.warning("Ignored %s for incident %s in state %s", trigger, incident.id, state)

    # --- Actions ---
    # Each saves and starts its timer *before* talking to the Box or caregivers, so if one of
    # those calls fails, the timer still fires and the incident keeps moving toward escalation.

    async def _ask(self, incident: Incident, trigger: Trigger, attempt: int) -> None:
        self._transition(incident, S.ASKING, trigger)
        incident.attempt = attempt
        self.repo.save_incident(incident)
        self._start_timer(incident, T.REPLY_TIMEOUT, self.reply_timeout_s, {"attempt": attempt})
        await self.box.say(incident.id, ASK_PROMPT if attempt == 1 else ASK_AGAIN_PROMPT)

    async def _escalate(self, incident: Incident, trigger: Trigger) -> None:
        self._transition(incident, S.ESCALATED, trigger)
        await self._alert_next_contact(incident)
        await self.box.say(incident.id, ESCALATING_PROMPT)

    async def _alert_next_contact(self, incident: Incident) -> None:
        contacts = self.repo.list_contacts()
        contact = contacts[incident.alerts_sent % len(contacts)] if contacts else None  # wraps to the first
        incident.alerts_sent += 1
        self.repo.save_incident(incident)
        self._start_timer(incident, T.ACK_TIMEOUT, self.ack_timeout_s, {"alerts_sent": incident.alerts_sent})
        if contact is None:
            log.error("No caregiver contacts configured; incident %s cannot alert anyone", incident.id)
            return
        await self.notifier.alert(contact, incident)

    def _transition(self, incident: Incident, to_state: IncidentState, trigger: Trigger) -> None:
        self._cancel_timer(incident.id)
        incident.timeline.append(TimelineEntry(from_state=incident.state, to_state=to_state, trigger=trigger))
        log.info("Incident %s: %s -> %s (%s)", incident.id, incident.state, to_state, trigger)
        incident.state = to_state
        self.repo.save_incident(incident)

    # --- Timers ---

    @staticmethod
    def _is_stale_timer(incident: Incident, trigger: Trigger, data: dict[str, Any]) -> bool:
        """A timer is stale if the incident moved on after the timer was started."""
        if trigger == T.REPLY_TIMEOUT:
            return not (incident.state == S.ASKING and incident.attempt == data.get("attempt"))
        if trigger == T.ACK_TIMEOUT:
            return not (incident.state == S.ESCALATED and incident.alerts_sent == data.get("alerts_sent"))
        return False

    def _start_timer(self, incident: Incident, trigger: Trigger, delay_s: float, data: dict[str, Any]) -> None:
        self._cancel_timer(incident.id)
        self._timers[incident.id] = asyncio.create_task(self._fire_after(incident, trigger, delay_s, data))

    async def _fire_after(self, incident: Incident, trigger: Trigger, delay_s: float, data: dict[str, Any]) -> None:
        await asyncio.sleep(delay_s)
        try:
            await self.handle(incident, trigger, data)
        except Exception:
            log.exception("Timer %s failed for incident %s", trigger, incident.id)

    def _cancel_timer(self, incident_id: str) -> None:
        task = self._timers.pop(incident_id, None)
        # When a timer fires, its own task calls handle(), which lands here; don't cancel ourselves.
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def cancel_all_timers(self) -> None:
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()
