# Steady

Fall detection for older people who live alone. A camera watches for a fall. A voice device asks "Are you okay?", an LLM interprets the spoken answer, and if they need help or don't answer, their caregivers get a Telegram alert they can acknowledge with one tap.

Built in 24 hours at LA Hacks.

```
ESP32-CAM ──video──▶ vision worker ──"fall"──▶ backend ──"Are you okay?"──▶ S3-Box (speaks)
 (pan servo)         (MediaPipe pose,          (state machine,  ◀──spoken answer (WAV)──
                      fall rules)               Whisper, Claude)
                                                    │
                                                    └──alert──▶ Telegram ──"I'm on it"──▶ backend ──▶ Box: "Help is coming."
```

## How a fall is handled

1. The **vision worker** sees a fall and sends one event to the backend. Only events, never video.
2. The **backend** opens an incident, and the **Box** asks "Are you okay?" and records the answer.
3. **Whisper** (local, on the laptop) turns the answer into text. **Claude Haiku** labels it `ok`, `needs_help` or `unclear`.
4. `ok` closes the incident. `needs_help`, or no clear answer after 3 tries, alerts the first caregiver on **Telegram**.
5. The caregiver taps **"I'm on it"**, and the Box says "Help is coming." No tap within 30 s alerts the next caregiver.

**Design rules:** the AI only labels answers; a fixed state machine makes every decision. Any failure (no answer, speech-to-text or LLM down) counts as "unclear", never "ok". A false alarm is better than a missed fall. Details: [docs/state-machine.md](docs/state-machine.md).

## Status

| Part | State |
|---|---|
| Backend: incidents, state machine, MongoDB storage | ✅ Working, tested (171 tests) |
| Speech-to-text (Whisper) + reply labelling (Claude) | ✅ Working |
| Telegram alerts + "I'm on it" button | ✅ Working on a real phone |
| S3-Box: speaks prompts, records and sends answers | ✅ Working on the real Box, both "I'm okay" and "Help, I fell" |
| Vision worker (MediaPipe fall detection) | 🟡 Detector works and reports falls to the backend; not yet tested end to end with the camera |
| Camera pan servo (Arduino) | 🟡 Responds to `curl`; sketch not in the repo yet ([docs/servo-contract.md](docs/servo-contract.md)) |
| Box touchscreen dashboard | 🟡 Separate sketch; merge planned, on hold |
| Caregiver web dashboard | ⏳ Not started (Telegram covers acknowledging) |
| Emergency services | Simulated: a log line only. Software can't reliably call 911. |

## Running it

**You need:** Python 3.11+ with [uv](https://docs.astral.sh/uv/), and [PlatformIO](https://platformio.org/) to flash the Box. Everything runs on one laptop.

```bash
uv sync                          # install dependencies
cp .env.example .env             # then fill it in: API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN
scripts/mongo.sh start           # local MongoDB (the venue Wi-Fi blocks MongoDB Atlas)
REPLY_TIMEOUT_S=15 uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- `--host 0.0.0.0` lets the Box and other devices reach the backend. Without it, only the laptop can.
- `REPLY_TIMEOUT_S=15`: with real hardware, the question, recording and upload take about 9 s, so the default 10 s is too tight. Put it in `.env` to make it permanent.
- Missing `ANTHROPIC_API_KEY` → replies are labelled by a simple keyword check. Missing `TELEGRAM_BOT_TOKEN` → alerts are only logged.

**Connect a caregiver's Telegram (once each):** `scripts/telegram_link.sh contact-1` prints a one-time link. The caregiver opens it and presses Start.

### Try it without any hardware

```bash
uv run python scripts/fake_box.py                   # pretends to be the Box: prints what it would say
scripts/send_fall.sh                                # a fake fall; prints the incident_id
scripts/send_reply.sh <incident_id> "help, I fell"  # answer as text → Telegram alert
scripts/send_ack.sh <incident_id>                   # or acknowledge without Telegram
uv run pytest                                       # the tests
```

### The S3-Box

```bash
cp firmware/box-audio-test/src/secrets.h.example firmware/box-audio-test/src/secrets.h   # Wi-Fi, laptop IP, API key
cd firmware/box-audio-test && pio run -t upload
```

Nothing plays at startup. Hardware notes, pins and test commands: [docs/box-contract.md](docs/box-contract.md).

## Before the demo

- [ ] **Every device on one phone hotspot.** The venue Wi-Fi (UCLA_WEB) blocks devices from reaching each other. Hotspot on 2.4 GHz (iPhone: "Maximize Compatibility").
- [ ] Laptop IP (`hostname -I`) in the Box's `secrets.h`, then reflash. Same for the vision worker's backend and Arduino addresses.
- [ ] Backend started with `--host 0.0.0.0` and `REPLY_TIMEOUT_S=15`. MongoDB running.
- [ ] Only one backend runs with the Telegram token (two cause `409 Conflict`).
- [ ] Test run: `scripts/send_fall.sh`, answer "I'm okay" out loud, and check that the Box replies.

## Repository

| Path | What |
|---|---|
| `app/` | FastAPI backend: state machine (`incidents.py`), endpoints (`routes/`), Telegram, Whisper, Claude |
| `vision/` | Vision worker: camera frames → MediaPipe pose → fall rules |
| `firmware/box-audio-test/` | S3-Box firmware (PlatformIO) |
| `scripts/` | Test and setup helpers: fake falls and replies, Telegram links, MongoDB, phrase generation |
| `docs/` | How each part works and what each device must do |
| `tests/` | Backend tests |

## Docs

- [api.md](docs/api.md): every endpoint, who calls it, what happens
- [state-machine.md](docs/state-machine.md): incident states and every transition
- [storage.md](docs/storage.md): memory vs. MongoDB, and what survives a crash
- [vision-contract.md](docs/vision-contract.md) · [box-contract.md](docs/box-contract.md) · [servo-contract.md](docs/servo-contract.md): what each device must do

## Not built yet

Learning daily routines, vitals (heart rate and oxygen sensor), daily summaries, real SMS alerts (US carrier registration takes days), and a caregiver web dashboard.
