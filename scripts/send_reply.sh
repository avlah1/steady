#!/usr/bin/env bash
# Send a text reply (as if the person answered the Box) to a running backend.
# Usage: scripts/send_reply.sh <incident_id> "I'm fine"
set -euo pipefail

INCIDENT_ID="${1:?usage: scripts/send_reply.sh <incident_id> <text>}"
TEXT="${2-}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-$(grep -E '^API_KEY=' .env | cut -d= -f2-)}"

# python3 builds the JSON so quotes/apostrophes in the text are escaped correctly.
python3 -c 'import json,sys; print(json.dumps({"incident_id": sys.argv[1], "text": sys.argv[2]}))' "$INCIDENT_ID" "$TEXT" |
  curl -sS -X POST "$BASE_URL/api/v1/box/reply" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $API_KEY" \
    -d @-
echo
