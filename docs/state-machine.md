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
                           └─no ack (30 s) ──▶ alert next caregiver (loops back to first)
```

A caregiver can also acknowledge while the person is still being asked.

## Safety rules

- **When in doubt, escalate.** No answer, garbled answer or an API failure all count as "unclear", never "ok".
- **The AI only labels.** The LLM turns the reply into `ok`, `needs_help` or `unclear`. The state machine decides what that means.
- **No take-backs.** Once caregivers are alerted, a late "I'm fine" doesn't cancel it.
- **Old timers are ignored.** If a timer goes off after the incident has moved on, it does nothing.
- **One thing at a time.** If a reply and a timeout arrive together, they're handled one after the other, never both at once.

## How the endpoints fit in

| Endpoint | Who calls it | What it reports to `handle()` | Status |
|---|---|---|---|
| `POST /api/v1/detections` | Vision worker | `new_fall` (or adds to the open incident) | ✅ Done |
| `POST /api/v1/box/reply` | Box (person's answer) | `ok` / `needs_help` / `unclear`, via speech-to-text + LLM | ✅ Done (text + keyword check for now) |
| `POST /api/v1/incidents/{id}/ack` | Caregiver dashboard | `ack` | Todo |
| `GET /api/v1/incidents[/{id}]` | Caregiver dashboard | Nothing; read-only | Todo |
| `WS /ws/box` | Box stays connected | Nothing; the backend uses it to tell the Box what to say | Todo |

Timers report `reply_timeout` and `ack_timeout` on their own. Endpoint details: [api.md](api.md).

## Where things live

- State machine: `app/incidents.py`
- Detections endpoint: `app/routes/detections.py`
- Reply endpoint: `app/routes/box.py`
- Reply labelling (keywords now, LLM later): `app/classifier.py`
- Box (fake, logs only): `app/box.py`
- Caregiver alerts (fake, logs only): `app/notifier.py`
- Storage (in memory, Supabase later): `app/repository.py`
