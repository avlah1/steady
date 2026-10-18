#!/usr/bin/env bash
# Run a local MongoDB for the backend (the venue Wi-Fi blocks Atlas's port 27017).
# Usage: scripts/mongo.sh start | stop | status
# Data lives in .data/mongo (gitignored) and survives restarts. Only this laptop can connect.
set -euo pipefail

MONGOD="${MONGOD:-$HOME/.local/opt/mongodb/bin/mongod}"
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/.data/mongo"
LOG="$DATA_DIR/mongod.log"

case "${1:-}" in
  start)
    # A second copy can't start (the port is taken) and only says "child process failed".
    if pgrep -f "mongod --dbpath $DATA_DIR" >/dev/null; then
      echo "MongoDB is already running."
      exit 0
    fi
    mkdir -p "$DATA_DIR"
    # 127.0.0.1 only: nothing else on the Wi-Fi can reach the database.
    "$MONGOD" --dbpath "$DATA_DIR" --bind_ip 127.0.0.1 --port 27017 --fork --logpath "$LOG"
    echo "MongoDB running. In .env: MONGODB_URI=mongodb://localhost:27017"
    ;;
  stop)
    "$MONGOD" --dbpath "$DATA_DIR" --shutdown
    ;;
  status)
    if pgrep -f "mongod --dbpath $DATA_DIR" >/dev/null; then echo "running"; else echo "stopped"; fi
    ;;
  *)
    echo "usage: scripts/mongo.sh start | stop | status" >&2
    exit 1
    ;;
esac
