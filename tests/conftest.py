import pytest
from fastapi.testclient import TestClient

from app.box import LoggingBox
from app.config import Settings, get_settings
from app.deps import get_flow, get_repo
from app.incidents import IncidentFlow
from app.main import app
from app.repository import InMemoryRepository

TEST_API_KEY = "test-key"


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def box() -> LoggingBox:
    return LoggingBox()


@pytest.fixture
def client(repo, box):
    """Test client with fresh storage and a fake Box for every test."""
    flow = IncidentFlow(repo, box)
    app.dependency_overrides[get_settings] = lambda: Settings(api_key=TEST_API_KEY)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_flow] = lambda: flow
    yield TestClient(app)
    app.dependency_overrides.clear()
