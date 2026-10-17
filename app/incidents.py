"""Incident flow. Every state change goes through IncidentFlow.handle().

Only the `new_fall` trigger exists so far; replies, timers and escalation come next.
"""

import logging
from typing import Any

from app.box import Box
from app.models import Incident, IncidentState, TimelineEntry
from app.repository import Repository

log = logging.getLogger("incidents")

ASK_PROMPT = "Are you okay?"


class IncidentFlow:
    def __init__(self, repo: Repository, box: Box) -> None:
        self.repo = repo
        self.box = box

    async def handle(self, incident: Incident, trigger: str, data: dict[str, Any] | None = None) -> None:
        if trigger == "new_fall" and incident.state == IncidentState.OPEN:
            self._transition(incident, IncidentState.ASKING, trigger)
            incident.attempt = 1
            self.repo.save_incident(incident)
            await self.box.say(incident.id, ASK_PROMPT)
            return

        log.warning("Ignored trigger %r for incident %s in state %s", trigger, incident.id, incident.state)

    @staticmethod
    def _transition(incident: Incident, to_state: IncidentState, trigger: str) -> None:
        incident.timeline.append(TimelineEntry(from_state=incident.state, to_state=to_state, trigger=trigger))
        log.info("Incident %s: %s -> %s (%s)", incident.id, incident.state, to_state, trigger)
        incident.state = to_state
