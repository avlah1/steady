"""Shared dependencies. Tests swap these out via app.dependency_overrides."""

import secrets
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.box import Box, WebSocketBox
from app.classifier import Classifier, KeywordClassifier
from app.config import Settings, get_settings
from app.incidents import IncidentFlow
from app.models import Contact, ContactKind
from app.notifier import LoggingNotifier
from app.repository import InMemoryRepository, Repository

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    key: str | None = Depends(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    # compare_digest takes the same time whether or not the key matches, so it can't be guessed by timing.
    if key is None or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


# Placeholder caregivers until the Supabase contacts table exists.
DEMO_CONTACTS = [
    Contact(id="contact-1", name="Caregiver 1"),
    Contact(id="contact-2", name="Caregiver 2"),
    Contact(id="emergency", name="Emergency services", kind=ContactKind.EMERGENCY),
]


@lru_cache
def get_repo() -> Repository:
    return InMemoryRepository(contacts=DEMO_CONTACTS)


@lru_cache
def get_box() -> Box:
    return WebSocketBox()


@lru_cache
def get_flow() -> IncidentFlow:
    settings = get_settings()
    return IncidentFlow(
        get_repo(),
        get_box(),
        LoggingNotifier(),
        reply_timeout_s=settings.reply_timeout_s,
        ack_timeout_s=settings.ack_timeout_s,
    )


@lru_cache
def get_classifier() -> Classifier:
    return KeywordClassifier()  # swap for the LLM classifier when it's ready
