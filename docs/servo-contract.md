# Servo (Arduino) contract

How the laptop moves the camera's pan servo. The servo is driven by an Arduino on the same Wi-Fi as the laptop. MediaPipe (the vision worker) decides the angle; the backend isn't involved.

```
vision worker ──GET /pan?angle=60──▶ Arduino ──▶ servo
```

Fill in the blanks once the hardware is final:

| | |
|---|---|
| Board | `[exact board, e.g. Arduino Uno R4 WiFi]` |
| Servo signal pin | `[pin]` |
| Wi-Fi | `[network name]` (same network as the laptop) |
| Arduino IP | Printed to Serial on boot. Put it in the vision worker's config. |

## What the Arduino must do

- Join the Wi-Fi, print its IP to Serial on boot, and reconnect if Wi-Fi drops.
- On boot, center the servo at **90°**.
- Run an HTTP server on **port 80**:

| Request | Does | Reply |
|---|---|---|
| `GET /pan?angle=N` (N = 0–180) | Move the servo to N. Out-of-range values are clamped. | `200 {"angle": N}` |
| `GET /status` | Nothing | `200 {"angle": <current>}` |
| Missing or bad `angle` | Nothing | `400` |

- **Reply immediately.** Don't wait for the servo to finish moving, and never block the loop.

Test from the laptop:

```bash
curl "http://<arduino-ip>/pan?angle=45"     # servo moves, prints {"angle":45}
curl "http://<arduino-ip>/status"
```

A successful `ping` only proves the network works. This `curl` proves the Arduino accepts commands.

## What the vision worker must do

```python
urllib.request.urlopen(f"http://{ARDUINO_IP}/pan?angle={angle}", timeout=1)
```

- **Send from a background thread** (like `vision/alerts.py`), so a slow Arduino never stalls the video loop.
- **Timeout 1 s; on failure, skip it.** Don't retry: the next frame sends a fresh angle anyway.
- **Only send when the angle changes by a few degrees, at most a few times a second.** Otherwise the servo jitters.
- **Don't pan while a fall is in progress** (detector state `descent` or `down`). Moving the camera mid-fall can lose the person and disrupt detection.

## Tracking (to be designed)

| Situation | Plan |
|---|---|
| Person in frame, near the centre | Don't move |
| Person near the edge | Pan toward them |
| Person out of frame | Open question: e.g. a slow sweep to search, but never during a fall |
