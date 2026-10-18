#!/usr/bin/env bash
# Print a one-time Telegram link for a caregiver (stands in for signing up on the dashboard).
# Send it to them; when they open it and press Start, they're connected for alerts.
# Usage: scripts/telegram_link.sh <contact_id>     e.g. contact-1
set -euo pipefail

CONTACT_ID="${1:?usage: scripts/telegram_link.sh <contact_id>}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-$(grep -E '^API_KEY=' .env | cut -d= -f2-)}"

curl -sS -X POST "$BASE_URL/api/v1/contacts/$CONTACT_ID/telegram-link" -H "X-API-Key: $API_KEY"
echo
