#!/usr/bin/env bash
# Send a recorded reply (as if the Box recorded the person) to a running backend.
# Usage: scripts/send_audio_reply.sh <incident_id> <file.wav>   (16 kHz mono 16-bit WAV)
set -euo pipefail

INCIDENT_ID="${1:?usage: scripts/send_audio_reply.sh <incident_id> <file.wav>}"
WAV="${2:?usage: scripts/send_audio_reply.sh <incident_id> <file.wav>}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-$(grep -E '^API_KEY=' .env | cut -d= -f2-)}"

curl -sS -X POST "$BASE_URL/api/v1/box/reply/audio?incident_id=$INCIDENT_ID" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: audio/wav" \
  --data-binary @"$WAV"
echo
