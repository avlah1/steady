"""Alert delivery: always logs, optionally POSTs to the backend and/or publishes to MQTT.

The POST follows docs/vision-contract.md: one body per fall with a fresh event_id, the
X-API-Key header, and retries with the same event_id until the backend answers.

Network sends run on a background thread so a slow server never stalls the
video loop (pass blocking=True to send inline, e.g. in tests).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from .detector import Event

log = logging.getLogger(__name__)

# The detector is rule-based and has no score of its own. The backend only stores this.
CONFIDENCE = 0.9
# Seconds between retries; the last one repeats forever. A dropped fall is the worst outcome.
RETRY_DELAYS_S = (1, 2, 4, 5)


class Alerter:
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        mqtt_host: Optional[str] = None,
        mqtt_port: int = 1883,
        mqtt_topic: str = "fall_detector/alert",
        blocking: bool = False,
        api_key: Optional[str] = None,
        camera_id: str = "living_room",
        retry_delays_s: Sequence[float] = RETRY_DELAYS_S,
    ):
        self.webhook_url = webhook_url
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic
        self.blocking = blocking
        self.api_key = api_key
        self.camera_id = camera_id
        self.retry_delays_s = retry_delays_s

    def send(self, event: Event, clip_path: Optional[Path] = None) -> None:
        payload = {
            # New id per fall. Retries below reuse it, so the backend won't count one fall twice.
            "event_id": str(uuid.uuid4()),
            "type": "fall_suspected",
            "confidence": CONFIDENCE,
            "detected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "camera_id": self.camera_id,
            "details": {**event.info, "clip": str(clip_path) if clip_path else None},
        }
        log.warning("FALL ALERT %s", payload)
        if self.webhook_url:
            self._run(self._post, payload)
        if self.mqtt_host:
            self._run(self._publish, payload)

    def _run(self, fn, payload) -> None:
        if self.blocking:
            fn(payload)
        else:
            threading.Thread(target=fn, args=(payload,), daemon=True).start()

    def _post(self, payload) -> None:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        req = urllib.request.Request(self.webhook_url, data=json.dumps(payload).encode(), headers=headers)
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    log.info("Backend accepted %s: %s", payload["event_id"], resp.read().decode())
                return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    # 401 = wrong/missing API key, 422 = body doesn't match the contract.
                    # Retrying won't fix either, so say so loudly and stop.
                    log.error("Backend REJECTED alert (%s): %s", exc.code, exc.read().decode()[:500])
                    return
                reason = f"HTTP {exc.code}"
            except Exception as exc:  # timeout, connection refused, Wi-Fi down
                reason = str(exc)
            delay = self.retry_delays_s[min(attempt, len(self.retry_delays_s) - 1)]
            log.error("Backend unreachable (%s); retrying %s in %ss", reason, payload["event_id"], delay)
            time.sleep(delay)
            attempt += 1

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
