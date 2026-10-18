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
| Vision worker (MediaPipe fall detection) | ✅ Working with the ESP32-CAM, end to end |
| ESP32-CAM: streams video over Wi-Fi | ✅ Working (`firmware/camera/`) |
| **Whole system, camera to "Help is coming"** | ✅ **Confirmed on real hardware** |
| Camera pan servo (Arduino) | 🟡 Responds to `curl`; not wired into the vision worker; sketch not in the repo yet ([docs/servo-contract.md](docs/servo-contract.md)) |
| Box touchscreen dashboard | 🟡 Separate sketch; merge planned, on hold |
| Caregiver web dashboard | ⏳ Not started (Telegram covers acknowledging) |
| Emergency services | Simulated: a log line only. Software can't reliably call 911. |

## Running everything

Everything runs on **one laptop**, and every device joins **one phone hotspot**. The venue Wi-Fi (UCLA_WEB) blocks devices from reaching each other.

```
iPhone hotspot ("iPhone", 2.4 GHz)
├── laptop   172.20.10.2   backend :8000 + MongoDB + vision worker
├── S3-Box   172.20.10.7   connects to the backend by itself
└── camera   172.20.10.8   video at http://172.20.10.8:81/stream
```

The addresses are what this hotspot gave each device. Another phone, or a long gap, can change them: see [If something's off](#if-somethings-off).

### One-time setup

**You need:** Python 3.11+ with [uv](https://docs.astral.sh/uv/), and [PlatformIO](https://platformio.org/) (`pio`) to flash the Box and camera.

```bash
uv sync                          # install dependencies
cp .env.example .env             # fill in API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN
```

Check these two lines in `.env` (`.env.example` already has them):

```
REPLY_TIMEOUT_S=15   # with hardware, asking + recording + uploading takes ~9 s; the default 10 is too tight
ACK_TIMEOUT_S=60     # time for a caregiver to tap "I'm on it" before the next one is alerted
```

- Missing `ANTHROPIC_API_KEY` → answers are labelled by a simple keyword check. Missing `TELEGRAM_BOT_TOKEN` → alerts are only logged.
- **Link a caregiver's Telegram (once per person):** with the backend running, `scripts/telegram_link.sh contact-1` prints a one-time link. They open it and press Start.
- **Flash the Box and camera** only when their Wi-Fi or the laptop's IP changes: see [Flashing the devices](#flashing-the-devices).

### Every session

**1. Network.** Turn on the iPhone hotspot with **Maximize Compatibility** on (the devices only do 2.4 GHz), and keep that screen open: iPhones turn the hotspot off when idle. Connect the laptop to **iPhone**, then check:

```bash
hostname -I     # should print 172.20.10.2
```

**2. Terminal 1: database and backend.**

```bash
cd ~/dev/projects/steady
scripts/mongo.sh start
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` lets the Box and camera reach it. Without it, only the laptop can.

**3. Power on the Box and the camera.** Any USB power works; each joins the hotspot by itself. Nothing plays when the Box starts.

- Box: within ~10 s, Terminal 1 shows `Box connected (1 connected)`.
- Camera: `curl -s -o /dev/null -w "%{http_code}\n" http://172.20.10.8/capture` prints `200`.

**4. Terminal 2: vision worker.** From now on, **a fall in front of the camera makes the Box speak** (loudly: volume 90).

```bash
cd ~/dev/projects/steady
uv run python -m vision.live --source http://172.20.10.8:81/stream --camera-id living_room
```

A window shows the video with the detected skeleton. Aim the camera at chest height, 2–4 m away, so a person fits head to feet. To aim without triggering anything: `uv run python -m vision.view_pose http://172.20.10.8:81/stream` (shows the skeleton, never reports falls).

**5. Test.** Fall onto something soft and lie still for ~3 s. The Box asks "Are you okay?"; answer out loud. "Help, I fell" (or "yeah, but my shoulder hurts") → Telegram alert → tap "I'm on it" → the Box says "Help is coming." "I'm okay" → "Okay, glad you're alright."

**Stop:** Ctrl+C in each terminal, then `scripts/mongo.sh stop`.

### Without the camera, or without any hardware

```bash
scripts/send_fall.sh                                # a fake fall; prints the incident_id (the Box asks, if connected)
scripts/send_reply.sh <incident_id> "help, I fell"  # answer as text instead of speaking
scripts/send_ack.sh <incident_id>                   # acknowledge without Telegram
uv run python scripts/fake_box.py                   # no Box: prints what it would say
uv run pytest                                       # the tests
```

### Flashing the devices

Needed only when the hotspot or the laptop's IP changes. Wi-Fi details live in each device's `secrets.h` (gitignored; copy `secrets.h.example`). Linux only lets you use the USB port after `sudo chmod a+rw <port>`, which resets every time the device is plugged in, so run it in a normal terminal (it asks for your password).

| Device | Settings | Plug in, then |
|---|---|---|
| S3-Box (`/dev/ttyACM0`) | `firmware/box-audio-test/src/secrets.h`: `WIFI_SSID`, `WIFI_PASSWORD`, `BACKEND_HOST` (the laptop's `hostname -I`), `API_KEY` (from `.env`) | `cd firmware/box-audio-test && pio run -t upload` |
| ESP32-CAM (`/dev/ttyUSB0`, via its USB adapter) | `firmware/camera/src/secrets.h`: `WIFI_SSID`, `WIFI_PASSWORD` | `cd firmware/camera && pio run -t upload` |

Read a device's messages (e.g. the camera's new IP, printed as `Camera Ready! Stream: http://...`; press its reset button to see it again):

```bash
pio device monitor -p /dev/ttyUSB0 -b 115200 --rts 0 --dtr 0     # camera; use /dev/ttyACM0 for the Box
```

`--rts 0 --dtr 0` matters: without it the monitor holds the board in reset. More on the Box (pins, volume, test commands): [docs/box-contract.md](docs/box-contract.md).

### If something's off

| Problem | Fix |
|---|---|
| `hostname -I` isn't `172.20.10.2` | Put the new IP in the Box's `secrets.h` (`BACKEND_HOST`) and reflash the Box. Use `--base-url` on the vision worker only if it runs on another machine. |
| Camera `curl` fails | Its IP changed, or it's off the hotspot. Read its messages (above) for the new IP and use that in step 4. |
| No `Box connected` | The hotspot went to sleep: open the hotspot screen and power-cycle the Box. Still nothing: check its messages for `Wi-Fi` / `Backend` lines. |
| `409 Conflict` in the backend log | Two backends use the Telegram bot. Stop the other one (`pkill -f "uvicorn app.main:app"`). |
| `address already in use` | A backend is already running: same command. |
| The Box asks again although you answered | Speak right after the question (it records 5 s), close to the Box. Check `REPLY_TIMEOUT_S=15`. |
| A fall isn't detected | The whole body must be in view. Lie still for ~3 s. Watch the skeleton in the vision window. |

## Repository

| Path | What |
|---|---|
| `app/` | FastAPI backend: state machine (`incidents.py`), endpoints (`routes/`), Telegram, Whisper, Claude |
| `vision/` | Vision worker: camera frames → MediaPipe pose → fall rules |
| `firmware/box-audio-test/` | S3-Box firmware (PlatformIO) |
| `firmware/camera/` | ESP32-CAM firmware: joins the hotspot, streams video |
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
