"""Shared dependencies. Tests swap these out via app.dependency_overrides."""

import logging
import secrets
from functools import lru_cache

from anthropic import AsyncAnthropic
from pymongo import AsyncMongoClient

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.box import Box, WebSocketBox
from app.classifier import Classifier, KeywordClassifier, LLMClassifier
from app.config import Settings, get_settings
from app.incidents import IncidentFlow
from app.models import Contact, ContactKind
from app.notifier import LoggingNotifier, Notifier, TelegramNotifier
from app.repository import InMemoryRepository, MongoRepository, Repository
from app.speech import Transcriber, WhisperTranscriber
from app.telegram import TelegramClient, TelegramBot

log = logging.getLogger("deps")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    key: str | None = Depends(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    # compare_digest takes the same time whether or not the key matches, so it can't be guessed by timing.
    if key is None or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


# Default caregivers. With MongoDB they're copied into the contacts collection on first run;
# edit them there after that.
DEMO_CONTACTS = [
    Contact(id="contact-1", name="Caregiver 1"),
    Contact(id="contact-2", name="Caregiver 2"),
    Contact(id="emergency", name="Emergency services", kind=ContactKind.EMERGENCY),
]


@lru_cache
def get_repo() -> Repository:
    settings = get_settings()
    if not settings.mongodb_uri:
        log.warning("No MONGODB_URI set; incidents are kept in memory only and lost on restart")
        return InMemoryRepository(contacts=DEMO_CONTACTS)
    # tz_aware: dates come back as UTC-aware datetimes, the same as before they were saved.
    # A short server timeout so a database problem shows up quickly in the logs.
    client = AsyncMongoClient(settings.mongodb_uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    return MongoRepository(client, settings.mongodb_db, DEMO_CONTACTS)


@lru_cache
def get_box() -> Box:
    return WebSocketBox()


@lru_cache
def get_telegram() -> TelegramClient | None:
    token = get_settings().telegram_bot_token
    if not token:
        log.warning("No TELEGRAM_BOT_TOKEN set; caregiver alerts are only logged")
        return None
    return TelegramClient(token)


@lru_cache
def get_telegram_bot() -> TelegramBot | None:
    """None without a bot token: no Telegram to link or receive acks from."""
    client = get_telegram()
    return TelegramBot(client, get_repo(), get_flow()) if client else None


def build_notifier() -> Notifier:
    client = get_telegram()
    return TelegramNotifier(client) if client else LoggingNotifier()


@lru_cache
def get_flow() -> IncidentFlow:
    settings = get_settings()
    return IncidentFlow(
        get_repo(),
        get_box(),
        build_notifier(),
        reply_timeout_s=settings.reply_timeout_s,
        ack_timeout_s=settings.ack_timeout_s,
    )


def build_classifier(settings: Settings) -> Classifier:
    if not settings.anthropic_api_key:
        log.warning("No ANTHROPIC_API_KEY set; labelling replies with the keyword classifier")
        return KeywordClassifier()
    log.info("Labelling replies with the LLM classifier")
    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        # No SDK retries and a timeout inside classify_timeout_s: a slow or failed call should
        # become "unclear" quickly so the Box can ask again, not keep the person waiting.
        max_retries=0,
        timeout=max(settings.classify_timeout_s - 1, 1),
    )
    return LLMClassifier(client)


@lru_cache
def get_classifier() -> Classifier:
    # Cached: one API client for the whole app, reusing its connection.
    return build_classifier(get_settings())


@lru_cache
def get_transcriber() -> Transcriber | None:
    """The Whisper model, loaded once. None when STT_MODEL is empty (audio replies turned off)."""
    model = get_settings().stt_model
    return WhisperTranscriber(model) if model else None
