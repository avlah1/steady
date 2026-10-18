# Backend endpoints

What each endpoint is for, who calls it, and what happens. For how incidents move between states, see [state-machine.md](state-machine.md). For the vision worker's exact payload, see [vision-contract.md](vision-contract.md).

All endpoints except `/health` need the header `X-API-Key: <API_KEY from .env>`. Wrong or missing key → `401`.

## At a glance

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET /health` | Anyone | Is the server up? | ✅ |
| `POST /api/v1/detections` | Vision worker | Report a fall | ✅ |
| `POST /api/v1/box/reply` | Box | Send the person's answer | ✅ (text only) |
| `POST /api/v1/incidents/{id}/ack` | Caregiver dashboard | "I'm on it" | ✅ |
| `GET /api/v1/incidents` | Caregiver dashboard | List incidents | Todo |
| `GET /api/v1/incidents/{id}` | Caregiver dashboard | One incident with its timeline | Todo |
| `WS /ws/box` | Box | Backend tells the Box what to say | Todo |

## `POST /api/v1/detections`

**The vision worker saw a fall.**

```json
{ "event_id": "uuid", "type": "fall_suspected", "confidence": 0.87,
  "detected_at": "2026-10-17T14:31:05Z", "camera_id": "living_room", "details": {} }
```

What happens:
1. Seen this `event_id` before? It's a retry. Send back the same answer, do nothing else.
2. This camera already has an open incident? Add the event to it. Nothing restarts.
3. Otherwise: create an incident. The Box asks "Are you okay?".

Returns `202 {"incident_id": "...", "created": true|false}` right away.
Bad payload → `422`.

Try it: `scripts/send_fall.sh [camera_id] [event_id]`

## `POST /api/v1/box/reply`

**The person answered the Box.**

```json
{ "incident_id": "...", "text": "I'm fine" }
```

What happens:
1. Unknown incident → `404`.
2. Returns `202 {"incident_id": "...", "accepted": true}` right away.
3. In the background, the reply is labelled `ok`, `needs_help` or `unclear`, and the state machine acts on it.

Labelling today is a simple keyword check; the LLM replaces it later. Empty text, errors and timeouts always count as `unclear`. A reply that arrives too late (incident already escalated or closed) is ignored.

Later: the Box sends audio instead of text, and speech-to-text runs first.

Try it: `scripts/send_reply.sh <incident_id> "I'm fine"`

## `POST /api/v1/incidents/{id}/ack`

**A caregiver is on it.** No body.

What happens:
1. Unknown incident → `404`.
2. Incident still asking or escalated → it becomes `ACKNOWLEDGED`, alerts stop, and the Box says "Help is coming".
3. Anything else (already acknowledged, or the person said they're fine) → nothing changes. Safe to tap twice.

Returns `200 {"incident_id": "...", "state": "ACKNOWLEDGED"}`. `state` is always the incident's state *after* the ack, so the dashboard can show what actually happened (e.g. `RESOLVED_OK` if the person already answered "I'm fine").

Unlike the other endpoints, this one finishes the work before it responds. It's quick, and the dashboard needs the result.

Try it: `scripts/send_ack.sh <incident_id>`

## Coming next

- **`GET /incidents`, `GET /incidents/{id}`**: read-only, for the dashboard. Shows state, attempts, alerts sent and the timeline.
- **`WS /ws/box`**: the Box stays connected; every "BOX SAYS" line in the logs today becomes a message sent over this.
