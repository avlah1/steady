from tests.conftest import TEST_API_KEY
from tests.test_box_reply import reply, start_incident
from tests.test_detections import detection

URL = "/api/v1/incidents"
AUTH = {"X-API-Key": TEST_API_KEY}


def test_list_empty(client):
    resp = client.get(URL, headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"incidents": []}


def test_list_newest_first(client):
    first = client.post("/api/v1/detections", json=detection(camera_id="kitchen"), headers=AUTH).json()["incident_id"]
    second = client.post("/api/v1/detections", json=detection(camera_id="bedroom"), headers=AUTH).json()["incident_id"]

    ids = [i["id"] for i in client.get(URL, headers=AUTH).json()["incidents"]]

    assert ids == [second, first]


def test_get_one_with_timeline(client):
    incident_id = start_incident(client)
    reply(client, incident_id, "help")

    body = client.get(f"{URL}/{incident_id}", headers=AUTH).json()

    assert body["id"] == incident_id
    assert body["state"] == "ESCALATED"
    assert body["is_active"] is True
    assert body["contact_position"] == 1
    assert [(t["from_state"], t["to_state"], t["trigger"]) for t in body["timeline"]] == [
        ("OPEN", "ASKING", "new_fall"),
        ("ASKING", "ESCALATED", "needs_help"),
    ]


def test_resolved_incident_is_not_active(client):
    incident_id = start_incident(client)
    reply(client, incident_id, "I'm fine")

    body = client.get(f"{URL}/{incident_id}", headers=AUTH).json()

    assert body["state"] == "RESOLVED_OK"
    assert body["is_active"] is False


def test_get_unknown_404(client):
    assert client.get(f"{URL}/no-such-incident", headers=AUTH).status_code == 404


def test_reads_require_api_key(client):
    incident_id = start_incident(client)

    assert client.get(URL).status_code == 401
    assert client.get(f"{URL}/{incident_id}").status_code == 401
