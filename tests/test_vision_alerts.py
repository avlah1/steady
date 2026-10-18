"""The vision worker's alerts must match the backend contract (docs/vision-contract.md)."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.models import DetectionIn
from vision.alerts import Alerter
from vision.detector import Event

ALERT = Event("alert", 12.3, {"reason": "still", "down_for_s": 3.4})


@pytest.fixture
def server():
    """A stand-in backend that records requests and answers with the next queued status."""
    received: list[tuple[dict, dict]] = []  # (headers with lowercased names, body)
    statuses: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(({k.lower(): v for k, v in self.headers.items()}, body))
            code = statuses.pop(0) if statuses else 202
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b'{"incident_id": "i1", "created": true}')

        def log_message(self, *args):  # keep test output quiet
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", received, statuses
    httpd.shutdown()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr("vision.alerts._RETRY_BACKOFF_S", [0.01])
    monkeypatch.setattr("vision.alerts._RETRY_INTERVAL_S", 0.01)


def alerter(base_url) -> Alerter:
    return Alerter(camera_id="kitchen", base_url=base_url, api_key="k", blocking=True)


def send(a: Alerter) -> None:
    a.send(ALERT, 0.9, ALERT.info)


def test_body_is_a_valid_detection(server):
    url, received, _ = server
    send(alerter(url))

    headers, body = received[0]
    detection = DetectionIn.model_validate(body)  # the backend's own model: raises if it doesn't match
    assert detection.camera_id == "kitchen"
    assert detection.details["reason"] == "still"
    assert headers["x-api-key"] == "k"  # header names aren't case-sensitive


def test_each_fall_gets_a_new_event_id(server):
    url, received, _ = server
    a = alerter(url)
    send(a)
    send(a)

    assert received[0][1]["event_id"] != received[1][1]["event_id"]


def test_retries_server_errors_with_the_same_event_id(server):
    url, received, statuses = server
    statuses.extend([500, 503])

    send(alerter(url))

    ids = [body["event_id"] for _, body in received]
    assert len(ids) == 3 and len(set(ids)) == 1  # two failures, then accepted; one fall, one id


@pytest.mark.parametrize("code", [401, 422])
def test_rejections_are_logged_loudly_and_not_retried(server, caplog, code):
    url, received, statuses = server
    statuses.append(code)

    with caplog.at_level(logging.ERROR):
        send(alerter(url))

    assert len(received) == 1
    assert f"rejected ({code})" in caplog.text
