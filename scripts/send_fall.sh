#!/usr/bin/env bash
# Send a fake fall detection to a running backend.
# Usage: scripts/send_fall.sh [camera_id] [event_id]
#   Reuse an event_id to simulate a worker retry.
set -euo pipefail

CAMERA_ID="${1:-living_room}"
EVENT_ID="${2:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-$(grep -E '^API_KEY=' .env | cut -d= -f2-)}"

curl -sS -X POST "$BASE_URL/api/v1/detections" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d @- <<EOF
{
  "event_id": "$EVENT_ID",
  "type": "fall_suspected",
  "confidence": 0.87,
  "detected_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "camera_id": "$CAMERA_ID",
  "details": {"hip_drop": 0.42, "torso_angle_deg": 12, "seconds_down": 5.2}
}
EOF
echo
