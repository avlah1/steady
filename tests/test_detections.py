from uuid import uuid4

import pytest

from app.incidents import ASK_PROMPT
from app.models import IncidentState
from tests.conftest import TEST_API_KEY

URL = "/api/v1/detections"
AUTH = {"X-API-Key": TEST_API_KEY}


def detection(**overrides):
    body = {
        "event_id": str(uuid4()),
        "type": "fall_suspected",
        "confidence": 0.87,
        "detected_at": "2026-10-17T14:31:05Z",
        "camera_id": "living_room",
        "details": {"hip_drop": 0.42, "torso_angle_deg": 12, "seconds_down": 5.2},
    }
    body.update(overrides)
    return body


def test_new_fall_creates_incident_and_asks(client, repo, box):
    resp = client.post(URL, json=detection(), headers=AUTH)

    assert resp.status_code == 202
    body = resp.json()
    assert body["created"] is True

    incident = repo.get_incident(body["incident_id"])
    assert incident.state == IncidentState.ASKING
    assert incident.attempt == 1
    assert [(e.from_state, e.to_state, e.trigger) for e in incident.timeline] == [
        (IncidentState.OPEN, IncidentState.ASKING, "new_fall")
    ]
    assert box.said == [(incident.id, ASK_PROMPT)]


def test_duplicate_event_id_replays_original_response(client, repo, box):
    payload = detection()
    first = client.post(URL, json=payload, headers=AUTH)
    retry = client.post(URL, json=payload, headers=AUTH)

    assert retry.status_code == 202
    assert retry.json() == first.json()  # still says created: true
    assert len(repo.incidents) == 1
    assert len(box.said) == 1  # no second prompt


def test_second_fall_same_camera_attaches_to_active_incident(client, repo, box):
    first = client.post(URL, json=detection(), headers=AUTH).json()
    second_payload = detection()
    second = client.post(URL, json=second_payload, headers=AUTH).json()

    assert second == {"incident_id": first["incident_id"], "created": False}
    incident = repo.get_incident(first["incident_id"])
    assert second_payload["event_id"] in incident.event_ids
    assert len(box.said) == 1  # prompt sequence not restarted


def test_fall_on_other_camera_creates_separate_incident(client, repo):
    a = client.post(URL, json=detection(camera_id="living_room"), headers=AUTH).json()
    b = client.post(URL, json=detection(camera_id="kitchen"), headers=AUTH).json()

    assert b["created"] is True
    assert a["incident_id"] != b["incident_id"]


@pytest.mark.parametrize("state", [IncidentState.RESOLVED_OK, IncidentState.ACKNOWLEDGED])
def test_fall_after_incident_closed_creates_new_incident(client, repo, state):
    first = client.post(URL, json=detection(), headers=AUTH).json()
    repo.get_incident(first["incident_id"]).state = state

    second = client.post(URL, json=detection(), headers=AUTH).json()

    assert second["created"] is True
    assert second["incident_id"] != first["incident_id"]


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_missing_or_wrong_api_key_rejected(client, repo, headers):
    resp = client.post(URL, json=detection(), headers=headers)

    assert resp.status_code == 401
    assert repo.incidents == {}


@pytest.mark.parametrize(
    "overrides",
    [{"type": "something_else"}, {"confidence": 1.5}, {"camera_id": ""}, {"detected_at": "not a date"}],
)
def test_invalid_payload_rejected(client, repo, overrides):
    resp = client.post(URL, json=detection(**overrides), headers=AUTH)

    assert resp.status_code == 422
    assert repo.incidents == {}
