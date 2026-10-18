import logging

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.classifier import Classifier, classify_reply
from app.config import Settings, get_settings
from app.deps import get_classifier, get_flow, get_repo, get_transcriber, require_api_key
from app.incidents import ASK_AGAIN_PROMPT, ASK_PROMPT, IncidentFlow
from app.models import Incident, Trigger
from app.repository import Repository
from app.speech import Transcriber, read_wav, transcribe_reply

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


@router.post("/reply/audio", status_code=status.HTTP_202_ACCEPTED)
async def post_reply_audio(
    request: Request,
    background: BackgroundTasks,
    incident_id: str = Query(min_length=1),
    repo: Repository = Depends(get_repo),
    flow: IncidentFlow = Depends(get_flow),
    classifier: Classifier = Depends(get_classifier),
    transcriber: Transcriber | None = Depends(get_transcriber),
    settings: Settings = Depends(get_settings),
) -> BoxReplyAccepted:
    # The body is the raw WAV file, not a form upload: that's the simplest thing for ESP32 firmware
    # to send. incident_id goes in the URL for the same reason.
    if transcriber is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Audio replies are off (STT_MODEL is empty)")
    incident = repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    data = await request.body()
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Audio too large")
    # Checked before responding, so a firmware format mistake shows up as a 422 instead of
    # silently turning every reply into "unclear".
    try:
        audio = read_wav(data)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e

    background.add_task(_transcribe_and_handle, flow, transcriber, classifier, incident, audio, settings)
    return BoxReplyAccepted(incident_id=incident.id)


async def _transcribe_and_handle(
    flow: IncidentFlow,
    transcriber: Transcriber,
    classifier: Classifier,
    incident: Incident,
    audio: np.ndarray,
    settings: Settings,
) -> None:
    text = await transcribe_reply(transcriber, audio, settings.stt_timeout_s)
    log.info("Transcribed reply for incident %s: %r", incident.id, text)
    # From here it's exactly the text reply path.
    await _classify_and_handle(flow, classifier, incident, text, settings.classify_timeout_s)
