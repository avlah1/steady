"""Audio replies: WAV in, fake transcriber, then the normal label + state machine path."""

import asyncio
import io
import time
import wave

import numpy as np
import pytest

from app.deps import get_transcriber
from app.main import app
from app.models import IncidentState
from app.speech import read_wav, transcribe_reply
from tests.conftest import TEST_API_KEY
from tests.test_box_reply import start_incident

AUTH = {"X-API-Key": TEST_API_KEY}


def make_wav(seconds: float = 1.0, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return buf.getvalue()


class FakeTranscriber:
    def __init__(self, text: str = "", error: Exception | None = None, delay_s: float = 0):
        self.text, self.error, self.delay_s = text, error, delay_s
        self.heard: list[np.ndarray] = []

    def transcribe(self, audio):
        self.heard.append(audio)
        time.sleep(self.delay_s)
        if self.error:
            raise self.error
        return self.text


@pytest.fixture
def use_transcriber():
    def install(transcriber):
        app.dependency_overrides[get_transcriber] = lambda: transcriber
        return transcriber

    return install  # cleared by the client fixture's teardown


def send_audio(client, incident_id: str, body: bytes, headers=AUTH):
    return client.post(
        f"/api/v1/box/reply/audio?incident_id={incident_id}",
        content=body,
        headers={**headers, "Content-Type": "audio/wav"},
    )


# --- Endpoint ---


@pytest.mark.parametrize(
    ("said", "state"),
    [("I'm fine", IncidentState.RESOLVED_OK), ("help, my hip hurts", IncidentState.ESCALATED)],
)
def test_audio_reply_is_transcribed_then_labelled(client, repo, use_transcriber, said, state):
    transcriber = use_transcriber(FakeTranscriber(said))
    incident_id = start_incident(client)

    resp = send_audio(client, incident_id, make_wav(seconds=2))

    assert resp.status_code == 202
    assert resp.json() == {"incident_id": incident_id, "accepted": True}
    assert len(transcriber.heard[0]) == 2 * 16000  # the samples reached the transcriber
    assert repo.get_incident(incident_id).state == state


def test_failed_transcription_counts_as_unclear(client, repo, use_transcriber):
    use_transcriber(FakeTranscriber(error=RuntimeError("model crashed")))
    incident_id = start_incident(client)

    send_audio(client, incident_id, make_wav())

    incident = repo.get_incident(incident_id)
    assert incident.state == IncidentState.ASKING
    assert incident.attempt == 2  # asked again, not "ok"


def test_silence_counts_as_unclear(client, repo, use_transcriber):
    use_transcriber(FakeTranscriber(""))
    incident_id = start_incident(client)

    send_audio(client, incident_id, make_wav())

    assert repo.get_incident(incident_id).attempt == 2


@pytest.mark.parametrize(
    "body",
    [make_wav(rate=44100), make_wav(channels=2), make_wav(width=1), b"not a wav", b""],
    ids=["44khz", "stereo", "8bit", "garbage", "empty"],
)
def test_wrong_format_is_rejected_before_accepting(client, use_transcriber, body):
    transcriber = use_transcriber(FakeTranscriber("I'm fine"))
    incident_id = start_incident(client)

    resp = send_audio(client, incident_id, body)

    assert resp.status_code == 422
    assert transcriber.heard == []


def test_too_large_rejected(client, use_transcriber):
    use_transcriber(FakeTranscriber("I'm fine"))
    incident_id = start_incident(client)

    assert send_audio(client, incident_id, make_wav(seconds=40)).status_code == 413


def test_unknown_incident_404(client, use_transcriber):
    use_transcriber(FakeTranscriber("I'm fine"))

    assert send_audio(client, "no-such-incident", make_wav()).status_code == 404


def test_requires_api_key(client, use_transcriber):
    use_transcriber(FakeTranscriber("I'm fine"))
    incident_id = start_incident(client)

    assert send_audio(client, incident_id, make_wav(), headers={}).status_code == 401


def test_audio_off_when_no_model(client, use_transcriber):
    use_transcriber(None)
    incident_id = start_incident(client)

    assert send_audio(client, incident_id, make_wav()).status_code == 503


# --- Pieces ---


def test_read_wav_gives_float_samples():
    audio = read_wav(make_wav(seconds=0.5))

    assert audio.dtype == np.float32
    assert len(audio) == 8000


@pytest.mark.anyio
async def test_slow_transcription_times_out_to_empty():
    text = await transcribe_reply(FakeTranscriber("I'm fine", delay_s=0.5), np.zeros(16000, np.float32), timeout_s=0.05)

    assert text == ""
