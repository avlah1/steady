# Backend endpoints

What each endpoint is for, who calls it, and what happens. For how incidents move between states, see [state-machine.md](state-machine.md). For how data is stored and what survives a failure, see [storage.md](storage.md). For what each device must do: vision worker → [vision-contract.md](vision-contract.md), S3-Box → [box-contract.md](box-contract.md), servo → [servo-contract.md](servo-contract.md).

All endpoints except `/health` need the header `X-API-Key: <API_KEY from .env>`. Wrong or missing key → `401`.

**Calling from a browser (the dashboard):** the page's origin must be listed in `CORS_ORIGINS` in `.env` (comma-separated; defaults to `http://localhost:5173` and `http://localhost:3000`). Otherwise the browser blocks the request before it reaches the backend; you'll see a CORS error in the browser console. Restart the backend after changing it.

## At a glance

| Endpoint | Caller | Purpose | Status |
|---|---|---|---|
| `GET /health` | Anyone | Is the server up? | ✅ |
| `POST /api/v1/detections` | Vision worker | Report a fall | ✅ |
| `POST /api/v1/box/reply/audio` | Box | Send the person's recorded answer | ✅ |
| `POST /api/v1/box/reply` | Testing | Send the answer as text (skips speech-to-text) | ✅ |
| `POST /api/v1/incidents/{id}/ack` | Caregiver dashboard | "I'm on it" | ✅ |
| `GET /api/v1/incidents` | Caregiver dashboard | List incidents | ✅ |
| `GET /api/v1/incidents/{id}` | Caregiver dashboard | One incident with its timeline | ✅ |
| `WS /ws/box` | Box | Backend tells the Box what to say | ✅ |
| `POST /api/v1/contacts/{id}/telegram-link` | Setup script (later the dashboard) | Link a caregiver's Telegram for alerts | ✅ |

## Running it

Start the database first: `scripts/mongo.sh start` (a local MongoDB; the venue Wi-Fi blocks Atlas). Without `MONGODB_URI` in `.env`, everything is kept in memory and lost on restart.

`uv run uvicorn app.main:app` only listens on the laptop itself. For the ESP32 boards (or a teammate's laptop) on the Wi-Fi, run:

```
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

and point the devices at the laptop's IP on the Wi-Fi (`hostname -I` on Linux, `ipconfig getifaddr en0` on macOS).

## Who calls what

```
Vision worker:            POST /detections
Person (S3-Box):          WS /ws/box (hears what to say) ──▶ POST /box/reply (sends the answer)
Caregivers (dashboard,    GET /incidents, GET /incidents/{id}, POST /incidents/{id}/ack
 later the mobile app):
Caregivers (Telegram):    "I'm on it" button under an alert (no endpoint: arrives by polling)
Setup (one-time):         POST /contacts/{id}/telegram-link
```

- **The dashboard polls.** The backend never pushes to it. Call `GET /incidents` every 2–3 s and redraw. When a caregiver opens one incident, poll `GET /incidents/{id}` instead (the list already has full incidents, so the detail view can start from that). Pushing updates can come later if the delay matters.
- **The mobile app uses the same endpoints.** Nothing extra is needed on the backend. CORS only applies to browsers, so a native app won't hit it. Push notifications would be one more `Notifier` next to Telegram.
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

Labelling uses Claude Haiku when `ANTHROPIC_API_KEY` is set in `.env`, and a simple keyword check otherwise (the startup log says which). Haiku sees the question and the reply, and can only answer with one of the three labels. It leans cautious: any sign of pain, confusion or distress is `needs_help`. Empty text, errors, refusals and timeouts (about 4 s) always count as `unclear`. A reply that arrives too late (incident already escalated or closed) is ignored.

Check Haiku against sample replies: `uv run python -m scripts.check_classifier` (real API calls, a fraction of a cent).

The real Box uses the audio version below; this text version is for testing and scripts.

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

The dashboard polls these for now (every couple of seconds is fine). Incidents are stored in MongoDB, so they survive a backend restart.

Try it: `curl -s -H "X-API-Key: $API_KEY" localhost:8000/api/v1/incidents`

## `WS /ws/box`

**The Box stays connected; the backend tells it what to say.** A WebSocket is a connection that stays open, so the backend can send a message the moment something happens instead of waiting to be asked.

Connect to `ws://<laptop-ip>:8000/ws/box` with the header `X-API-Key: <key>`. If the firmware can't set headers, use `?api_key=<key>` instead. Wrong or missing key → the connection is closed (code `1008`).

The backend sends messages like this:

```json
{ "type": "say", "incident_id": "abc", "text": "Are you okay?" }
```

The Box should:
1. Speak `text`.
2. If it was a question, record the answer and send it to `POST /api/v1/box/reply` with the **same `incident_id`**. That's how the backend knows which incident the answer belongs to.
3. Reconnect if the connection drops (wait a couple of seconds and retry).

Messages only go backend → Box. Anything the Box sends over the socket is ignored; answers go through `POST /box/reply`.

If no Box is connected, the backend logs a warning and carries on: the person can't answer, so the reply timer runs out and the incident escalates as usual. A missing Box never hides a fall.

Try it without hardware: `uv run python scripts/fake_box.py` prints everything the Box would say.

Full firmware requirements (phrases, timing, checklist): [box-contract.md](box-contract.md).

## `POST /api/v1/box/reply/audio`

**The Box recorded the person's answer.** The body is the raw WAV file (not a form upload); the incident goes in the URL:

```
POST /api/v1/box/reply/audio?incident_id=abc
Content-Type: audio/wav
<WAV bytes: 16 kHz, mono, 16-bit>
```

What happens:
1. Unknown incident → `404`. Bigger than 1 MB (~30 s) → `413`. Not 16 kHz mono 16-bit WAV → `422` (the message says what was wrong).
2. Returns `202 {"incident_id": "...", "accepted": true}` right away.
3. In the background, Whisper turns the audio into text (on the laptop, ~1 s), then it's exactly the text path above: label, then the state machine.

Speech-to-text failing or taking over 4 s counts as `unclear`. Model: `STT_MODEL` in `.env` (`base.en` default; `small.en` is more accurate but ~3× slower). It loads when the backend starts; the first start downloads it, so do that before the demo. `STT_MODEL=` (empty) turns audio replies off → `503`.

Try it: `scripts/send_audio_reply.sh <incident_id> reply.wav`

## `POST /api/v1/contacts/{id}/telegram-link`

**Connect a caregiver's Telegram, so alerts reach their phone.** No body. Returns `200 {"contact_id": "contact-1", "link": "https://t.me/<bot>?start=<code>"}`.

Why a link: a Telegram bot can only message someone who has messaged it first, and only then does Telegram tell us their chat ID. The caregiver opens the link and presses **Start**. The backend sees the message (it polls Telegram in the background) and saves their chat ID on the contact. The bot replies "You're connected".

- The code is **random and one-time**, not the contact ID. A guessable link would let a stranger connect their own Telegram and receive the family's alerts. A new link replaces any unused old one.
- Unknown contact → `404`. Emergency services → `400`. No `TELEGRAM_BOT_TOKEN`, or Telegram unreachable → `503`.
- For now `scripts/telegram_link.sh <contact_id>` calls this and prints the link. Later the dashboard will, when a caregiver signs up.

When an incident escalates, a linked caregiver gets a message like:

```
⚠️ Possible fall (living_room) at 14:31.
They said they need help.
Please check on them, and tap "I'm on it" so they know help is coming.
Incident 3f2a9c1e
                [ I'm on it ]
```

**Tapping "I'm on it"** acknowledges the incident, exactly like `POST /incidents/{id}/ack`: alerts stop and the Box says "Help is coming". The button is replaced by "✓ Andrew is on it." Only a linked caregiver's tap counts (an alert forwarded to someone else can't ack). A tap after someone else acked, or after the person said they're fine, changes nothing and says so.

A contact who hasn't linked Telegram only gets the log line. A failed send is logged and not retried: the ack timer is already running, so the next contact is still alerted on time.

Only one backend can poll a bot at a time. If two run with the same `TELEGRAM_BOT_TOKEN`, the log shows `409 Conflict` every few seconds.

## Coming next

