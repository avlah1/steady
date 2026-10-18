"""Alert delivery: always logs, optionally POSTs to a webhook and/or publishes to MQTT.

Network sends run on a background thread so a slow server never stalls the
video loop (pass blocking=True to send inline, e.g. in tests).
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .detector import Event

log = logging.getLogger(__name__)


class Alerter:
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        mqtt_host: Optional[str] = None,
        mqtt_port: int = 1883,
        mqtt_topic: str = "fall_detector/alert",
        blocking: bool = False,
    ):
        self.webhook_url = webhook_url
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic
        self.blocking = blocking

    def send(self, event: Event, clip_path: Optional[Path] = None) -> None:
        payload = {
            "event": "fall_alert",
            "time": datetime.now(timezone.utc).isoformat(),
            "info": event.info,
            "clip": str(clip_path) if clip_path else None,
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
