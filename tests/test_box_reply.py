import pytest

from app.deps import get_classifier
from app.main import app
from app.models import IncidentState
from tests.conftest import TEST_API_KEY
from tests.test_classifier import CrashingClassifier
from tests.test_detections import detection

URL = "/api/v1/box/reply"
AUTH = {"X-API-Key": TEST_API_KEY}


def start_incident(client) -> str:
    """POST a fall and return the new incident's id (now ASKING, attempt 1)."""
    return client.post("/api/v1/detections", json=detection(), headers=AUTH).json()["incident_id"]


def reply(client, incident_id: str, text: str, headers=AUTH):
    return client.post(URL, json={"incident_id": incident_id, "text": text}, headers=headers)


@pytest.mark.parametrize(
    ("text", "state"),
    [
        ("I'm fine", IncidentState.RESOLVED_OK),
        ("help, my hip hurts", IncidentState.ESCALATED),
        ("what?", IncidentState.ASKING),
    ],
)
def test_reply_moves_incident(client, repo, text, state):
    incident_id = start_incident(client)

    resp = reply(client, incident_id, text)

    assert resp.status_code == 202
    assert resp.json() == {"incident_id": incident_id, "accepted": True}
    assert repo.get_incident(incident_id).state == state


def test_unclear_reply_asks_again(client, repo):
    incident_id = start_incident(client)
    reply(client, incident_id, "what?")

    assert repo.get_incident(incident_id).attempt == 2


def test_empty_reply_counts_as_unclear(client, repo):
    incident_id = start_incident(client)
    reply(client, incident_id, "")

    assert repo.get_incident(incident_id).attempt == 2


def test_three_unclear_replies_escalate(client, repo, notifier):
    incident_id = start_incident(client)
    for _ in range(3):
        reply(client, incident_id, "hmm")

    assert repo.get_incident(incident_id).state == IncidentState.ESCALATED
    assert len(notifier.sent) == 1


def test_late_ok_after_escalation_changes_nothing(client, repo):
    incident_id = start_incident(client)
    reply(client, incident_id, "help")
    resp = reply(client, incident_id, "actually I'm fine")

    assert resp.status_code == 202
    assert repo.get_incident(incident_id).state == IncidentState.ESCALATED


def test_crashing_classifier_counts_as_unclear(client, repo):
    app.dependency_overrides[get_classifier] = lambda: CrashingClassifier()
    incident_id = start_incident(client)
    reply(client, incident_id, "I'm fine")

    incident = repo.get_incident(incident_id)
    assert (incident.state, incident.attempt) == (IncidentState.ASKING, 2)


def test_unknown_incident_404(client):
    assert reply(client, "no-such-incident", "I'm fine").status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_missing_or_wrong_api_key_rejected(client, repo, headers):
    incident_id = start_incident(client)

    assert reply(client, incident_id, "I'm fine", headers=headers).status_code == 401
    assert repo.get_incident(incident_id).state == IncidentState.ASKING
