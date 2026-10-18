"""Pretend to be the S3-Box: connect to WS /ws/box and print whatever the backend says.

Usage: uv run python scripts/fake_box.py   (BASE_URL / API_KEY env vars override .env)
Then post a fall with scripts/send_fall.sh and answer with scripts/send_reply.sh.
"""

import asyncio
import json
import os
from pathlib import Path

import websockets  # installed with uvicorn[standard]


def api_key() -> str:
    if key := os.environ.get("API_KEY"):
        return key
    for line in Path(".env").read_text().splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1]
    raise SystemExit("No API_KEY in environment or .env")


async def main() -> None:
    url = os.environ.get("BASE_URL", "http://localhost:8000").replace("http", "ws", 1) + "/ws/box"
    async with websockets.connect(url, additional_headers={"X-API-Key": api_key()}) as ws:
        print(f"Fake Box connected to {url}. Waiting for prompts (Ctrl+C to quit).")
        async for raw in ws:
            msg = json.loads(raw)
            print(f'BOX SPEAKS: "{msg["text"]}"')
            if msg["text"].endswith("?"):  # only questions expect an answer
                print(f'  reply with: scripts/send_reply.sh {msg["incident_id"]} "I\'m fine"')


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
