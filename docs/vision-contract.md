# Vision worker → backend contract

How the vision worker (OpenCV + MediaPipe) reports falls to the backend.
The worker sends **events only, never video**.

## What an event is

An **event** is one fall the worker detected, reported once.

- **One fall = one event.** When the fall rules fire, generate a new UUID and send it. Do not send again on every frame while the person is still down.
- **Retry = same `event_id`.** If the request fails or times out, resend the same body with the same `event_id`. The backend recognises it and won't create a second incident.
- **New fall = new event.** Once the person has clearly recovered (e.g. upright for a few seconds), reset. The next fall gets a new `event_id`.

The backend groups events into **incidents**. An event is the evidence; the incident is the response (asking "Are you okay?", alerting caregivers). The worker never needs to track incidents.

## Endpoint

```
POST {BASE_URL}/api/v1/detections
Content-Type: application/json
X-API-Key: <shared secret, same value as API_KEY in the backend's .env>
```

`BASE_URL` is the laptop running the backend, e.g. `http://localhost:8000` (same machine) or `http://<laptop-ip>:8000` (over the hotspot).

## Request body

```json
{
  "event_id": "3f1c2a9e-8b7d-4c1e-9a2f-5d6e7f8a9b0c",
  "type": "fall_suspected",
  "confidence": 0.87,
  "detected_at": "2026-10-17T14:31:05Z",
  "camera_id": "living_room",
  "details": { "hip_drop": 0.42, "torso_angle_deg": 12, "seconds_down": 5.2 }
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `event_id` | string | yes | Non-empty. Use a UUID4. Unique per fall; reused only for retries. |
| `type` | string | yes | Must be exactly `"fall_suspected"`. |
| `confidence` | number | yes | `0.0`–`1.0`. Your own estimate of how sure the rules are. |
| `detected_at` | string | yes | ISO 8601 timestamp of when the fall was detected. Use UTC with `Z`. |
| `camera_id` | string | yes | Non-empty. Stable name for the camera, e.g. `"living_room"`. |
| `details` | object | no | Free-form numbers that explain the decision. Stored for debugging and the dashboard. Defaults to `{}`. |

Suggested `details` keys (not enforced):

| Key | Meaning |
|---|---|
| `hip_drop` | How far the hips dropped, as a fraction of frame height (0–1). |
| `torso_angle_deg` | Torso angle from horizontal; near 0 = lying flat. |
| `seconds_down` | How long the person has been on the ground when the event is sent. |

Unknown top-level fields are ignored. Put extra data in `details`.

## Responses

| Status | Meaning | Worker should |
|---|---|---|
| `202` | Accepted. Body: `{"incident_id": "...", "created": true\|false}` | Done. Don't resend. |
| `401` | Missing or wrong `X-API-Key` | Fix config. Don't retry. |
| `422` | Body failed validation (details in response) | Fix the code. Don't retry. |
| `5xx`, timeout, connection error | Backend unavailable | Retry with the **same** `event_id`. |

`created` is `true` if this event started a new incident and `false` if it joined one that was already open. A retry of an accepted event gets the exact same response again. The worker doesn't need to act on `created`; it's informational.

The backend responds immediately; it never waits on the voice prompt or alerts.

## Retry policy

- Request timeout: ~3 s.
- On `5xx`, timeout or connection error: retry with backoff (1 s, 2 s, 4 s, then every 5 s).
- **Don't give up on a fall.** A dropped fall is the worst outcome. Keep retrying until a `202` arrives.
- Don't block frame processing while retrying (send from a background thread or queue).

## Testing without the real worker

With the backend running (`uv run uvicorn app.main:app`):

```bash
scripts/send_fall.sh                    # new fall on living_room
scripts/send_fall.sh kitchen            # new fall on another camera
scripts/send_fall.sh living_room abc-1  # run twice to simulate a retry
```

Minimal Python sender:

```python
import uuid
from datetime import datetime, timezone

import requests

resp = requests.post(
    "http://localhost:8000/api/v1/detections",
    headers={"X-API-Key": API_KEY},
    json={
        "event_id": str(uuid.uuid4()),
        "type": "fall_suspected",
        "confidence": 0.87,
        "detected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "camera_id": "living_room",
        "details": {"hip_drop": 0.42, "torso_angle_deg": 12, "seconds_down": 5.2},
    },
    timeout=3,
)
print(resp.status_code, resp.json())
```

## Not part of this contract

Camera panning (`GET /pan?angle=N`) goes straight from the worker to the ESP32-CAM; the backend isn't involved.
