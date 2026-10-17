"""Shared dependencies. Tests swap these out via app.dependency_overrides."""

import secrets
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.box import LoggingBox
from app.config import Settings, get_settings
from app.incidents import IncidentFlow
from app.repository import InMemoryRepository, Repository

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    key: str | None = Depends(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    # compare_digest takes the same time whether or not the key matches, so it can't be guessed by timing.
    if key is None or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


@lru_cache
def get_repo() -> Repository:
    return InMemoryRepository()


@lru_cache
def get_flow() -> IncidentFlow:
    return IncidentFlow(get_repo(), LoggingBox())
