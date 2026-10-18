# Backend endpoints

What each endpoint is for, who calls it, and what happens. For how incidents move between states, see [state-machine.md](state-machine.md). For the vision worker's exact payload, see [vision-contract.md](vision-contract.md).

All endpoints except `/health` need the header `X-API-Key: <API_KEY from .env>`. Wrong or missing key → `401`.

**Calling from a browser (the dashboard):** the page's origin must be listed in `CORS_ORIGINS` in `.env` (comma-separated; defaults to `http://localhost:5173` and `http://localhost:3000`). Otherwise the browser blocks the request before it reaches the backend; you'll see a CORS error in the browser console. Restart the backend after changing it.

## At a glance

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET /health` | Anyone | Is the server up? | ✅ |
| `POST /api/v1/detections` | Vision worker | Report a fall | ✅ |
| `POST /api/v1/box/reply` | Box | Send the person's answer | ✅ (text only) |
| `POST /api/v1/incidents/{id}/ack` | Caregiver dashboard | "I'm on it" | ✅ |
| `GET /api/v1/incidents` | Caregiver dashboard | List incidents | ✅ |
| `GET /api/v1/incidents/{id}` | Caregiver dashboard | One incident with its timeline | ✅ |
| `WS /ws/box` | Box | Backend tells the Box what to say | Todo |

## Who calls what

```
Vision worker:            POST /detections
Person (S3-Box):          WS /ws/box (hears what to say) ──▶ POST /box/reply (sends the answer)
Caregivers (dashboard,    GET /incidents, GET /incidents/{id}, POST /incidents/{id}/ack
 later the mobile app):
```

- **The dashboard polls.** The backend never pushes to it. Call `GET /incidents` every 2–3 s and redraw. When a caregiver opens one incident, poll `GET /incidents/{id}` instead (the list already has full incidents, so the detail view can start from that). Pushing updates can come later if the delay matters.
- **The mobile app uses the same endpoints.** Nothing extra is needed on the backend. CORS only applies to browsers, so a native app won't hit it. Push notifications would be one more `Notifier` next to SMS.
- **The S3-Box never reads incidents.** It's the person's device: it only speaks and sends back answers.

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

## `GET /api/v1/incidents` and `GET /api/v1/incidents/{id}`

**What the dashboard shows.** Read-only; never changes anything.

- `GET /incidents` → `200 {"incidents": [...]}`, newest first. (An object, not a bare list, so paging can be added later.)
- `GET /incidents/{id}` → `200` with one incident, or `404`.

An incident looks like this:

```json
{
  "id": "a2103f98-...",
  "camera_id": "living_room",
  "state": "ESCALATED",
  "is_active": true,
  "attempt": 3,
  "contact_position": 1,
  "emergency_alerted": false,
  "event_ids": ["15b36892-..."],
  "created_at": "2026-10-17T14:31:05.171636Z",
  "timeline": [
    { "at": "2026-10-17T14:31:05.171741Z", "from_state": "OPEN", "to_state": "ASKING", "trigger": "new_fall" },
    { "at": "2026-10-17T14:31:35.402113Z", "from_state": "ASKING", "to_state": "ESCALATED", "trigger": "reply_timeout" }
  ]
}
```

- `is_active`: still needs attention (`OPEN`, `ASKING` or `ESCALATED`). Use it to split "live" from "history".
- `attempt`: which "Are you okay?" the Box is on (1–3).
- `contact_position`: how far down the contact list the backend has gone. It keeps counting when the list loops back to the start, and moves past emergency services when it's skipped, so it's not a count of alerts sent.
- `emergency_alerted`: emergency services has been (simulated) alerted for this incident.
- Times are UTC.

The dashboard polls these for now (every couple of seconds is fine). Storage is in memory, so incidents disappear when the backend restarts; Supabase fixes that later.

Try it: `curl -s -H "X-API-Key: $API_KEY" localhost:8000/api/v1/incidents`

## Coming next

- **`WS /ws/box`**: the Box stays connected; every "BOX SAYS" line in the logs today becomes a message sent over this.
