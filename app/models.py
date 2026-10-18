from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- API payloads ---


class DetectionIn(BaseModel):
    """What the vision worker POSTs to /api/v1/detections."""

    event_id: str = Field(min_length=1)
    type: Literal["fall_suspected"]
    confidence: float = Field(ge=0.0, le=1.0)
    detected_at: datetime
    camera_id: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class DetectionAccepted(BaseModel):
    incident_id: str
    created: bool


# --- Domain records ---


class IncidentState(StrEnum):
    OPEN = "OPEN"
    ASKING = "ASKING"
    RESOLVED_OK = "RESOLVED_OK"
    ESCALATED = "ESCALATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


# A new detection on the same camera attaches to an incident in one of these states.
ACTIVE_STATES = frozenset({IncidentState.OPEN, IncidentState.ASKING, IncidentState.ESCALATED})


class Trigger(StrEnum):
    """Something that happened, passed to IncidentFlow.handle()."""

    NEW_FALL = "new_fall"  # detections endpoint
    OK = "ok"  # reply labels from the LLM
    NEEDS_HELP = "needs_help"
    UNCLEAR = "unclear"
    REPLY_TIMEOUT = "reply_timeout"  # timers
    ACK_TIMEOUT = "ack_timeout"
    ACK = "ack"  # caregiver acknowledged


class ContactKind(StrEnum):
    PERSON = "person"  # family, neighbour, carer: acks from the dashboard
    EMERGENCY = "emergency"  # emergency services; simulated for now (no public 911 API)


class Contact(BaseModel):
    id: str
    name: str
    phone: str | None = None
    # Emergency services is just another contact, so the user decides where it sits in the alert order.
    kind: ContactKind = ContactKind.PERSON


class TimelineEntry(BaseModel):
    at: datetime = Field(default_factory=utcnow)
    from_state: IncidentState | None
    to_state: IncidentState
    trigger: Trigger


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    camera_id: str
    state: IncidentState = IncidentState.OPEN
    attempt: int = 0  # which "Are you okay?" we're on (1-3) while ASKING
    alerts_sent: int = 0  # position in the contact list so far (wraps around); picks the next contact
    emergency_alerted: bool = False  # emergency services is alerted at most once per incident
    event_ids: list[str] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    @computed_field  # also sent to the dashboard, so it doesn't need its own copy of ACTIVE_STATES
    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES
