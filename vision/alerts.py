"""Alert delivery.

Always logs. Always reports the fall to the Steady backend per
docs/vision-contract.md (POST /api/v1/detections, retried forever on the
same event_id - "don't give up on a fall"). Optionally also POSTs to a
generic webhook and/or publishes to MQTT, for any other integration.

Network sends run on a background thread so a slow server never stalls the
video loop (pass blocking=True to send inline, e.g. in tests).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from .detector import Event

load_dotenv()  # same .env the backend reads; shares API_KEY with it

log = logging.getLogger(__name__)

# Per docs/vision-contract.md: 3s per attempt, backoff 1s/2s/4s then every 5s, forever.
_REQUEST_TIMEOUT_S = 3.0
_RETRY_BACKOFF_S = [1.0, 2.0, 4.0]
_RETRY_INTERVAL_S = 5.0


class Alerter:
    def __init__(
        self,
        camera_id: str,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        webhook_url: Optional[str] = None,
        mqtt_host: Optional[str] = None,
        mqtt_port: int = 1883,
        mqtt_topic: str = "fall_detector/alert",
        blocking: bool = False,
    ):
        self.camera_id = camera_id
        self.base_url = (base_url or os.environ.get("BACKEND_BASE_URL", "http://localhost:8000")).rstrip("/")
        self.api_key = api_key or os.environ.get("API_KEY")
        if not self.api_key:
            log.warning("No API_KEY set (env or --api-key); the backend will reject detections with 401")
        self.webhook_url = webhook_url
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic
        self.blocking = blocking

    def send(self, event: Event, confidence: float, details: dict[str, Any], clip_path: Optional[Path] = None) -> None:
        detection = {
            "event_id": str(uuid.uuid4()),  # fixed for this fall; reused on every retry below
            "type": "fall_suspected",
            "confidence": confidence,
            "detected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "camera_id": self.camera_id,
            "details": {k: v for k, v in details.items() if v is not None},
        }
        log.warning("FALL ALERT %s", detection)
        self._run(self._post_detection, detection)

        payload = {
            "event": "fall_alert",
            "time": detection["detected_at"],
            "info": event.info,
            "clip": str(clip_path) if clip_path else None,
        }
        if self.webhook_url:
            self._run(self._post_webhook, payload)
        if self.mqtt_host:
            self._run(self._publish, payload)

    def _run(self, fn, payload) -> None:
        if self.blocking:
            fn(payload)
        else:
            threading.Thread(target=fn, args=(payload,), daemon=True).start()

    def _post_detection(self, detection: dict) -> None:
        """POST /api/v1/detections. Retries the same event_id until accepted
        or the backend tells us not to (401/422) - a dropped fall is the
        worst outcome, so this never gives up on its own."""
        url = f"{self.base_url}/api/v1/detections"
        data = json.dumps(detection).encode()
        headers = {"Content-Type": "application/json", "X-API-Key": self.api_key or ""}
        attempt = 0
        while True:
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
                    body = json.loads(resp.read())
                    log.info("Detection %s accepted: %s", detection["event_id"], body)
                    return
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 422):
                    log.error("Detection %s rejected (%s): %s", detection["event_id"], exc.code, exc.read())
                    return
                log.warning("Detection %s got HTTP %s; retrying", detection["event_id"], exc.code)
            except Exception as exc:
                log.warning("Detection %s failed (%s); retrying", detection["event_id"], exc)
            delay = _RETRY_BACKOFF_S[attempt] if attempt < len(_RETRY_BACKOFF_S) else _RETRY_INTERVAL_S
            attempt += 1
            time.sleep(delay)

    def _post_webhook(self, payload) -> None:
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:
            log.error("Webhook failed: %s", exc)

    def _publish(self, payload) -> None:
        try:
            from paho.mqtt import publish  # pip install paho-mqtt

            publish.single(
                self.mqtt_topic, json.dumps(payload), hostname=self.mqtt_host, port=self.mqtt_port
            )
        except ImportError:
            log.error("MQTT alerts need paho-mqtt: pip install paho-mqtt")
        except Exception as exc:
            log.error("MQTT publish failed: %s", exc)
