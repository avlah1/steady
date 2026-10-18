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
| **The backend** crashes or restarts | Memory is wiped; on restart everything is reloaded from MongoDB. Dashboard history is back, worker retries are still recognised. | Writes still queued at the moment of the crash (usually milliseconds' worth), and **timers** (see below). |
| **MongoDB** goes down | Nothing visible: falls are still asked about, escalated and alerted from memory. Write failures are logged. | Nothing, unless the backend *also* restarts before MongoDB is back. Then changes made while it was down are gone. |

Each covers the other. Data is only lost if both fail together.

**Known gap: timers.** They exist only in memory. An incident that was asking or escalated when the backend restarted has no timer afterwards, so it won't move on by itself (a warning is logged at startup). A caregiver can still ack it from the dashboard.

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
| `contacts` | person to alert; `position` sets the order | contact id |

To change who gets alerted, edit `contacts` in MongoDB and restart the backend.

Code: `MongoRepository` in `app/repository.py`.
