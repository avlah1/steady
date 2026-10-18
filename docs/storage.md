# How storage works

Incidents, events and contacts live in **memory** while the backend runs, and are also copied to **MongoDB**.

## Memory is the live copy, MongoDB is the record

```
handle() ──changes──▶ memory (instant) ──queued──▶ background writer ──▶ MongoDB
```

- The state machine, endpoints and dashboard reads all use **memory**. Nothing ever waits on the database.
- Every save is also **queued**. One background writer sends them to MongoDB in order, as whole documents.
- A failed write is **logged and skipped**. The next save of the same incident rewrites the whole document, which repairs it.
- On **startup**, everything is loaded back from MongoDB into memory. First run on an empty database: the default contacts are copied in.

Like writing in a notebook while an assistant copies each page into a filing cabinet: you never wait for the copy, and a dropped page doesn't change your notebook.

## When something fails

| What fails | What happens | What's lost |
|---|---|---|
| **The backend** crashes or restarts | Memory is wiped; on restart everything is reloaded from MongoDB. Dashboard history is back, worker retries are still recognised. | Writes still queued at the moment of the crash (usually milliseconds' worth). Timers are restarted (see below). |
| **MongoDB** goes down | Nothing visible: falls are still asked about, escalated and alerted from memory. Write failures are logged. | Nothing, unless the backend *also* restarts before MongoDB is back. Then changes made while it was down are gone. |

Each covers the other. Data is only lost if both fail together.

**Timers after a restart.** Timers exist only in memory, so at startup every incident that's still active gets a fresh one for where it was (`IncidentFlow.resume_after_restart`): asking → a new reply timer, escalated → a new ack timer. Nothing is repeated to the Box or caregivers. A fresh timer runs its full length, so the next step can be up to one timeout (10–30 s) later than it would have been.

## Running it

```bash
scripts/mongo.sh start     # local MongoDB on this laptop; also: stop, status
uv run uvicorn app.main:app
```

- Local, not Atlas: the venue Wi-Fi blocks port 27017, which Atlas needs. Switching to Atlas later is just a different `MONGODB_URI` in `.env`.
- Data is in `.data/mongo` (gitignored). Only this laptop can connect.
- No `MONGODB_URI` in `.env` → memory only, lost on restart. Tests run this way.

## Collections (database `steady`)

| Collection | One document per | `_id` |
|---|---|---|
| `incidents` | incident, including its timeline | incident id |
| `events` | detection from the vision worker, plus the response it got | `event_id` |
| `contacts` | person to alert; `position` sets the order. Also their linked Telegram chat. | contact id |

To change who gets alerted, stop the backend, edit `contacts` in MongoDB, then start it. (Linking Telegram rewrites that contact's document, so an edit made while the backend runs can be overwritten.)

Code: `MongoRepository` in `app/repository.py`.
