from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.deps import get_flow, get_repo, require_api_key
from app.incidents import IncidentFlow
from app.models import IncidentState, Trigger
from app.repository import Repository

router = APIRouter(prefix="/api/v1/incidents", dependencies=[Depends(require_api_key)])


class AckResult(BaseModel):
    incident_id: str
    state: IncidentState


@router.post("/{incident_id}/ack")
async def ack_incident(
    incident_id: str,
    repo: Repository = Depends(get_repo),
    flow: IncidentFlow = Depends(get_flow),
) -> AckResult:
    incident = repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    # Unlike detections and replies, this runs before responding: an ack is quick (no LLM, no
    # alerts sent), and the dashboard wants to show the result. handle() ignores an ack that
    # doesn't apply (already acknowledged, or the person said they're fine), so a double-click
    # is harmless; the returned state tells the dashboard what actually happened.
    await flow.handle(incident, Trigger.ACK)
    return AckResult(incident_id=incident_id, state=repo.get_incident(incident_id).state)
