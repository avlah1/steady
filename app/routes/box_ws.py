import logging
import secrets

from fastapi import APIRouter, Depends, WebSocket, status

from app.box import WebSocketBox
from app.config import Settings, get_settings
from app.deps import get_box

log = logging.getLogger("box_ws")

router = APIRouter()


@router.websocket("/ws/box")
async def box_socket(
    ws: WebSocket,
    box: WebSocketBox = Depends(get_box),
    settings: Settings = Depends(get_settings),
) -> None:
    # require_api_key can't be reused here (it reads a normal HTTP request), so check by hand.
    # The query parameter is a fallback for Box firmware that can't set headers.
    key = ws.headers.get("x-api-key") or ws.query_params.get("api_key")
    if key is None or not secrets.compare_digest(key, settings.api_key):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    box.connect(ws)
    try:
        # Messages only flow backend -> Box; replies come in over POST /api/v1/box/reply.
        # Reading here just keeps the connection open and notices when the Box goes away.
        # receive() rather than receive_text(), so a stray binary frame doesn't drop the Box.
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    finally:
        box.disconnect(ws)
