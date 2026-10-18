"""Storage behind a small interface.

InMemoryRepository keeps everything in memory (tests, and running without a database).
MongoRepository keeps the same in-memory copy and also writes every change to MongoDB.
"""

import asyncio
import logging
from typing import Any, Protocol

from pymongo import AsyncMongoClient

from app.models import Contact, DetectionAccepted, DetectionIn, Incident

log = logging.getLogger("repository")


class Repository(Protocol):
    def get_detection_response(self, event_id: str) -> DetectionAccepted | None: ...
    def save_event(self, event: DetectionIn, response: DetectionAccepted) -> None: ...
    def get_active_incident(self, camera_id: str) -> Incident | None: ...
    def get_incident(self, incident_id: str) -> Incident | None: ...
    def save_incident(self, incident: Incident) -> None: ...
    def list_incidents(self) -> list[Incident]: ...
    def list_contacts(self) -> list[Contact]: ...
    def get_contact(self, contact_id: str) -> Contact | None: ...
    def save_contact(self, contact: Contact) -> None: ...


class InMemoryRepository:
    def __init__(self, contacts: list[Contact] | None = None) -> None:
        self.events: dict[str, DetectionIn] = {}
        # Response returned for each event_id, replayed when the worker retries.
        self.responses: dict[str, DetectionAccepted] = {}
        self.incidents: dict[str, Incident] = {}
        self.contacts: list[Contact] = contacts or []

    def get_detection_response(self, event_id: str) -> DetectionAccepted | None:
        return self.responses.get(event_id)

    def save_event(self, event: DetectionIn, response: DetectionAccepted) -> None:
        self.events[event.event_id] = event
        self.responses[event.event_id] = response

    def get_active_incident(self, camera_id: str) -> Incident | None:
        for incident in self.incidents.values():
            if incident.camera_id == camera_id and incident.is_active:
                return incident
        return None

    def get_incident(self, incident_id: str) -> Incident | None:
        return self.incidents.get(incident_id)

    def save_incident(self, incident: Incident) -> None:
        self.incidents[incident.id] = incident

    def list_incidents(self) -> list[Incident]:
        """Newest first. Dicts keep insertion order, so reversing it is exact even when two
        incidents share a timestamp."""
        return list(reversed(self.incidents.values()))

    def list_contacts(self) -> list[Contact]:
        """Caregivers in the order they should be alerted."""
        return list(self.contacts)

    def get_contact(self, contact_id: str) -> Contact | None:
        return next((c for c in self.contacts if c.id == contact_id), None)

    def save_contact(self, contact: Contact) -> None:
        """Replaces the contact with the same id, keeping its place in the order; new ones go last."""
        for i, existing in enumerate(self.contacts):
            if existing.id == contact.id:
                self.contacts[i] = contact
                return
        self.contacts.append(contact)


class MongoRepository(InMemoryRepository):
    """Memory is the live copy; MongoDB is the record.

    Reads come from memory and saves update memory immediately, exactly like InMemoryRepository,
    so the state machine never waits on the network. Each save is also queued, and one background
    writer sends them to MongoDB in order. A failed write is logged, never raised: a database
    problem must not stop a fall from escalating. Every write is the whole document, so the next
    save of the same incident repairs a write that failed.

    On startup, everything is loaded back from MongoDB, so incidents survive a restart.
    MongoDB stores times to the millisecond, so reloaded timestamps lose their microseconds.
    """

    def __init__(self, client: AsyncMongoClient, db_name: str, default_contacts: list[Contact]) -> None:
        super().__init__()
        self.client = client
        self.db = client[db_name]
        self.default_contacts = default_contacts
        self._queue: asyncio.Queue[tuple[str, str, dict[str, Any]]] = asyncio.Queue()
        self._writer: asyncio.Task | None = None

    async def start(self) -> None:
        try:
            await self._load()
        except Exception:
            # Keep running: the backend works from memory, and writes keep trying in the background.
            log.exception("Couldn't load from MongoDB; starting empty")
            self.contacts = list(self.default_contacts)
        self._writer = asyncio.create_task(self._write_loop())

    async def flush(self) -> None:
        """Wait until every queued write has been attempted."""
        await self._queue.join()

    async def stop(self, timeout_s: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self.flush(), timeout_s)
        except TimeoutError:
            log.error("Stopping with %d MongoDB writes not done", self._queue.qsize())
        if self._writer:
            self._writer.cancel()
        await self.client.close()

    # --- Saves: update memory (parent class), then queue the write ---

    def save_event(self, event: DetectionIn, response: DetectionAccepted) -> None:
        super().save_event(event, response)
        # The response is stored too, so a worker retry after a restart still gets the same answer.
        self._enqueue("events", event.event_id, {"event": event.model_dump(), "response": response.model_dump()})

    def save_incident(self, incident: Incident) -> None:
        super().save_incident(incident)
        # Dumped now, not when written: the incident object keeps changing after this call.
        self._enqueue("incidents", incident.id, incident.model_dump(exclude={"is_active"}))

    def save_contact(self, contact: Contact) -> None:
        super().save_contact(contact)
        # position is rewritten as the list index: same order, though not necessarily the same numbers.
        position = next(i for i, c in enumerate(self.contacts) if c.id == contact.id)
        self._enqueue("contacts", contact.id, {"position": position, **contact.model_dump()})

    def _enqueue(self, collection: str, doc_id: str, doc: dict[str, Any]) -> None:
        self._queue.put_nowait((collection, doc_id, doc))

    async def _write_loop(self) -> None:
        while True:
            collection, doc_id, doc = await self._queue.get()
            try:
                await self.db[collection].replace_one({"_id": doc_id}, {"_id": doc_id, **doc}, upsert=True)
            except Exception:
                log.exception("MongoDB write to %s/%s failed; kept in memory", collection, doc_id)
            finally:
                self._queue.task_done()

    # --- Startup ---

    async def _load(self) -> None:
        # Oldest first, so list_incidents() (newest first) keeps working after a restart.
        async for doc in self.db.incidents.find().sort("created_at", 1):
            incident = Incident.model_validate(doc)
            self.incidents[incident.id] = incident
        async for doc in self.db.events.find():
            event = DetectionIn.model_validate(doc["event"])
            self.events[event.event_id] = event
            self.responses[event.event_id] = DetectionAccepted.model_validate(doc["response"])
        self.contacts = [Contact.model_validate(doc) async for doc in self.db.contacts.find().sort("position", 1)]
        if not self.contacts:
            await self._seed_contacts()

        active = [i.id for i in self.incidents.values() if i.is_active]
        log.info("Loaded %d incidents, %d events, %d contacts from MongoDB",
                 len(self.incidents), len(self.events), len(self.contacts))
        if active:
            # Their timers were lost in the restart; the app restarts them (IncidentFlow.resume_after_restart).
            log.warning("%d incident(s) were still active before the restart: %s", len(active), active)

    async def _seed_contacts(self) -> None:
        """First run on an empty database: store the default contacts. Edit them in MongoDB after."""
        self.contacts = list(self.default_contacts)
        if not self.contacts:
            return
        await self.db.contacts.insert_many(
            [{"_id": c.id, "position": i, **c.model_dump()} for i, c in enumerate(self.contacts)]
        )
        log.info("Seeded %d default contacts into MongoDB", len(self.contacts))
