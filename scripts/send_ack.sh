#!/usr/bin/env bash
# Acknowledge an incident (as if a caregiver tapped "I'm on it" on the dashboard).
# Usage: scripts/send_ack.sh <incident_id>
set -euo pipefail

INCIDENT_ID="${1:?usage: scripts/send_ack.sh <incident_id>}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-$(grep -E '^API_KEY=' .env | cut -d= -f2-)}"

curl -sS -X POST "$BASE_URL/api/v1/incidents/$INCIDENT_ID/ack" -H "X-API-Key: $API_KEY"
echo
