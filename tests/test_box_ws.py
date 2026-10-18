import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.box import WebSocketBox
from app.config import Settings, get_settings
from app.deps import get_box, get_flow, get_repo
from app.incidents import ASK_PROMPT, IncidentFlow
from app.main import app
from app.models import IncidentState
from tests.conftest import TEST_API_KEY
from tests.test_detections import detection

AUTH = {"X-API-Key": TEST_API_KEY}


@pytest.fixture
def ws_box() -> WebSocketBox:
    return WebSocketBox()


@pytest.fixture
def ws_client(repo, notifier, ws_box):
    """Like the `client` fixture, but the flow talks to a real WebSocketBox."""
    flow = IncidentFlow(repo, ws_box, notifier, reply_timeout_s=60, ack_timeout_s=60)
    app.dependency_overrides[get_settings] = lambda: Settings(api_key=TEST_API_KEY)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_flow] = lambda: flow
    app.dependency_overrides[get_box] = lambda: ws_box
    with TestClient(app) as c:
        yield c
        c.portal.call(flow.cancel_all_timers)
    app.dependency_overrides.clear()


def test_box_hears_are_you_okay_after_a_fall(ws_client):
    with ws_client.websocket_connect("/ws/box", headers=AUTH) as ws:
        incident_id = ws_client.post("/api/v1/detections", json=detection(), headers=AUTH).json()["incident_id"]

        assert ws.receive_json() == {"type": "say", "incident_id": incident_id, "text": ASK_PROMPT}


def test_key_in_query_param_also_works(ws_client):
    with ws_client.websocket_connect(f"/ws/box?api_key={TEST_API_KEY}") as ws:
        ws_client.post("/api/v1/detections", json=detection(), headers=AUTH)

        assert ws.receive_json()["text"] == ASK_PROMPT


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_bad_key_rejected(ws_client, ws_box, headers):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/ws/box", headers=headers) as ws:
            ws.receive_json()
    assert not ws_box.connections


def wait_until(condition, timeout_s: float = 1.0) -> bool:
    """The server runs on its own thread, so it registers connects/closes slightly later."""
    deadline = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_connect_and_disconnect_are_tracked(ws_client, ws_box):
    with ws_client.websocket_connect("/ws/box", headers=AUTH) as ws:
        ws.send_bytes(b"\x00")  # binary frames are ignored, not fatal
        ws.send_text("hello")
        assert wait_until(lambda: len(ws_box.connections) == 1)
    assert wait_until(lambda: not ws_box.connections)


def test_no_box_connected_incident_still_runs(ws_client, repo):
    incident_id = ws_client.post("/api/v1/detections", json=detection(), headers=AUTH).json()["incident_id"]

    assert repo.get_incident(incident_id).state == IncidentState.ASKING  # reply timer will escalate it


class HangingSocket:
    async def send_json(self, message):
        await asyncio.sleep(60)


class BrokenSocket:
    async def send_json(self, message):
        raise ConnectionResetError


@pytest.mark.anyio
async def test_hung_or_broken_box_is_dropped_without_blocking():
    box = WebSocketBox(send_timeout_s=0.05)
    box.connections = {HangingSocket(), BrokenSocket()}

    await box.say("incident-1", "Are you okay?")  # returns quickly instead of waiting 60 s

    assert not box.connections
