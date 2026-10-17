"""Storage behind a small interface so Supabase can replace the in-memory version later."""

from typing import Protocol

from app.models import DetectionAccepted, DetectionIn, Incident


class Repository(Protocol):
    def get_detection_response(self, event_id: str) -> DetectionAccepted | None: ...
    def save_event(self, event: DetectionIn, response: DetectionAccepted) -> None: ...
    def get_active_incident(self, camera_id: str) -> Incident | None: ...
    def get_incident(self, incident_id: str) -> Incident | None: ...
    def save_incident(self, incident: Incident) -> None: ...


class InMemoryRepository:
    def __init__(self) -> None:
        self.events: dict[str, DetectionIn] = {}
        # Response returned for each event_id, replayed when the worker retries.
        self.responses: dict[str, DetectionAccepted] = {}
        self.incidents: dict[str, Incident] = {}

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
