from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.deps import get_flow, get_repo, require_api_key
from app.incidents import IncidentFlow
from app.models import DetectionAccepted, DetectionIn, Incident, Trigger
from app.repository import Repository

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.post("/detections", status_code=status.HTTP_202_ACCEPTED)
async def post_detection(
    detection: DetectionIn,
    background: BackgroundTasks,
    repo: Repository = Depends(get_repo),
    flow: IncidentFlow = Depends(get_flow),
) -> DetectionAccepted:
    # There is no `await` between the lookups and the saves below, so two requests for the
    # same camera can't interleave here and both create an incident. MongoDB doesn't change
    # that: saves update memory immediately and the database write happens in the background.
    # Keep it that way, or add a lock.

    # Rule 1: worker retry of an event we already handled -> replay the original answer.
    previous = repo.get_detection_response(detection.event_id)
    if previous is not None:
        return previous

    # Rule 2: an incident is already running for this camera -> attach, don't restart the prompts.
    incident = repo.get_active_incident(detection.camera_id)
    if incident is not None:
        incident.event_ids.append(detection.event_id)
        repo.save_incident(incident)
        response = DetectionAccepted(incident_id=incident.id, created=False)
        repo.save_event(detection, response)
        return response

    # Rule 3: new incident. The flow runs after the response is sent, so the worker never waits on it.
    incident = Incident(camera_id=detection.camera_id, event_ids=[detection.event_id])
    repo.save_incident(incident)
    response = DetectionAccepted(incident_id=incident.id, created=True)
    repo.save_event(detection, response)
    background.add_task(flow.handle, incident, Trigger.NEW_FALL)
    return response
