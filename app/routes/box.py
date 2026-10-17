import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.classifier import Classifier, classify_reply
from app.config import Settings, get_settings
from app.deps import get_classifier, get_flow, get_repo, require_api_key
from app.incidents import ASK_AGAIN_PROMPT, ASK_PROMPT, IncidentFlow
from app.models import Incident, Trigger
from app.repository import Repository

log = logging.getLogger("box_reply")

router = APIRouter(prefix="/api/v1/box", dependencies=[Depends(require_api_key)])


class BoxReplyIn(BaseModel):
    incident_id: str = Field(min_length=1)
    # Text for now; audio + speech-to-text comes later. Empty text is allowed and counts as "unclear".
    text: str = Field(max_length=2000)


class BoxReplyAccepted(BaseModel):
    incident_id: str
    accepted: bool = True


@router.post("/reply", status_code=status.HTTP_202_ACCEPTED)
async def post_reply(
    reply: BoxReplyIn,
    background: BackgroundTasks,
    repo: Repository = Depends(get_repo),
    flow: IncidentFlow = Depends(get_flow),
    classifier: Classifier = Depends(get_classifier),
    settings: Settings = Depends(get_settings),
) -> BoxReplyAccepted:
    incident = repo.get_incident(reply.incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    # Classifying can take seconds once it's an LLM, so it runs after the response is sent.
    background.add_task(_classify_and_handle, flow, classifier, incident, reply.text, settings.classify_timeout_s)
    return BoxReplyAccepted(incident_id=incident.id)


async def _classify_and_handle(
    flow: IncidentFlow, classifier: Classifier, incident: Incident, text: str, timeout_s: float
) -> None:
    question = ASK_PROMPT if incident.attempt <= 1 else ASK_AGAIN_PROMPT
    label = await classify_reply(classifier, question, text, timeout_s)
    log.info("Reply for incident %s: %r -> %s", incident.id, text, label)
    # Not the reply's job to check the state: handle() ignores a reply that arrives too late.
    await flow.handle(incident, Trigger(label))
