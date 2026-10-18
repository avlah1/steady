import os

# app.main reads settings at import time; give it a key so tests don't depend on a local .env.
os.environ.setdefault("API_KEY", "test-key")
# Never call the real Claude API from tests, even if .env has a key (env vars beat .env).
os.environ["ANTHROPIC_API_KEY"] = ""
# Don't load Whisper in tests (slow, downloads a model); tests that need it use a fake.
os.environ["STT_MODEL"] = ""
# Keep tests in memory. tests/test_mongo_repository.py uses its own database explicitly.
os.environ["MONGODB_URI"] = ""

import pytest
from fastapi.testclient import TestClient

from app.box import LoggingBox
from app.config import Settings, get_settings
from app.classifier import KeywordClassifier
from app.deps import get_classifier, get_flow, get_repo
from app.incidents import IncidentFlow
from app.main import app
from app.models import Contact
from app.notifier import LoggingNotifier
from app.repository import InMemoryRepository

TEST_API_KEY = "test-key"

CONTACTS = [Contact(id="contact-1", name="Caregiver 1"), Contact(id="contact-2", name="Caregiver 2")]


@pytest.fixture
def anyio_backend():
    # Async tests (marked @pytest.mark.anyio) run on asyncio, same as uvicorn.
    return "asyncio"


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository(contacts=CONTACTS)


@pytest.fixture
def box() -> LoggingBox:
    return LoggingBox()


@pytest.fixture
def notifier() -> LoggingNotifier:
    return LoggingNotifier()


@pytest.fixture
def client(repo, box, notifier):
    """Test client with fresh storage and fake Box/notifier for every test.

    Timeouts are long so timers never fire during endpoint tests.
    """
    flow = IncidentFlow(repo, box, notifier, reply_timeout_s=60, ack_timeout_s=60)
    app.dependency_overrides[get_settings] = lambda: Settings(api_key=TEST_API_KEY)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_flow] = lambda: flow
    app.dependency_overrides[get_classifier] = lambda: KeywordClassifier()
    # The `with` block keeps one event loop alive for the whole test, so timer tasks
    # created by one request still exist for the next.
    with TestClient(app) as c:
        yield c
        c.portal.call(flow.cancel_all_timers)
    app.dependency_overrides.clear()
