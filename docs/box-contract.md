# S3-Box → backend contract

What the ESP32-S3-BOX-3 firmware must do to work with the backend. The Box is the person's device: it speaks the backend's prompts and sends back what the person said.

## Hardware

**No wiring.** The S3-BOX-3 has its microphones, speaker and screen built in. It only needs USB-C power.

(The ESP32-CAM + pan servo is the device that needs wiring. It talks to the vision worker, not to the backend.)

## Settings the firmware needs

| Setting | Value |
|---|---|
| Wi-Fi name / password | The venue Wi-Fi (same network as the laptop) |
| Backend address | `<laptop-ip>:8000`. Find the IP with `hostname -I` (Linux) or `ipconfig getifaddr en0` (macOS). **It can change when the laptop reconnects**, so check before each demo. |
| API key | The same value as `API_KEY` in the backend's `.env` |

The backend must be started with `--host 0.0.0.0` or the Box can't reach it:

```
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## The loop

```
Box boots → connects to WS /ws/box, stays connected
backend   → {"type": "say", "incident_id": "abc", "text": "Are you okay?"}
Box       → plays the phrase
Box       → if it was a question: records the answer, POSTs the WAV to /api/v1/box/reply/audio?incident_id=abc
backend   → decides what happens, and sends the next phrase over the WebSocket
```

### 1. Connect and stay connected

```
ws://<laptop-ip>:8000/ws/box
X-API-Key: <key>
```

If the firmware can't set headers, use `ws://<laptop-ip>:8000/ws/box?api_key=<key>`. A wrong key closes the connection (code `1008`).

**If the connection drops, reconnect** after ~2 s, forever. While the Box is disconnected it hears nothing, and incidents escalate to caregivers because nobody answers.

The Box never needs to send anything over the WebSocket. It only listens.

### 2. Play the phrase

Every message looks like:

```json
{ "type": "say", "incident_id": "abc", "text": "Are you okay?" }
```

On-device text-to-speech isn't practical on the ESP32, but there are only five phrases, and they never change. **Record them as audio clips on the Box and pick the clip by matching `text` exactly:**

| `text` | Question? |
|---|---|
| `Are you okay?` | yes |
| `I didn't catch that. Are you okay?` | yes |
| `Okay, glad you're alright.` | no |
| `I'm contacting your caregiver now.` | no |
| `Help is coming.` | no |

Source of truth: the `*_PROMPT` constants in `app/incidents.py`. If one changes, re-record the clip. Unknown text: show it on the screen.

### 3. Record and send the answer (questions only)

After a question (`text` ends with `?`), record the answer for about 5 s and send the WAV file as the request body:

```
POST http://<laptop-ip>:8000/api/v1/box/reply/audio?incident_id=abc
X-API-Key: <key>
Content-Type: audio/wav

<WAV bytes>
```

- **Format: 16 kHz, mono, 16-bit PCM WAV** (with the normal 44-byte WAV header). Anything else gets a `422` that says what was wrong.
- **Use the `incident_id` from the message you just played.** That's how the backend knows which incident the answer belongs to.
- **Send the recording even if it's silence.** It counts as "unclear" and the Box will be asked again, sooner than waiting for the timeout.
- The backend turns it into text with Whisper, running on the laptop. The Box doesn't do any speech recognition.

| Response | Meaning | Box should |
|---|---|---|
| `202` | Got it | Nothing; the next phrase comes over the WebSocket |
| `401` | Wrong key | Fix the setting |
| `404` | Unknown incident (e.g. backend restarted) | Drop it |
| `413` | Recording over 1 MB (~30 s) | Record less |
| `422` | Not 16 kHz mono 16-bit WAV | Fix the recording settings |
| `503` | Backend has speech-to-text turned off | Tell the backend team |

## Timing

The person has `REPLY_TIMEOUT_S` (default **10 s**, set in `.env`) from the moment the backend sends a question. In that window the Box plays the phrase (~2 s), records (~5 s) and uploads, and the backend transcribes (~1 s) and labels (~1–2 s) the answer. That's tight. **With real hardware, set `REPLY_TIMEOUT_S=15`.** An answer that arrives too late is counted against the next question.

## Testing without hardware

```bash
uv run python scripts/fake_box.py                     # pretends to be the Box; prints each phrase
scripts/send_fall.sh                                  # in another terminal: start an incident
scripts/send_audio_reply.sh <incident_id> reply.wav   # answer with a recording (16 kHz mono 16-bit)
scripts/send_reply.sh <incident_id> "I'm fine"        # or answer as text, skipping speech-to-text
```

## Firmware checklist

- [ ] Joins the venue Wi-Fi; backend address and key configurable
- [ ] Connects to `/ws/box` with the key; reconnects every ~2 s when dropped
- [ ] Plays the clip matching `text`; shows unknown text on screen
- [ ] Records at 16 kHz, mono, 16-bit and wraps it as a WAV file
- [ ] After a question: records ~5 s, POSTs the WAV to `/api/v1/box/reply/audio?incident_id=...` with the same `incident_id`
- [ ] Sends the recording even when it's silence

## Our firmware

`firmware/box-audio-test/` (PlatformIO). It does everything above and is tested on the real Box: connects to `/ws/box` (key in the `X-API-Key` header), says the matching clip at volume 90, then records 5 s and POSTs it.

```bash
cp firmware/box-audio-test/src/secrets.h.example firmware/box-audio-test/src/secrets.h   # Wi-Fi, laptop IP, API key
cd firmware/box-audio-test && pio run -t upload        # Linux: sudo chmod a+rw /dev/ttyACM0 after each plug-in
```

- **Network:** the venue Wi-Fi blocks devices from reaching each other. Use a phone hotspot (2.4 GHz; iPhone: "Maximize Compatibility").
- **Phrases** are built into the firmware by `scripts/make_box_phrases.py` from the `*_PROMPT` texts. Rerun it and reflash when a prompt changes.
- **Box-3 hardware quirks:** the audio word clock is GPIO 45 (the generic "esp32s3box" board says 47), and the speaker amplifier (GPIO 46) must be switched on by hand.
- Nothing plays at startup. Serial test commands (`h` reach backend, `b` beep, `p0`–`p4` phrases, `+`/`-` volume) are listed at the top of `src/main.cpp`.
