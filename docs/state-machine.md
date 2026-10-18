# How the incident flow works

An **incident** is one possible emergency: a fall was seen, and the system is checking on the person.

## The one rule

Nothing changes an incident directly. Everything (endpoints, timers, the LLM) reports **what happened** to one function, `handle()`, and `handle()` decides what to do next.

```
endpoints ─┐
timers ────┼──▶ handle(incident, what_happened) ──▶ new state + actions
LLM label ─┘
```

## States

| State | Meaning |
|---|---|
| `OPEN` | Just created. Moves to `ASKING` immediately. |
| `ASKING` | The Box is asking "Are you okay?" (up to 3 tries). |
| `RESOLVED_OK` | Person said they're fine. **Done.** |
| `ESCALATED` | Caregivers are being alerted, one at a time. |
| `ACKNOWLEDGED` | A caregiver responded. **Done.** |

## The flow

```
fall detected
     │
     ▼
  ASKING ──"ok"──────────────────────────▶ RESOLVED_OK
     │
     ├─"unclear" or no answer (10 s) ──▶ ask again (max 3 tries)
     │
     ├─"needs help" ───────┐
     └─3rd try fails ──────┤
                           ▼
                      ESCALATED ──caregiver acks──▶ ACKNOWLEDGED
                           │
                           └─no ack (30 s) ──▶ alert next caregiver (loops back to first;
                                emergency services: no wait, once only)
```

A caregiver can also acknowledge while the person is still being asked.

## Emergency services

Emergency services is just another contact (`kind: "emergency"`), so the user chooses where it sits in the alert order. For now the alert is **simulated** (a log line: `ALERT -> EMERGENCY SERVICES (simulated)`). Software can't reliably call 911 directly; a real version would go through a staffed monitoring service that checks and dispatches.

It's treated differently from people in two ways:

- **No waiting.** 911 can't ack from the dashboard, so after alerting them the next person is alerted straight away.
- **Once per incident.** When the list loops back to the start, emergency services is skipped.

The incident stays `ESCALATED` until a *person* acks. Same rules as everyone else for when it's reached: "needs help", or 3 unanswered tries, then its place in the order.

## Safety rules

- **When in doubt, escalate.** No answer, garbled answer or an API failure all count as "unclear", never "ok".
- **The AI only labels.** The LLM turns the reply into `ok`, `needs_help` or `unclear`. The state machine decides what that means.
- **No take-backs.** Once caregivers are alerted, a late "I'm fine" doesn't cancel it.
- **Old timers are ignored.** Every state change cancels the pending timer. As a backup, a timer that goes off after the incident has moved on does nothing.
- **Restarts pick up where they left off.** Incidents are reloaded from MongoDB, and each active one gets `restarted`: a fresh reply or ack timer for where it already was (nothing is repeated to the Box or caregivers). An incident that never got its first question is asked now. A fresh timer runs its full length, so the next step can be up to one timeout late.
- **One thing at a time.** If a reply and a timeout arrive together, they're handled one after the other, never both at once.

## How the endpoints fit in

| Endpoint | Who calls it | What it reports to `handle()` | Status |
|---|---|---|---|
| `POST /api/v1/detections` | Vision worker | `new_fall` (or adds to the open incident) | ✅ Done |
| `POST /api/v1/box/reply/audio` | Box (person's answer) | `ok` / `needs_help` / `unclear`, via speech-to-text + LLM | ✅ Done |
| `POST /api/v1/box/reply` | Testing (answer as text) | same, skipping speech-to-text | ✅ Done |
| `POST /api/v1/incidents/{id}/ack` | Caregiver dashboard | `ack` | ✅ Done |
| `GET /api/v1/incidents[/{id}]` | Caregiver dashboard | Nothing; read-only | ✅ Done |
| `WS /ws/box` | Box stays connected | Nothing; the backend uses it to tell the Box what to say | ✅ Done |

Timers report `reply_timeout` and `ack_timeout` on their own. Endpoint details: [api.md](api.md).

## Where things live

- State machine: `app/incidents.py`
- Detections endpoint: `app/routes/detections.py`
- Reply endpoint: `app/routes/box.py`
- Ack and dashboard read endpoints: `app/routes/incidents.py` (Telegram's "I'm on it" button: `app/telegram.py`)
- Speech-to-text (local Whisper): `app/speech.py`
- Reply labelling (Claude Haiku, keyword fallback): `app/classifier.py`
- Box (WebSocket; logging fake for tests): `app/box.py`, socket endpoint in `app/routes/box_ws.py`
- Caregiver alerts (Telegram, or logs only without a bot token): `app/notifier.py`, linking in `app/telegram.py`
- Storage (in memory, also written to MongoDB; see [storage.md](storage.md)): `app/repository.py`
