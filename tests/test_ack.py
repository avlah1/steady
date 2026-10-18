from app.incidents import HELP_COMING_PROMPT
from app.models import IncidentState
from tests.conftest import TEST_API_KEY
from tests.test_box_reply import reply, start_incident

AUTH = {"X-API-Key": TEST_API_KEY}


def ack(client, incident_id: str, headers=AUTH):
    return client.post(f"/api/v1/incidents/{incident_id}/ack", headers=headers)


def test_ack_escalated_incident(client, repo, box):
    incident_id = start_incident(client)
    reply(client, incident_id, "help, my hip hurts")

    resp = ack(client, incident_id)

    assert resp.status_code == 200
    assert resp.json() == {"incident_id": incident_id, "state": "ACKNOWLEDGED"}
    assert repo.get_incident(incident_id).state == IncidentState.ACKNOWLEDGED
    assert box.said[-1] == (incident_id, HELP_COMING_PROMPT)


def test_ack_while_still_asking(client):
    incident_id = start_incident(client)

    assert ack(client, incident_id).json()["state"] == "ACKNOWLEDGED"


def test_ack_stops_further_alerts(client, repo):
    incident_id = start_incident(client)
    reply(client, incident_id, "help")
    ack(client, incident_id)

    assert not repo.get_incident(incident_id).is_active  # no ack timer left to alert the next contact


def test_double_ack_is_harmless(client, box):
    incident_id = start_incident(client)
    reply(client, incident_id, "help")
    ack(client, incident_id)
    said_before = len(box.said)

    resp = ack(client, incident_id)

    assert resp.status_code == 200
    assert resp.json()["state"] == "ACKNOWLEDGED"
    assert len(box.said) == said_before  # "Help is coming" isn't repeated


def test_ack_after_person_said_ok_changes_nothing(client):
    incident_id = start_incident(client)
    reply(client, incident_id, "I'm fine")

    assert ack(client, incident_id).json()["state"] == "RESOLVED_OK"


def test_ack_unknown_incident_404(client):
    assert ack(client, "no-such-incident").status_code == 404


def test_ack_requires_api_key(client):
    incident_id = start_incident(client)

    assert ack(client, incident_id, headers={}).status_code == 401
    assert ack(client, incident_id, headers={"X-API-Key": "wrong"}).status_code == 401
