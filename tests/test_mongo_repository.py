"""MongoRepository against a real local MongoDB (scripts/mongo.sh start). Skipped if none is running."""

import logging
import socket
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient

from app.models import Contact, DetectionAccepted, DetectionIn, Incident, IncidentState, TimelineEntry, Trigger
from app.repository import MongoRepository


def _mongo_running() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 27017), timeout=0.5).close()
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not _mongo_running(), reason="no local MongoDB (scripts/mongo.sh start)"),
]

CONTACTS = [Contact(id="contact-1", name="Caregiver 1"), Contact(id="contact-2", name="Caregiver 2")]


@pytest.fixture
async def db_name():
    name = f"steady_test_{uuid4().hex[:8]}"
    yield name
    client = AsyncMongoClient("mongodb://localhost:27017")
    await client.drop_database(name)
    await client.close()


async def open_repo(db_name: str) -> MongoRepository:
    client = AsyncMongoClient("mongodb://localhost:27017", tz_aware=True, serverSelectionTimeoutMS=2000)
    repo = MongoRepository(client, db_name, CONTACTS)
    await repo.start()
    return repo


def detection(camera_id: str = "living_room") -> DetectionIn:
    return DetectionIn(
        event_id=str(uuid4()), type="fall_suspected", confidence=0.9,
        detected_at=datetime(2026, 10, 17, 14, 31, 5, tzinfo=UTC), camera_id=camera_id,
        details={"hip_drop": 0.42},
    )


async def test_first_run_seeds_default_contacts(db_name):
    repo = await open_repo(db_name)
    await repo.stop()

    reopened = await open_repo(db_name)
    assert [c.id for c in reopened.list_contacts()] == ["contact-1", "contact-2"]
    await reopened.stop()


async def test_incident_survives_restart_exactly(db_name):
    repo = await open_repo(db_name)
    # Millisecond timestamps: MongoDB doesn't store microseconds.
    at = datetime(2026, 10, 17, 14, 31, 5, 123000, tzinfo=UTC)
    incident = Incident(camera_id="living_room", state=IncidentState.ESCALATED, attempt=3, contact_position=1,
                        created_at=at, event_ids=["e1", "e2"])
    incident.timeline.append(TimelineEntry(at=at, from_state=IncidentState.ASKING, to_state=IncidentState.ESCALATED,
                                           trigger=Trigger.NEEDS_HELP))
    repo.save_incident(incident)
    await repo.stop()

    reopened = await open_repo(db_name)
    assert reopened.get_incident(incident.id) == incident  # every field, including timeline and dates
    assert reopened.get_active_incident("living_room") == incident
    await reopened.stop()


async def test_latest_save_wins(db_name):
    repo = await open_repo(db_name)
    incident = Incident(camera_id="living_room")
    repo.save_incident(incident)
    incident.state = IncidentState.ASKING
    repo.save_incident(incident)
    incident.state = IncidentState.RESOLVED_OK
    repo.save_incident(incident)
    await repo.stop()

    reopened = await open_repo(db_name)
    assert reopened.get_incident(incident.id).state == IncidentState.RESOLVED_OK
    await reopened.stop()


async def test_worker_retry_after_restart_gets_same_answer(db_name):
    repo = await open_repo(db_name)
    event = detection()
    response = DetectionAccepted(incident_id="incident-1", created=True)
    repo.save_event(event, response)
    await repo.stop()

    reopened = await open_repo(db_name)
    assert reopened.get_detection_response(event.event_id) == response
    await reopened.stop()


async def test_empty_default_contacts_is_fine(db_name):
    client = AsyncMongoClient("mongodb://localhost:27017", tz_aware=True, serverSelectionTimeoutMS=2000)
    repo = MongoRepository(client, db_name, [])
    await repo.start()

    assert repo.list_contacts() == []
    await repo.stop()


async def test_list_incidents_newest_first_after_restart(db_name):
    repo = await open_repo(db_name)
    first = Incident(camera_id="a", created_at=datetime(2026, 10, 17, 14, 0, tzinfo=UTC))
    second = Incident(camera_id="b", created_at=datetime(2026, 10, 17, 15, 0, tzinfo=UTC))
    repo.save_incident(first)
    repo.save_incident(second)
    await repo.stop()

    reopened = await open_repo(db_name)
    assert [i.id for i in reopened.list_incidents()] == [second.id, first.id]
    await reopened.stop()


async def test_saves_never_wait_on_or_fail_because_of_the_database(caplog):
    # Nothing listens on this port: every write fails. Saves must still work from memory.
    client = AsyncMongoClient("mongodb://127.0.0.1:1", tz_aware=True, serverSelectionTimeoutMS=200)
    repo = MongoRepository(client, "unused", CONTACTS)
    await repo.start()  # load fails -> logged, starts empty with default contacts

    incident = Incident(camera_id="living_room")
    repo.save_incident(incident)  # returns immediately, no exception

    assert repo.get_incident(incident.id) is incident
    assert [c.id for c in repo.list_contacts()] == ["contact-1", "contact-2"]
    with caplog.at_level(logging.ERROR):
        await repo.flush()
    assert "MongoDB write to incidents" in caplog.text
    await repo.stop()


async def test_linked_telegram_chat_survives_restart_and_order_is_kept(db_name):
    repo = await open_repo(db_name)
    contact = repo.get_contact("contact-2").model_copy(update={"telegram_chat_id": 8999464616})
    repo.save_contact(contact)
    await repo.stop()

    reopened = await open_repo(db_name)
    assert [c.id for c in reopened.list_contacts()] == ["contact-1", "contact-2"]
    assert reopened.get_contact("contact-2").telegram_chat_id == 8999464616
    await reopened.stop()
