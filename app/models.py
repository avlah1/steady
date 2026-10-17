from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


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


class TimelineEntry(BaseModel):
    at: datetime = Field(default_factory=utcnow)
    from_state: IncidentState | None
    to_state: IncidentState
    trigger: str


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    camera_id: str
    state: IncidentState = IncidentState.OPEN
    attempt: int = 0
    event_ids: list[str] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES
